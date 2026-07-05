"""Static checks on rendered HTML output.

Errors block writing the output file; warnings are advisory (printed to
stderr by the render pipeline, output still written).

Checks run at structural positions only — real tags and attributes found
by parsing the document (stdlib html.parser, same approach as
sanitize.py) — never on escaped text. Prose like "phase one = done" or
"never use javascript: URLs" is inert once escaped and must not block a
render; only an actual event-handler attribute, script tag, or
disallowed URL in a live attribute counts. These are last-gate tripwires
on the assembled page: the markdown sanitizer (sanitize.py) should make
them unreachable for slot content; they exist so template-authored
markup and text/data-slot values written straight into a macro's
attribute (which bypass markdown sanitization entirely) fail closed too.

The core rule is a strict ALLOWLIST over every attribute the browser
fetches at page-load time (_FETCH_ON_LOAD_ATTRS). A resource reference
there is legal in exactly one form: an inlined raster
``data:image/{png,jpeg,jpg,gif,webp}`` URI, and only on an attribute
that displays an image (_IMAGE_DATA_URI_ATTRS — what inline_images
produces).

Everything else is an error, INCLUDING a pure ``#fragment``: a
fetch-on-load attribute still makes the browser attempt a
same-document resource load for a fragment value — unlike ``<a
href="#section">``, which only navigates within the page on click and
is not in _FETCH_ON_LOAD_ATTRS at all. jimemo pages never legitimately
put a fragment in a resource-load attribute, so it fails closed here
too. Also an error: remote http(s), protocol-relative
``//host/...``, any other scheme, non-raster or non-image ``data:``
payloads, ``data:`` anywhere outside an image attribute, empty values,
and surviving bare local paths — a local path that reached the final
HTML means a sidecar-file dependency inline_images could not localize,
so the output is not self-contained. Enumerating bad forms is a losing
game; anything not explicitly allowed fails closed.

Two further gates sit in front of the attribute allowlist. A set of
tags is banned outright (_BANNED_TAGS): iframe/frame/frameset/object/
embed/applet/portal each embed a nested browsing context or plugin
content — ``<iframe srcdoc>`` executes script with no src attribute at
all, which no per-attribute check can see — and ``<form>`` turns a
static document into a data-exfiltration vector on submit; any
occurrence of these tags is an error, attributes unexamined. And CSS
is scanned: every ``<style>`` element's text and every ``style="..."``
attribute value is searched for ``url(...)`` references and
``@import`` rules. A ``url()`` target must satisfy the same allowlist
as a fetch-on-load attribute; ``@import`` always loads a stylesheet,
which has no allowed form, so any ``@import`` is an error.

Separate from the fetch allowlist, execution checks remain: ``on*``
attributes and ``javascript:``/``vbscript:`` URLs are never allowed
anywhere, ``<script src>`` is never allowed, ``<script>`` requires the
manifest to declare charts, ``<meta http-equiv="refresh">`` (a
navigation at view time) is never allowed, and ``<base href>`` (which
re-roots every relative URL) is never allowed. ``<canvas>`` is not
restricted: without script it is an inert blank box, and the script
rules already gate execution.

On a chart page the inline-script opening is itself an allowlist over
script BODIES, in one of two modes. On the real render path,
render_page passes ``allowed_scripts`` — the EXACT bodies it emitted
(the inlined vendored Chart.js text plus one charts.chart_init_js body
per declared chart) — and lint requires the page's inline scripts to
equal that multiset: a forged or altered body, an extra script, a
duplicate, or a missing one is each an error, so the page carries
exactly the scripts the renderer built and nothing else (surrounding
whitespace is normalized identically on both sides; it cannot arm or
disarm a body). Without ``allowed_scripts`` (direct lint_html callers
with no render context) the check falls back to STRUCTURAL recognition
of the two renderer-emitted byte shapes: the vendored Chart.js bundle
(byte-compared against the file at CHARTJS_BUNDLE, read lazily at lint
time) and, per declared chart, an init in exactly the shape
charts.chart_init_js builds, whose id the manifest declares, whose
config contains no raw ``<`` (the serializer u003c-escapes every one),
and whose config parses as JSON — pure data, never code. The
structural mode cannot tell a hand-forged-but-well-shaped init from
the renderer's own (same id, different config bytes); the exact mode
can, which is why the render pipeline always passes allowed_scripts.
Either way any other inline script is an error, so a shared
third-party template cannot ride a chart declaration to embed its own
JavaScript, and neither mode is a JavaScript judge: each recognizes
renderer output and rejects everything else, fail closed.

In exact mode, three further completeness checks close a gap the body
multiset alone leaves open: a page can contain exactly the renderer's
script bodies and still fail to draw a chart. (1) Every matched script
must be a bare executable ``<script>`` -- no ``type`` attribute, or
``type`` in ``{"text/javascript", "module"}``; a non-executable type
(``application/json``, ``text/template``, ...) is inert data a browser
never runs, so a byte-perfect body wrapped in one would silently draw
nothing. (2) The library body must appear, in document order, before
every init body -- ``new Chart(...)`` needs the ``Chart`` global
already defined. (3) Every manifest-declared chart id must have a
matching ``<canvas id="...">`` somewhere on the page; the id showing up
on some other element (a ``<div>``, say) does not count -- the init
script's ``getElementById`` call would resolve to nothing Chart.js can
draw on. These are not new security boundaries -- the body allowlist
above is the boundary -- they complete the guarantee that an exact-mode
pass means the page actually renders the charts it declares.
"""
import json
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

