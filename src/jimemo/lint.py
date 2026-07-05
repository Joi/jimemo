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
script BODIES: the only bodies accepted are the ones the RENDERER
emits — the vendored Chart.js bundle (byte-compared against the file at
CHARTJS_BUNDLE, read lazily at lint time) and, per declared chart, an
init in exactly the shape charts.chart_init_js builds, whose id the
manifest declares, whose config contains no raw ``<`` (the serializer
u003c-escapes every one), and whose config parses as JSON — pure data,
never code. Any other inline script is an error, so a shared
third-party template cannot ride a chart declaration to embed its own
JavaScript. This is still not a JavaScript judge: it recognizes the two
renderer-emitted byte shapes and rejects everything else, fail closed.
"""
import json
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from ._paths import CHARTJS_BUNDLE
from .charts import parse_chart_init_js
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


class _Linter(HTMLParser):
    def __init__(
        self, charts_declared: bool, chart_ids: FrozenSet[str] = frozenset()
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.charts_declared = charts_declared
        self.chart_ids = chart_ids
        self.errors: List[str] = []
        self.warnings: List[str] = []
        # Buffers a <style> element's text until its end tag (or EOF,
        # for an unterminated element), then the sheet is scanned whole.
        self._style_parts: Optional[List[str]] = None
        # Same for an inline <script> on a chart page, whose body is
        # then checked against the renderer-emitted allowlist. (html.
        # parser treats script/style as CDATA: their text arrives raw
        # via handle_data, charrefs unconverted — the same bytes the
        # browser's tokenizer would see.)
        self._script_parts: Optional[List[str]] = None
        self._chart_lib_cache: Any = _UNSET

    def handle_starttag(self, tag, attrs):
        self._check_tag(tag, attrs)
        if tag == "style":
            self._style_parts = []
        elif tag == "script" and self.charts_declared:
            src = next((v for n, v in attrs if n.lower() == "src"), None)
            # A src'd script already errored in _check_tag; only an
            # inline body is buffered for the allowlist check.
            if src is None:
                self._script_parts = []

    def handle_startendtag(self, tag, attrs):
        self._check_tag(tag, attrs)  # a <style/> has no text to buffer
        if tag == "script" and self.charts_declared:
            src = next((v for n, v in attrs if n.lower() == "src"), None)
            if src is None:
                # A body-less <script/> is nothing the renderer emits;
                # judge its empty body so it fails closed like any other
                # unexpected inline script.
                self._check_script_body("")

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
        self._check_script_body(body)

    def _chart_lib(self) -> Optional[str]:
        """The vendored Chart.js bundle text (stripped), read lazily on
        the first inline script judged — never at module import — and
        None if unreadable, in which case no body can match the library
        form and everything but a valid chart init fails closed."""
        if self._chart_lib_cache is _UNSET:
            try:
                self._chart_lib_cache = CHARTJS_BUNDLE.read_text(
                    encoding="utf-8"
                ).strip()
            except OSError:
                self._chart_lib_cache = None
        return self._chart_lib_cache

    def _check_script_body(self, body: str) -> None:
        """Chart pages only: an inline script body is legal in exactly
        two byte shapes — the vendored Chart.js bundle, or a
        charts.chart_init_js body whose id the manifest declares and
        whose config argument is the safe-serialized JSON (no raw "<",
        parses as JSON: data, never code). The allowlist is the set of
        scripts the RENDERER emits, so a template author cannot ride a
        chart declaration to embed their own JavaScript. Surrounding
        whitespace is ignored; it cannot arm an otherwise-legal body."""
        stripped = body.strip()
        lib = self._chart_lib()
        if lib is not None and stripped == lib:
            return
        parsed = parse_chart_init_js(stripped)
        if parsed is None:
            shown = stripped if len(stripped) <= _MAX_URL_IN_MESSAGE else (
                stripped[:_MAX_URL_IN_MESSAGE] + "..."
            )
            self.errors.append(
                f"unexpected inline <script> on chart page: {shown!r} — "
                "the only inline scripts allowed are the vendored "
                "Chart.js library and one renderer-built init per "
                "declared chart"
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
            src = next((v for n, v in attrs if n.lower() == "src"), None)
            if src is not None:
                # Never allowed, remote or local: a src'd script is an
                # external fetch/file dependency either way. (Phase 4
                # chart support vendors its script inline instead.)
                self.errors.append(f'<script src="{src}"> is never allowed')
            elif not self.charts_declared:
                self.errors.append(
                    "<script> tag found but this template declares no charts "
                    "(manifest 'charts' is empty)"
                )
            # else: the ONE controlled opening in the no-script rule
            # (Phase 4 charts): an inline, src-less <script> may pass
            # when the manifest declares charts — but only if its BODY
            # is one the renderer emits (the vendored bundle or a
            # declared chart's init; see _check_script_body, fed by the
            # buffering in handle_starttag/handle_data). This is still
            # not a JavaScript judge — a lint that pretends to evaluate
            # JS is a false promise — it recognizes two byte shapes and
            # rejects everything else, so the guarantee holds even for
            # a template someone else wrote. Every other Phase 3 rule
            # (script src, on*, script-scheme URLs, banned tags, the
            # fetch-on-load and CSS allowlists) still applies on chart
            # pages, unchanged.

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


def lint_html(html: str, manifest: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    linter = _Linter(
        charts_declared=bool(manifest.get("charts")),
        chart_ids=_declared_chart_ids(manifest),
    )
    linter.feed(html)
    linter.close()

    errors, warnings = linter.errors, linter.warnings

    size = len(html.encode("utf-8"))
    if size > MAX_OUTPUT_BYTES:
        warnings.append(f"output is {size} bytes, over the {MAX_OUTPUT_BYTES}-byte guideline")

    return errors, warnings