from ._paths import CHARTJS_BUNDLE
from .charts import chart_lib_inline_text, parse_chart_init_js
from .errors import ContentError
from .sanitize import (
    browser_url_form,
    is_allowed_image_data_uri,
    is_protocol_relative,
    normalize_url,
    parse_srcset,
    url_scheme,
)

MAX_OUTPUT_BYTES = 8_000_000

# Attributes whose values are URLs the browser may act on (click-time
# included); scheme-checked for javascript:/vbscript: on every tag.
_URL_ATTRS = frozenset({"href", "src", "action", "formaction", "xlink:href"})

# Tags with no legitimate use in a self-contained static document. Each
# is an embed/exec/fetch vector in some attribute or content form that
# per-attribute checks cannot fully cover (e.g. <iframe srcdoc=...>
# executes script with no src attribute at all), so ANY occurrence is an
# error — the tag itself is rejected, attributes unexamined. The legit
# resource tags (img/source/link/video/audio/track/...) stay on the
# per-attribute allowlist below instead.
_BANNED_TAGS: Dict[str, str] = {
    "iframe": "it embeds a nested browsing context, and srcdoc alone "
              "can execute script",
    "frame": "it embeds a nested browsing context",
    "frameset": "it replaces the document body with nested browsing contexts",
    "object": "it embeds external documents or plugin content",
    "embed": "it embeds external documents or plugin content",
    "applet": "it embeds plugin content",
    "portal": "it embeds and preloads a remote page",
    "form": "a static document has nothing to submit — a form is a "
            "data-exfiltration vector on submit",
}

# Tag -> attribute(s) the browser fetches automatically at page-load
# time, as opposed to e.g. <a href>, which fetches only on click.
# Only tags with a conceivable self-contained form appear here; the
# embed/exec-vector tags (iframe, object, embed, ...) are rejected
# wholesale by _BANNED_TAGS above and need no per-attribute rules.
# Deliberately inclusive otherwise: legacy (body/table/cell background,
# html manifest), the <image> alias the HTML parser rewrites to <img>,
# and SVG's fetching elements (use, image) are all listed even though
# jimemo never emits them — an unlisted vector is an unchecked vector,
# and a false listing costs nothing on pages that don't use the tag.
# srcset-family attributes hold multiple candidates and are validated
# per candidate URL.
_FETCH_ON_LOAD_ATTRS: Dict[str, Tuple[str, ...]] = {
    "audio": ("src",),
    "body": ("background",),
    "html": ("manifest",),
    "image": ("src", "srcset", "href", "xlink:href"),
    "img": ("src", "srcset"),
    "input": ("src",),
    "link": ("href", "imagesrcset"),
    "source": ("src", "srcset"),
    "table": ("background",),
    "td": ("background",),
    "th": ("background",),
    "tr": ("background",),
    "track": ("src",),
    "use": ("href", "xlink:href"),
    "video": ("src", "poster"),
}

# srcset-shaped attribute values: comma-separated candidate lists.
_SRCSET_ATTRS = frozenset({"srcset", "imagesrcset"})

# The only (tag, attr) pairs where an inlined raster image data: URI is
# a legitimate self-contained value — the attributes that display an
# image, i.e. exactly what inline_images can produce. Everywhere else
# (link href, audio/video/track src, input src, ...) NO data: URI is
# allowed at all: those attributes embed or apply their target as
# markup, style, or media content, not pixels.
_IMAGE_DATA_URI_ATTRS = frozenset({
    ("img", "src"),
    ("img", "srcset"),
    ("source", "src"),
    ("source", "srcset"),
    ("video", "poster"),
})

# Longest URL to echo back in an error message; a rejected data: URI can
# be megabytes and the tail adds nothing to the diagnosis.
_MAX_URL_IN_MESSAGE = 120


def _shorten(text: str) -> str:
    """`text` truncated for an error message (a script body or URL can
    be megabytes and the tail adds nothing to the diagnosis)."""
    if len(text) <= _MAX_URL_IN_MESSAGE:
        return text
    return text[:_MAX_URL_IN_MESSAGE] + "..."


# Numeric character references. Python's html.unescape silently DROPS
# references to controls and noncharacters (they decode to ""), while a
# browser's tokenizer keeps the real code point in the attribute value —
# e.g. src="da&#1;ta:image/png,..." reads as a clean data: URI here but
# as a relative-path fetch in the browser. A value the two parsers
# disagree on cannot be validated, so its mere presence in a start tag
# fails closed. Legitimate escaping (&amp;, &#39;, &#x27;, ...) always
# decodes to a real character and never trips this.
_NUMERIC_CHARREF_RE = re.compile(r"&#(?:[0-9]+|[xX][0-9a-fA-F]+);?")


# --- CSS references -------------------------------------------------------
# CSS fetches on its own: a url(...) in any property (background,
# cursor, @font-face src, ...) and an @import both load their target at
# style-apply time, so <style> element text and style="..." attribute
# values are scanned against their own allowlist: a url() may hold an
# inlined raster data:image URI or a pure #fragment (a same-document
# paint-server reference, e.g. fill:url(#grad), which never fetches),
# nothing else. This is broader than the HTML fetch-on-load attribute
# allowlist above, which no longer accepts a fragment at all — a CSS
# url() fragment is a reference to an element in the current document,
# not a resource-load attribute pointing at an external resource, so
# the two are judged differently on purpose. @import's target is a
# stylesheet — no allowed form exists (a raster image or a fragment is
# never a stylesheet) — so any @import is an error outright. Violations are
# searched for in the comment-stripped text AND in a copy with CSS
# escapes decoded, so `url/**/(x)`, `\75rl(x)` and `@\69mport` cannot
# hide; the decoded copy only ever ADDS findings (allowances are judged
# on the extracted URL text itself), so over-decoding cannot bless an
# unsafe value — it can only over-reject, which fails closed.

_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
# A CSS escape: backslash + 1-6 hex digits + one optional whitespace,
# or backslash + any other single character (identity escape).
_CSS_ESCAPE_RE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})[ \t\r\n\f]?|(.))", re.DOTALL)
# Where a url( token might start; each hit is then parsed in full by
# _CSS_URL_RE, and a hit that does not parse is itself an error — a
# construct this scanner cannot read cannot be validated.
_CSS_URL_OPEN_RE = re.compile(r"url\(", re.IGNORECASE)
_CSS_URL_RE = re.compile(
    r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)"']*))\s*\)""", re.IGNORECASE
)
# The whole rule text up to the terminator, for the error message; the
# rule is rejected regardless of what its target turns out to be.
_CSS_IMPORT_RE = re.compile(r"@import\b[^;{]*", re.IGNORECASE)


def _css_unescape(text: str) -> str:
    """`text` with CSS escapes decoded (``\\75`` -> ``u``, ``\\:`` ->
    ``:``); out-of-range code points decode to U+FFFD. Used only to
    FIND hidden url(/@import constructs, never to allow anything."""
    def _sub(match: "re.Match") -> str:
        if match.group(1) is not None:
            codepoint = int(match.group(1), 16)
            if 0 < codepoint <= 0x10FFFF:
                return chr(codepoint)
            return "�"
        return match.group(2)
    return _CSS_ESCAPE_RE.sub(_sub, text)


def _css_url_problem(url: str) -> Optional[str]:
    """Why `url` (extracted from a CSS url() reference) violates the
    resource allowlist, or None if allowed — the same two allowed forms
    as _check_fetch_on_load_url: an inlined raster data:image URI or a
    pure #fragment (e.g. an SVG paint server, fill:url(#grad))."""
    if browser_url_form(url).startswith("#"):
        return None
    shown = url if len(url) <= _MAX_URL_IN_MESSAGE else (
        url[:_MAX_URL_IN_MESSAGE] + "..."
    )
    scheme = url_scheme(url)
    if scheme == "data":
        if is_allowed_image_data_uri(url):
            return None
        return (
            f"url({shown!r}) is a disallowed data: URI — only raster "
            "data:image/{png,jpeg,jpg,gif,webp} may be referenced"
        )
    if scheme in ("http", "https") or is_protocol_relative(url):
        return f"url({shown!r}) is a remote resource and would fetch at view time"
    if scheme:
        return f"url({shown!r}): scheme {scheme!r} is not allowed in CSS"
    if not normalize_url(url):
        return "url() with an empty target resolves to the page itself"
    return (
        f"url({shown!r}) is a local path that was not inlined — the "
        "output would depend on a sidecar file"
    )


def _css_reference_errors(css: str) -> List[str]:
    """Error strings for every url()/@import reference in `css` that
    violates the allowlist (see the section comment above)."""
    errors: List[str] = []

    def add(message: str) -> None:
        # The two scan forms usually find the same violations; report
        # each distinct problem once.
        if message not in errors:
            errors.append(message)

    stripped = _CSS_COMMENT_RE.sub("", css)
    forms = [stripped]
    decoded = _css_unescape(stripped)
    if decoded != stripped:
        forms.append(decoded)
    for text in forms:
        for open_match in _CSS_URL_OPEN_RE.finditer(text):
            full = _CSS_URL_RE.match(text, open_match.start())
            if full is None:
                add(
                    "unparseable url( construct — its target cannot be "
                    "validated, failing closed"
                )
                continue
            url = next(g for g in full.groups() if g is not None).strip()
            problem = _css_url_problem(url)
            if problem is not None:
                add(problem)
        for import_match in _CSS_IMPORT_RE.finditer(text):
            rule = import_match.group(0).strip()
            shown = rule if len(rule) <= _MAX_URL_IN_MESSAGE else (
                rule[:_MAX_URL_IN_MESSAGE] + "..."
            )
            add(
                f"{shown!r}: @import always loads a stylesheet, and no "
                "stylesheet source is allowed in a self-contained page"
            )
    return errors


# Sentinel for "vendored bundle not read yet" (None means "read failed",
# which fails closed: no script body can match the library form).
_UNSET = object()


def _has_src_attr(attrs: List[Tuple[str, Optional[str]]]) -> bool:
    """True if `attrs` contains a `src` attribute AT ALL, regardless of
    its value — including a valueless ``<script src>`` (html.parser
    reports its value as None, identical to "attribute absent") and an
    empty ``src=""``. A browser that sees src present ignores the
    element's inline body and fetches src instead, so presence, not
    value, must gate every no-src/has-src decision on a <script> tag;
    testing the value would let a valueless src slip through as if the
    tag had none."""
    return any(name.lower() == "src" for name, _value in attrs)


# Sentinel: the <script> start tag carries no `type` attribute at all
# (distinct from a `type` attribute present with an empty or None
# value, both of which are judged as the empty string below).
_NO_TYPE = object()


def _type_attr(attrs: List[Tuple[str, Optional[str]]]) -> Any:
    """The raw `type` attribute value from a start tag's `attrs`, or
    `_NO_TYPE` if the attribute is absent."""
    for name, value in attrs:
        if name.lower() == "type":
            return value
    return _NO_TYPE


# The only `type` values (case/whitespace-insensitive) under which a
# browser executes a <script>'s body as classic or module JavaScript.
# Absent entirely is the common case (jimemo never emits a type
# attribute); anything else -- application/json, text/template, ... --
# is inert data the browser parses but never runs.
_EXECUTABLE_SCRIPT_TYPES = frozenset({"", "text/javascript", "module"})


def _is_executable_script_type(type_attr: Any) -> bool:
    """True if a <script> carrying this `type` attribute (as returned by
    `_type_attr`) is one whose body a browser actually executes."""
    if type_attr is _NO_TYPE:
        return True
    return (type_attr or "").strip().lower() in _EXECUTABLE_SCRIPT_TYPES


class _Linter(HTMLParser):
    def __init__(
        self,
        charts_declared: bool,
        chart_ids: FrozenSet[str] = frozenset(),
        allowed_scripts: Optional[List[str]] = None,
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.charts_declared = charts_declared
        self.chart_ids = chart_ids
        self.errors: List[str] = []
        self.warnings: List[str] = []
        # Exact mode (see module docstring): the renderer's emitted
        # inline-script bodies as a multiset of remaining expected
        # occurrences, whitespace-stripped exactly as found bodies are
        # in _check_script_body. Engaged only on a chart page — on a
        # chartless page every script errors outright, and a caller-
        # supplied allowlist must not soften that. None means the
        # structural fallback judges each body instead.
        self._allowed_remaining: Optional[Dict[str, int]] = None
        if charts_declared and allowed_scripts is not None:
            counts: Dict[str, int] = {}
            for script in allowed_scripts:
                key = script.strip()
                counts[key] = counts.get(key, 0) + 1
            self._allowed_remaining = counts
        # Buffers a <style> element's text until its end tag (or EOF,
        # for an unterminated element), then the sheet is scanned whole.
        self._style_parts: Optional[List[str]] = None
        # Same for an inline <script> on a chart page, whose body is
        # then checked against the renderer-emitted allowlist. (html.
        # parser treats script/style as CDATA: their text arrives raw
        # via handle_data, charrefs unconverted — the same bytes the
        # browser's tokenizer would see.)
        self._script_parts: Optional[List[str]] = None
        # The current <script>'s `type` attribute (captured at the start
        # tag, read back at flush time -- scripts never nest, so one
        # slot suffices) and a monotonic per-tag sequence number, both
        # feeding the exact-mode completeness checks below.
        self._script_type: Any = _NO_TYPE
        self._script_seq = 0
        self._current_script_seq: Optional[int] = None
        # Exact-mode completeness checks 2 and 3 (see module docstring):
        # the document-order position of the matched library body (None
        # until/unless one is matched) and every matched init body's
        # (chart_id, position) pair, plus every <canvas id="..."> found
        # anywhere on the page. Populated only via _record_script_order,
        # which only exact-mode acceptance calls -- structural mode and
        # chartless pages never touch these, so their close()-time
        # checks (both guarded on self._lib_seq / exact mode) are inert
        # there.
        self._lib_seq: Optional[int] = None
        self._init_seqs: List[Tuple[str, int]] = []
        self._canvas_ids: Set[str] = set()
        self._chart_lib_cache: Any = _UNSET

    def handle_starttag(self, tag, attrs):
        self._check_tag(tag, attrs)
        if tag == "style":
            self._style_parts = []
        elif tag == "script" and self.charts_declared:
            # A src-bearing script (src present at all, any value)
            # already errored in _check_tag; only a true no-src inline
            # body is buffered for the allowlist check.
            if not _has_src_attr(attrs):
                self._script_parts = []
                self._script_type = _type_attr(attrs)

    def handle_startendtag(self, tag, attrs):
        self._check_tag(tag, attrs)  # a <style/> has no text to buffer
        if tag == "script" and self.charts_declared:
            if not _has_src_attr(attrs):
                # A body-less <script/> is nothing the renderer emits;
                # judge its empty body so it fails closed like any other
                # unexpected inline script.
                self._check_script_body("", _type_attr(attrs))

    def handle_endtag(self, tag):
        if tag == "style":
            self._flush_style()
        elif tag == "script":
            self._flush_script()

    def handle_data(self, data):
        if self._style_parts is not None:
            self._style_parts.append(data)
        elif self._script_parts is not None:
            self._script_parts.append(data)

    def close(self):
        super().close()
        self._flush_style()
        self._flush_script()
        if self._allowed_remaining is not None:
            # Exact mode's third failure class: every renderer-emitted
            # body must actually appear. A chart page whose template
            # dropped the library or an init script is not the page the
            # renderer built, so silence here would break the "output is
            # exactly what jimemo rendered" promise.
            for expected, count in self._allowed_remaining.items():
                if count:
                    self.errors.append(
                        "renderer-emitted inline <script> missing from "
                        f"the page ({count} occurrence(s) not found): "
                        f"{_shorten(expected)!r}"
                    )
            # Completeness check 2: the library must already be defined
            # when an init runs. Guarded on _lib_seq (only set once a
            # matched script's body equals the vendored library text) so
            # a caller-supplied allowed_scripts that never includes the
            # library (as several unit tests below do, deliberately
            # exercising only the init multiset) has nothing to check
            # against and stays silent here.
            if self._lib_seq is not None:
                for chart_id, seq in self._init_seqs:
                    if seq < self._lib_seq:
                        self.errors.append(
                            "Chart.js library must load before chart "
                            f"init scripts (init for chart {chart_id!r} "
                            "appears first in document order)"
                        )
            # Completeness check 3: a declared chart with no matching
            # <canvas> would have its init script's getElementById call
            # resolve to nothing Chart.js can draw on. An id present on
            # some other element (e.g. a <div>) does not satisfy this --
            # only _canvas_ids (populated from <canvas> tags alone)
            # counts.
            for chart_id in sorted(self.chart_ids):
                if chart_id not in self._canvas_ids:
                    self.errors.append(
                        f"no <canvas id={chart_id!r}> found for declared "
                        "chart -- its init script has nothing to draw on"
                    )

    def _flush_style(self) -> None:
        if self._style_parts is None:
            return
        css = "".join(self._style_parts)
        self._style_parts = None
        for problem in _css_reference_errors(css):
            self.errors.append(f"in <style> CSS: {problem}")

    def _flush_script(self) -> None:
        if self._script_parts is None:
            return
        body = "".join(self._script_parts)
        self._script_parts = None
        script_type, self._script_type = self._script_type, _NO_TYPE
        self._check_script_body(body, script_type)

    def _chart_lib(self) -> Optional[str]:
        """The vendored Chart.js bundle text in its INLINED form (same
        chart_lib_inline_text charts.py function render.py calls to
        build the library <script> — sourceMappingURL stripped, breakout
        defense re-checked), stripped of surrounding whitespace, read
        lazily on the first inline script judged — never at module
        import — and None if unreadable or unsafe to inline, in which
        case no body can match the library form and everything but a
        valid chart init fails closed."""
        if self._chart_lib_cache is _UNSET:
            try:
                self._chart_lib_cache = chart_lib_inline_text(CHARTJS_BUNDLE).strip()
            except (OSError, ContentError):
                self._chart_lib_cache = None
        return self._chart_lib_cache

    def _check_script_body(
        self, body: str, script_type: Any = _NO_TYPE
    ) -> None:
        """Chart pages only. In exact mode (the render path,
        _allowed_remaining set) a body is legal iff it is one of the
        bodies the renderer actually emitted for THIS page, each
        consumable once — string equality against render's own output,
        so even a well-shaped hand-forged init with different config
        bytes fails. In the structural fallback (no render context) a
        body is legal in exactly two byte shapes — the vendored
        Chart.js bundle, or a charts.chart_init_js body whose id the
        manifest declares and whose config argument is the
        safe-serialized JSON (no raw "<", parses as JSON: data, never
        code). Either way the allowlist is scripts the RENDERER emits,
        so a template author cannot ride a chart declaration to embed
        their own JavaScript. Surrounding whitespace is stripped
        identically on both sides; it cannot arm an otherwise-legal
        body.

        `script_type` (the tag's `type` attribute, from `_type_attr`) is
        checked only in exact mode, only once a body matches: a matched
        body wrapped in a non-executable type is completeness check 1
        (see module docstring) — the byte-exact body a browser never
        runs. That still counts as "found" (it is consumed from
        _allowed_remaining) so the completeness error stands on its own
        rather than piling a spurious "missing" on top of it."""
        stripped = body.strip()
        if self._allowed_remaining is not None:
            remaining = self._allowed_remaining.get(stripped)
            if remaining:
                self._allowed_remaining[stripped] = remaining - 1
                if not _is_executable_script_type(script_type):
                    self.errors.append(
                        "chart script must be a bare executable "
                        f"<script>, got type={script_type!r} — a "
                        "<script> with this type attribute is inert "
                        "and never runs, so the chart would not draw"
                    )
                else:
                    self._record_script_order(stripped)
                return
            if remaining == 0:
                self.errors.append(
                    "duplicate inline <script> on chart page: "
                    f"{_shorten(stripped)!r} appears more times than the "
                    "renderer emitted it"
                )
            else:
                self.errors.append(
                    "unexpected inline <script> on chart page: "
                    f"{_shorten(stripped)!r} — the page's inline scripts "
                    "must be exactly the ones the renderer emitted (the "
                    "vendored Chart.js library and one built init per "
                    "declared chart)"
                )
            return
        lib = self._chart_lib()
        if lib is not None and stripped == lib:
            return
        parsed = parse_chart_init_js(stripped)
        if parsed is None:
            self.errors.append(
                f"unexpected inline <script> on chart page: "
                f"{_shorten(stripped)!r} — the only inline scripts "
                "allowed are the vendored Chart.js library and one "
                "renderer-built init per declared chart"
            )
            return
        chart_id, config_json = parsed
        if chart_id not in self.chart_ids:
            self.errors.append(
                f"chart init <script> references chart id {chart_id!r}, "
                "which the manifest does not declare"
            )
            return
        if "<" in config_json:
            self.errors.append(
                f"chart init <script> for {chart_id!r} contains a raw "
                "'<' in its config — serialize_chart_config always "
                "\\u003c-escapes '<', so this is not renderer-built "
                "output"
            )
            return
        try:
            json.loads(config_json)
        except ValueError:
            self.errors.append(
                f"chart init <script> for {chart_id!r} has a config "
                "argument that is not valid JSON — renderer-built "
                "configs are pure data, never code"
            )

    def _record_script_order(self, stripped: str) -> None:
        """Classifies an exact-mode-accepted script body as the library
        or a chart init, independent of any position the body happened
        to occupy in the caller's own allowed_scripts list (lint_html's
        docstring treats that list as an unordered multiset) — purely by
        matching the same two byte shapes the structural fallback
        recognizes, self._chart_lib() and parse_chart_init_js. Records
        its document-order sequence number for completeness check 2,
        judged in close()."""
        seq = self._current_script_seq
        lib = self._chart_lib()
        if lib is not None and stripped == lib:
            if self._lib_seq is None or (seq is not None and seq < self._lib_seq):
                self._lib_seq = seq
            return
        parsed = parse_chart_init_js(stripped)
        if parsed is not None and seq is not None:
            chart_id, _config = parsed
            self._init_seqs.append((chart_id, seq))

    def _check_tag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        raw_tag = self.get_starttag_text() or ""
        for match in _NUMERIC_CHARREF_RE.finditer(raw_tag):
            if unescape(match.group(0)) == "":
                self.errors.append(
                    f"<{tag}> contains numeric character reference "
                    f"{match.group(0)!r} to a control/noncharacter code "
                    "point — this parser drops it but a browser keeps it, "
                    "so the attribute value cannot be trusted as written"
                )
                break

        reason = _BANNED_TAGS.get(tag)
        if reason is not None:
            self.errors.append(
                f"<{tag}> is never allowed in a self-contained page — {reason}"
            )

        if tag == "script":
            # Document-order position of this tag, read back at flush
            # time by _record_script_order (completeness check 2).
            # Scripts never nest, so one counter/slot pair suffices.
            self._script_seq += 1
            self._current_script_seq = self._script_seq
            if _has_src_attr(attrs):
                # Never allowed, remote, local, or valueless/empty: a
                # src-bearing script is an external fetch/file
                # dependency (or, for a browser, "ignore my body and
                # fetch src") either way. (Phase 4 chart support vendors
                # its script inline instead.) Presence is what counts —
                # not the value — so this reports the raw value only
                # for display, defaulting to "" when there is none.
                src = next((v for n, v in attrs if n.lower() == "src"), None)
                self.errors.append(
                    f'<script src="{src if src is not None else ""}"> '
                    "is never allowed"
                )
            elif not self.charts_declared:
                self.errors.append(
                    "<script> tag found but this template declares no charts "
                    "(manifest 'charts' is empty)"
                )
            # else: the ONE controlled opening in the no-script rule
            # (Phase 4 charts): an inline, src-less <script> may pass
            # when the manifest declares charts — but only if its BODY
            # is one the renderer emits: in exact mode, string-equal to
            # a body render_page actually built for this page; in the
            # structural fallback, the vendored bundle or a declared
            # chart's init shape (see _check_script_body, fed by the
            # buffering in handle_starttag/handle_data). This is still
            # not a JavaScript judge — a lint that pretends to evaluate
            # JS is a false promise — it recognizes renderer output and
            # rejects everything else, so the guarantee holds even for
            # a template someone else wrote. Every other Phase 3 rule
            # (script src, on*, script-scheme URLs, banned tags, the
            # fetch-on-load and CSS allowlists) still applies on chart
            # pages, unchanged.

        if tag == "canvas":
            # Fed to completeness check 3 (close()): a declared chart id
            # must land on an actual <canvas>, not merely appear as some
            # other element's id.
            canvas_id = next((v for n, v in attrs if n.lower() == "id"), None)
            if canvas_id:
                self._canvas_ids.add(canvas_id)

        if tag == "meta":
            http_equiv = next(
                (v for n, v in attrs if n.lower() == "http-equiv"), None
            )
            if http_equiv is not None and normalize_url(http_equiv) == "refresh":
                self.errors.append(
                    '<meta http-equiv="refresh"> is never allowed — it '
                    "navigates away from the page at view time"
                )

        if tag == "base":
            href = next((v for n, v in attrs if n.lower() == "href"), None)
            if href is not None:
                self.errors.append(
                    f"<base href={href!r}> is not allowed: it rebases every "
                    "relative and #fragment URL on the page against a "
                    "remote origin, defeating the self-contained allowlist"
                )

        for name, value in attrs:
            name = name.lower()
            if name.startswith("on"):
                self.errors.append(
                    f"inline event handler found ({name!r} on <{tag}>) — "
                    "on* attributes are never allowed"
                )
                continue
            if name == "style" and value:
                for problem in _css_reference_errors(value):
                    self.errors.append(
                        f"in style attribute on <{tag}>: {problem}"
                    )
                continue
            if value is None or name not in _URL_ATTRS:
                continue
            scheme = url_scheme(value)
            if scheme in ("javascript", "vbscript"):
                self.errors.append(
                    f"{scheme}: URI found on <{tag} {name}> — "
                    "script-scheme URLs are never allowed"
                )
                continue

        for attr_name in _FETCH_ON_LOAD_ATTRS.get(tag, ()):
            # Every occurrence is validated, not just the first one the
            # browser would honor: a duplicate attribute that disagrees
            # is at best confusing markup and at worst a parser trick.
            for name, value in attrs:
                if name.lower() != attr_name:
                    continue
                if attr_name in _SRCSET_ATTRS:
                    # An empty candidate list fetches nothing; each real
                    # candidate URL must pass the allowlist on its own.
                    for url, _descriptor in parse_srcset(value or ""):
                        self._check_fetch_on_load_url(tag, attr_name, url)
                else:
                    self._check_fetch_on_load_url(tag, attr_name, value or "")

    def _check_fetch_on_load_url(self, tag: str, attr_name: str, value: str) -> None:
        """Strict allowlist (see module docstring): `value` is legal only
        as a raster image data: URI on an image-displaying attribute.
        Everything else — including a pure #fragment, which still makes
        the browser attempt a same-document resource load on a
        fetch-on-load attribute (unlike <a href>, which only navigates on
        click and isn't checked here) — appends an error naming the tag,
        attribute, value, and reason."""
        shown = value if len(value) <= _MAX_URL_IN_MESSAGE else (
            value[:_MAX_URL_IN_MESSAGE] + "..."
        )
        where = f"<{tag} {attr_name}={shown!r}>"

        # Judged in the browser-faithful form (see browser_url_form): the
        # over-normalized form may read "#x" out of a value the browser
        # would actually treat as a relative path and fetch — so a value
        # this form does NOT recognize as a fragment must still be judged
        # as one below (it falls through to the local-path/empty checks,
        # which fail closed too).
        if browser_url_form(value).startswith("#"):
            self.errors.append(
                f"{where}: a #fragment on a fetch-on-load resource "
                "attribute still makes the browser attempt a "
                "same-document resource load — only an inlined "
                "data:image (image-displaying attributes only) is "
                "allowed here; <a href> is the attribute for "
                "same-document navigation"
            )
            return

        compact = normalize_url(value)
        scheme = url_scheme(value)
        if scheme == "data":
            if (tag, attr_name) in _IMAGE_DATA_URI_ATTRS:
                if is_allowed_image_data_uri(value):
                    return
                self.errors.append(
                    f"{where} is not an allowed image data URI — only "
                    "data:image/{png,jpeg,jpg,gif,webp} are permitted "
                    "(svg+xml can carry markup; other data: subtypes "
                    "aren't raster images)"
                )
            else:
                self.errors.append(
                    f"{where}: data: URIs are not allowed on this "
                    "attribute — only image-displaying attributes "
                    "(img/source src+srcset, video poster) may carry an "
                    "inlined data:image"
                )
            return
        if scheme in ("http", "https") or is_protocol_relative(value):
            self.errors.append(
                f"external {where} would fetch at view time — the output "
                "must be self-contained (images are inlined as data: "
                "URIs; nothing else may be referenced)"
            )
            return
        if scheme:
            self.errors.append(
                f"{where}: scheme {scheme!r} is not allowed on a "
                "fetch-on-load attribute — only an inlined data:image "
                "(image-displaying attributes only) is"
            )
            return
        if not compact:
            self.errors.append(
                f"{where} is empty — an empty resource reference is at "
                "best dead markup and at worst resolves to the page "
                "itself; drop the attribute instead"
            )
            return
        self.errors.append(
            f"{where}: local path was not inlined — the output would "
            "depend on a sidecar file and is not self-contained"
        )


def _declared_chart_ids(manifest: Dict[str, Any]) -> FrozenSet[str]:
    """Chart ids from the manifest's ``charts`` list. load_manifest
    guarantees objects with validated string ids; bare-string entries
    are accepted too so hand-built manifest dicts passed straight to
    lint_html behave predictably."""
    ids = set()
    for chart in manifest.get("charts") or []:
        if isinstance(chart, str):
            ids.add(chart)
        elif isinstance(chart, dict) and isinstance(chart.get("id"), str):
            ids.add(chart["id"])
    return frozenset(ids)


def lint_html(
    html: str,
    manifest: Dict[str, Any],
    allowed_scripts: Optional[List[str]] = None,
) -> Tuple[List[str], List[str]]:
    """Lint `html` against `manifest`; ``(errors, warnings)``.

    ``allowed_scripts`` — the exact inline-script bodies the renderer
    emitted for this page, in render_page's hands the inlined chart
    library plus each chart's init body. When given (and the manifest
    declares charts) the page's inline scripts must equal that multiset
    exactly: forged/altered, extra, duplicate, and missing bodies are
    each errors, and three completeness checks close the remaining gap
    between "the exact bodies are present" and "the page actually draws
    the charts" — see the module docstring's closing paragraph: a
    matched body must sit in a bare executable ``<script>`` (no
    non-executable ``type``), the library body must precede every init
    body in document order, and every manifest-declared chart id needs
    a matching ``<canvas id="...">`` on the page. When None (direct
    callers with no render context) each inline script body is judged
    structurally instead — see the module docstring. The render
    pipeline always passes it, so production output is held to the
    exact set.
    """
    linter = _Linter(
        charts_declared=bool(manifest.get("charts")),
        chart_ids=_declared_chart_ids(manifest),
        allowed_scripts=allowed_scripts,
    )
    linter.feed(html)
    linter.close()

    errors, warnings = linter.errors, linter.warnings

    size = len(html.encode("utf-8"))
    if size > MAX_OUTPUT_BYTES:
        warnings.append(f"output is {size} bytes, over the {MAX_OUTPUT_BYTES}-byte guideline")

    return errors, warnings
