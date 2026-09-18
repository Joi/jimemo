"""Allowlist HTML sanitizer for markdown-rendered slot content.

python-markdown passes raw HTML in the source through to its output
verbatim, so a content file (which may come from an untrusted source —
e.g. a briefing assembled from web research) can smuggle
``<img onerror=...>``, ``<svg onload=...>`` or ``javascript:`` links
into the rendered page. ``sanitize_html`` rebuilds the markdown output
from parsed tokens, keeping only the tags/attributes python-markdown
itself emits (extensions: tables, fenced_code) and dropping everything
else. Pure stdlib (html.parser); no vendored dependency.

Rules:
  * Tags outside ALLOWED_TAGS are unwrapped (tag dropped, children
    kept), except DISCARD_TAGS whose entire subtree is removed.
  * Only ``a``, ``img``, ``th``/``td``, ``code`` keep any attributes at
    all, per ALLOWED_ATTRS; ``style`` on table cells must be exactly the
    text-align rule python-markdown emits for column alignment; ``class``
    on ``code`` must be exactly the ``language-*`` form the fenced_code
    extension emits.
  * ``on*`` attributes are always stripped, on every tag.
  * ``href``/``src`` values are scheme-checked after entity decoding
    and whitespace/control stripping, so ``java&#09;script:`` and
    friends are rejected; only relative URLs, http(s), ``#fragment``,
    ``mailto:`` (href) and raster ``data:image/{png,jpeg,jpg,gif,webp}``
    (img src) survive — SVG can itself carry markup/script, so it's
    excluded even though ``<img>`` never executes it, and every other
    ``data:`` subtype is excluded wholesale rather than enumerated.
"""
import html
import re
from html.parser import HTMLParser
from typing import List, Optional, Tuple
from urllib.parse import urlsplit

# Everything python-markdown (with the tables and fenced_code
# extensions) emits for legitimate markdown constructs.
ALLOWED_TAGS = frozenset({
    "p", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "em", "strong", "del", "code", "pre", "blockquote",
    "a", "ul", "ol", "li", "img",
    "table", "thead", "tbody", "tr", "th", "td",
    "sup", "sub",
})

# Dropped together with their entire content, not unwrapped: their text
# children are executable/renderable payload, not prose.
DISCARD_TAGS = frozenset({
    "script", "style", "svg", "iframe", "object", "embed", "template",
})

VOID_TAGS = frozenset({"br", "hr", "img"})

ALLOWED_ATTRS = {
    # `id` on <a> alone, so raw-HTML in-page anchors (<a id="x"></a>
    # plus a markdown [jump](#x)) have a target to scroll to. Carries
    # no URL and executes nothing; the value is attribute-escaped by
    # _format_tag like every other kept attribute. Not added to any
    # other tag, and `name` is not added either.
    "a": frozenset({"href", "title", "id"}),
    "img": frozenset({"src", "alt", "title"}),
    "th": frozenset({"align", "style"}),
    "td": frozenset({"align", "style"}),
    "code": frozenset({"class"}),
}

# The one style python-markdown's tables extension emits for column
# alignment. Anything else on a cell is dropped.
_TEXT_ALIGN_RE = re.compile(r"^\s*text-align:\s*(left|right|center);?\s*$")

# The `class` python-markdown's fenced_code extension emits to carry the
# language hint (e.g. `class="language-python"`). Anything else is dropped.
_FENCED_CODE_LANG_RE = re.compile(r"^language-[\w-]+$")


def normalize_url(value: str) -> str:
    """`value` in its most-decoded form: entity-decoded (again,
    defensively; an HTML parser upstream already decoded once),
    control/space characters removed, lowercased — so obfuscations like
    ``java&#09;script:`` or ``JaVaScRiPt:`` are judged decoded.
    Over-decoding can only reject a value that would have been safe,
    never accept an unsafe one."""
    return "".join(ch for ch in html.unescape(value) if ord(ch) > 0x20).lower()


def url_scheme(value: str) -> str:
    """Scheme of the normalized URL (``"javascript"``, ``"https"``, ...),
    or ``""`` for relative/fragment-only/empty values. Shared by the
    sanitizer's allowlist and lint's last-gate scheme checks so both
    judge the same normalization (see `normalize_url`)."""
    compact = normalize_url(value)
    if not compact or compact.startswith("#"):
        return ""
    head = re.split(r"[/?#]", compact, maxsplit=1)[0]
    if ":" not in head:
        return ""  # relative URL, no scheme
    return head.split(":", 1)[0]


# The chars the WHATWG URL parser strips from the ends of an input URL
# (C0 controls and space) before parsing.
_URL_EDGE_STRIP = "".join(chr(c) for c in range(0x21))

# data: image subtypes that are pure raster pixels. svg+xml is excluded
# (it can carry markup/script); everything else — bmp, tiff, avif,
# x-icon, future subtypes — is excluded wholesale rather than judged.
_RASTER_IMAGE_SUBTYPES = frozenset({"png", "jpeg", "jpg", "gif", "webp"})

# data: font subtypes/prefixes that are binary font formats. The
# `application/font-*` / `application/x-font-*` forms are legacy
# vendor-prefixed mimes some encoders still emit for woff/woff2/ttf.
_FONT_SUBTYPES = frozenset({"ttf", "otf", "woff", "woff2"})
_LEGACY_FONT_MIME_PREFIXES = ("application/font-", "application/x-font-")


def _mime_head(form: str) -> str:
    """`type/subtype` from a normalized ``data:`` URI `form` (see
    `is_allowed_image_data_uri` for the normalization contract this is
    always called under), or ``""`` if `form` isn't a ``data:`` URI."""
    if not form.startswith("data:"):
        return ""
    return re.split(r"[;,]", form[len("data:"):], maxsplit=1)[0]


def _is_font_data_uri(form: str) -> bool:
    mime = _mime_head(form)
    if mime.startswith("font/"):
        return mime[len("font/"):] in _FONT_SUBTYPES
    for prefix in _LEGACY_FONT_MIME_PREFIXES:
        if mime.startswith(prefix):
            # Exact subtype match, not just the prefix: a bare
            # `mime.startswith(_LEGACY_FONT_MIME_PREFIXES)` (the prior
            # form) accepted ANY subtype after "application/font-"/
            # "application/x-font-", including one that isn't a real
            # font format at all (e.g. "application/font-evil").
            return mime[len(prefix):] in _FONT_SUBTYPES
    return False


def browser_url_form(value: str) -> str:
    """`value` as a browser's URL parser would first see it: leading and
    trailing C0-control/space characters stripped, ASCII tab/newline/CR
    removed everywhere (the WHATWG URL spec's preprocessing), lowercased.
    Unlike `normalize_url` this does NOT entity-decode a second time or
    strip other mid-URL control characters — it mirrors, rather than
    exceeds, what the browser does. Allow-side decisions (lint's
    ``#fragment`` allowance, `is_allowed_image_data_uri`) must hold in
    THIS form too: judging an allowance only on the over-normalized form
    could bless a value the browser actually treats as a relative fetch
    (e.g. ``da\\x01ta:image/png,...``, whose control char survives URL
    parsing and demotes it to a path)."""
    compact = value.strip(_URL_EDGE_STRIP)
    for ch in ("\t", "\n", "\r"):
        compact = compact.replace(ch, "")
    return compact.lower()


def _is_raster_image_data_uri(form: str) -> bool:
    mime = _mime_head(form)
    return mime.startswith("image/") and mime[len("image/"):] in _RASTER_IMAGE_SUBTYPES


def is_allowed_image_data_uri(value: str) -> bool:
    """True if `value` is a raster ``data:image/{png,jpeg,jpg,gif,webp}``
    URI in BOTH normalizations: `normalize_url` (paranoid — catches
    obfuscation on the block side) and `browser_url_form` (faithful —
    guarantees the browser really sees a data: URI and not a relative
    path it would fetch; see that function's docstring). SVG is excluded
    because it can itself carry markup/script; every other subtype is
    excluded because nothing in this pipeline legitimately produces it
    (inline_images emits exactly these five). Shared by the sanitizer's
    ``img src`` allowance, inline_images' early check, and lint's
    last-gate allowlist, so all three judge identically.

    Stays image-only on purpose: callers here must never accept a font
    payload in an `<img>`/CSS-image context. Use `is_allowed_data_uri`
    where a font payload is also legitimate (e.g. design-token import)."""
    return _is_raster_image_data_uri(normalize_url(value)) and _is_raster_image_data_uri(
        browser_url_form(value)
    )


def is_allowed_data_uri(value: str) -> bool:
    """True if `value` is a ``data:`` URI whose mime type is an
    allowlisted raster image (see `is_allowed_image_data_uri`) OR an
    allowlisted binary font (``font/{ttf,otf,woff,woff2}``, or a legacy
    ``application/font-*``/``application/x-font-*`` vendor form), judged
    in both normalizations exactly like `is_allowed_image_data_uri`
    (same rationale: paranoid `normalize_url` catches obfuscation,
    faithful `browser_url_form` guarantees the browser agrees it's a
    data: URI at all). Does not itself constrain the payload charset —
    callers that need to bound what a data: URI can contain (e.g. to
    keep it from smuggling a `;` past a blunt delimiter check) must
    additionally require the payload matches a `;base64,<payload>` shape
    before calling this."""
    def _allowed(form: str) -> bool:
        return _is_raster_image_data_uri(form) or _is_font_data_uri(form)

    return _allowed(normalize_url(value)) and _allowed(browser_url_form(value))


def parse_srcset(value: str) -> List[Tuple[str, str]]:
    """``(url, descriptor)`` pairs from a ``srcset`` attribute value,
    split the way the HTML spec (and therefore the browser) does: a
    candidate URL is a maximal run of non-whitespace characters — so a
    ``data:`` URI's own commas stay inside one URL — and a comma only
    separates candidates when it trails a URL or follows a descriptor.
    Descriptor is ``""`` when absent. Shared by lint (which validates
    every candidate URL) and inline_images (which rewrites local ones),
    so both judge exactly the candidates a browser would fetch; naive
    comma-splitting would shred inlined data: URIs into a bogus "URL"
    prefix and a payload tail that looks like a local path."""
    out: List[Tuple[str, str]] = []
    pos, n = 0, len(value)
    while pos < n:
        while pos < n and (value[pos] in " \t\n\f\r" or value[pos] == ","):
            pos += 1
        if pos >= n:
            break
        start = pos
        while pos < n and value[pos] not in " \t\n\f\r":
            pos += 1
        url = value[start:pos]
        if url.endswith(","):
            url = url.rstrip(",")
            if url:
                out.append((url, ""))
            continue
        desc_start = pos
        while pos < n and value[pos] != ",":
            pos += 1
        descriptor = value[desc_start:pos].strip(" \t\n\f\r")
        pos += 1  # past the separating comma (or harmlessly off the end)
        out.append((url, descriptor))
    return out


def is_protocol_relative(value: str) -> bool:
    """True if `value` has no scheme but resolves a netloc once
    normalized — a protocol-relative reference like
    ``//cdn.example/x.css``. The browser fetches these with the
    embedding page's own scheme, so they're a remote resource exactly
    like an explicit http(s) URL, even though `url_scheme` reports
    ``""`` for them (otherwise shaped like a relative URL). Used by
    lint's last-gate external-resource check on ``<img src>``/``<link
    href>``; a bare root-relative path (``/x``) or fragment (``#x``)
    has no netloc and is correctly not flagged."""
    if url_scheme(value):
        return False
    return bool(urlsplit(normalize_url(value)).netloc)


def _url_allowed(value: str, *, allow_mailto: bool, allow_data_image: bool) -> bool:
    """True if the URL's scheme is acceptable (see module docstring for
    the per-context rules)."""
    compact = normalize_url(value)
    if not compact or compact.startswith("#"):
        return True
    head = re.split(r"[/?#]", compact, maxsplit=1)[0]
    if ":" not in head:
        return True  # relative URL, no scheme
    scheme = head.split(":", 1)[0]
    if scheme in ("http", "https"):
        return True
    if scheme == "mailto" and allow_mailto:
        return True
    if scheme == "data" and allow_data_image and is_allowed_image_data_uri(value):
        return True
    return False


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        # While set, everything (tags and text) is discarded until the
        # matching close tag at the recorded nesting depth.
        self._discard_tag: Optional[str] = None
        self._discard_depth = 0

    # -- tag emission --------------------------------------------------

    def _format_tag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]], self_closing: bool
    ) -> str:
        parts = ["<", tag]
        allowed = ALLOWED_ATTRS.get(tag, frozenset())
        for name, value in attrs:
            name = name.lower()
            if name.startswith("on"):
                continue  # never, on any tag, regardless of allowlists
            if name not in allowed:
                continue
            if value is None:
                value = ""
            if tag == "a" and name == "href":
                if not _url_allowed(value, allow_mailto=True, allow_data_image=False):
                    continue
            elif tag == "img" and name == "src":
                if not _url_allowed(value, allow_mailto=False, allow_data_image=True):
                    continue
            elif tag == "code" and name == "class":
                if not _FENCED_CODE_LANG_RE.match(value):
                    continue
            elif name == "style":  # only reachable on th/td
                if not _TEXT_ALIGN_RE.match(value):
                    continue
            parts.append(' {0}="{1}"'.format(name, html.escape(value, quote=True)))
        parts.append(" />" if self_closing else ">")
        return "".join(parts)

    # -- parser callbacks ----------------------------------------------

    def handle_starttag(self, tag, attrs):
        if self._discard_tag is not None:
            if tag == self._discard_tag:
                self._discard_depth += 1
            return
        if tag in DISCARD_TAGS:
            self._discard_tag = tag
            self._discard_depth = 1
            return
        if tag not in ALLOWED_TAGS:
            return  # unwrap: drop the tag, keep its children
        self.out.append(self._format_tag(tag, attrs, self_closing=tag in VOID_TAGS))

    def handle_startendtag(self, tag, attrs):
        if self._discard_tag is not None or tag in DISCARD_TAGS:
            return
        if tag not in ALLOWED_TAGS:
            return
        if tag in VOID_TAGS:
            self.out.append(self._format_tag(tag, attrs, self_closing=True))
        else:
            self.out.append(self._format_tag(tag, attrs, self_closing=False))
            self.out.append("</{0}>".format(tag))

    def handle_endtag(self, tag):
        if self._discard_tag is not None:
            if tag == self._discard_tag:
                self._discard_depth -= 1
                if self._discard_depth == 0:
                    self._discard_tag = None
            return
        if tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.out.append("</{0}>".format(tag))

    def handle_data(self, data):
        if self._discard_tag is not None:
            return
        # convert_charrefs=True delivered this decoded, so one escape
        # round-trips existing entities without double-escaping.
        self.out.append(html.escape(data, quote=False))

    def handle_comment(self, data):
        pass

    def handle_decl(self, decl):
        pass

    def handle_pi(self, data):
        pass

    def unknown_decl(self, data):
        pass


def sanitize_html(html_text: str) -> str:
    """Rebuild `html_text` keeping only allowlisted tags/attributes/URL
    schemes (see module docstring). An unterminated DISCARD_TAGS element
    discards the rest of the document — failing closed."""
    parser = _Sanitizer()
    parser.feed(html_text)
    parser.close()
    return "".join(parser.out)


# --- SVG figure sanitizer (render --figure splice) -----------------------
#
# `jimemo render --figure NAME=file.svg` splices an operator-provided
# SVG into the rendered page as INLINE SVG, so the page's --jm-* custom
# properties cascade into the diagram (docs/diagrams.md). The file is
# untrusted exactly like markdown content — agents pass machine-
# generated SVG without reading it — so it is rebuilt through an
# allowlist parser in the same spirit as _Sanitizer, with these
# SVG-specific rules: an element outside the allowlist is dropped WITH
# its whole subtree (inside an SVG there is no prose to unwrap — every
# unrecognized element is assumed payload); <title> and <desc> are
# text-only (in a browser they are HTML integration points); `style` is
# KEPT, because var(--jm-*) theming is the whole point of inline SVG,
# but it and the paint attributes are judged by _svg_css_value_ok, which
# refuses rather than rewrites; and the result must be exactly one root
# <svg> element or the input is refused outright. Entry points:
# sanitize_svg (markup) and sanitize_svg_with_ids (markup + ids).
# Nothing here touches the markdown path above: new names only.
#
# Everything emitted is rebuilt from parsed tokens and escaped, so a
# difference between html.parser and a browser's tree construction can
# change what is DROPPED but never smuggle markup through: no input
# byte reaches the output unescaped.

# Nothing else survives. Everything not listed — script, style,
# foreignObject, iframe, object, embed, image, a, the SMIL animation
# elements (animate, animateTransform, animateMotion, set), and any
# unknown element — is dropped together with its content. Names are the
# lowercased forms html.parser reports (see _SVG_CAMEL_CASE for the
# restore map).
_SVG_ALLOWED_TAGS = frozenset({
    "svg", "g", "defs", "title", "desc", "path", "rect", "circle",
    "ellipse", "line", "polyline", "polygon", "text", "tspan", "marker",
    "pattern", "lineargradient", "radialgradient", "stop", "clippath",
    "use",
})

# Presentation/geometry attributes (lowercased; html.parser reports
# attribute names lowercased, so the allowlist matches that form).
_SVG_ALLOWED_ATTRS = frozenset({
    "id", "class", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r",
    "rx", "ry", "width", "height", "d", "points", "viewbox", "xmlns",
    "fill", "stroke", "stroke-width", "stroke-dasharray", "stroke-linecap",
    "stroke-linejoin", "opacity", "fill-opacity", "stroke-opacity",
    "transform", "font-size", "font-family", "font-weight", "text-anchor",
    "dominant-baseline", "dx", "dy", "marker-start", "marker-mid",
    "marker-end", "markerwidth", "markerheight", "refx", "refy",
    "orient", "markerunits", "patternunits", "patterntransform",
    "gradientunits", "gradienttransform", "offset", "stop-color",
    "stop-opacity", "clip-path", "role", "aria-label", "aria-labelledby",
    "aria-hidden", "preserveaspectratio", "style", "href", "xlink:href",
})

# html.parser lowercases tag and attribute names; SVG is case-sensitive
# XML, so the camelCase forms are restored on output. Membership checks
# stay on the lowercased names; this map only shapes the emitted text
# (so it is harmless that e.g. attribute "clip-path" keeps its hyphen
# while element clippath maps to clipPath).
_SVG_CAMEL_CASE = {
    "viewbox": "viewBox",
    "lineargradient": "linearGradient",
    "radialgradient": "radialGradient",
    "clippath": "clipPath",
    "markerwidth": "markerWidth",
    "markerheight": "markerHeight",
    "refx": "refX",
    "refy": "refY",
    "markerunits": "markerUnits",
    "patternunits": "patternUnits",
    "patterntransform": "patternTransform",
    "gradientunits": "gradientUnits",
    "gradienttransform": "gradientTransform",
    "preserveaspectratio": "preserveAspectRatio",
}

# Presentation attributes whose VALUE a browser parses as CSS (a paint
# or a reference) and which may therefore carry url(...) or another
# fetching function: fill, stroke, clip-path, marker-start, marker-mid,
# marker-end. They get exactly the rules a style value gets
# (_svg_css_value_ok) — only url(#fragment) survives there.
_SVG_CSS_VALUE_ATTRS = frozenset({
    "fill", "stroke", "clip-path", "marker-start", "marker-mid", "marker-end",
})

# Allowed attributes whose value is NOT CSS and is never judged as CSS:
# identifiers, labels (an aria-label may well read "Revenue (2024)"),
# path data and coordinate lists. Every OTHER allowed attribute goes
# through _svg_css_value_ok as well — not because font-family or
# stop-color can fetch (no browser accepts a URL there; the attributes
# above are the ones that can), but so that no url(https://…) text is
# ever emitted at all and the rule needs no per-property reasoning.
# transform values pass it as they are: rotate(), translate(), … are on
# the function allowlist.
_SVG_NON_CSS_ATTRS = frozenset({
    "id", "class", "role", "aria-label", "aria-labelledby", "aria-hidden",
    "xmlns", "d", "points", "viewbox", "href", "xlink:href",
})

# CSS functions a style or paint value may call. None of them fetches:
# var() reads a page token, the colour and maths functions compute, the
# transform functions move things, and url( is allowed only as a bare
# same-document reference, url(#id) (checked separately below).
# Everything else that opens a parenthesis is refused — image-set(),
# src(), image(), cross-fade(), element(), attr(), paint(),
# expression(), ... — so a fetch that needs no url( token (image-set
# takes a bare string) cannot get through.
_SVG_CSS_ALLOWED_FUNCTIONS = frozenset({
    "var", "url",
    "rgb", "rgba", "hsl", "hsla", "color-mix",
    "calc", "min", "max", "clamp",
    "translate", "translatex", "translatey", "scale", "scalex", "scaley",
    "rotate", "skewx", "skewy", "matrix",
})

_SVG_CSS_BLOCKED_SUBSTRINGS = (
    "@import", "expression(", "javascript:", "behavior:", "-moz-binding",
)


def _svg_css_ident_char(ch: str) -> bool:
    """True if `ch` can be part of a CSS identifier: letters and digits
    of any script, ``-``, ``_``, and every non-ASCII code point (CSS
    Syntax 3, "ident code point"). Deliberately wide, so the run of
    characters before a ``(`` is the WHOLE function name a browser would
    read — ``foo1rgb(`` and ``érgb(`` are not ``rgb(``."""
    return ch.isalnum() or ch in "-_" or ord(ch) > 0x7F


def _svg_css_value_ok(value: str) -> bool:
    """True if `value` is safe to keep as a ``style`` attribute or as a
    paint/reference presentation attribute (fill, stroke, clip-path,
    marker-*). This is the make-or-break allowance for theming —
    ``fill: var(--jm-accent)`` must come through verbatim — so the value
    is never rewritten, only judged, and judged to be refused unless
    every construct in it is one a browser cannot turn into a fetch or
    script:

      * no backslash at all. A browser decodes CSS escapes BEFORE it
        tokenizes, so ``u\\72l(`` is a ``url(`` token; matching on the
        literal text (or on text with escapes deleted) is unsound.
      * no ``/*`` or ``*/`` at all. Comments are not stripped, because
        stripping is unsound too: inside an unquoted ``url(`` token a
        browser reads ``/*`` as URL text, so
        ``url(/*);background:url(https://e.x/p);/*x*/#g)`` strips to
        ``url(#g)`` while the browser fetches ``https://e.x/p``.
      * none of the legacy script/binding markers.
      * every ``(`` is immediately preceded by a function name in
        _SVG_CSS_ALLOWED_FUNCTIONS, judged on the whole identifier;
        ``url(`` must be followed directly by ``#`` (``url(#grad)`` —
        a same-document paint-server reference, which fetches nothing;
        a quoted or space-padded form is refused rather than parsed),
        and ``var(`` must name a custom property (``--``).

    Over-rejection costs one attribute on one element; under-rejection
    is the failure this function exists to prevent."""
    if "\\" in value or "/*" in value or "*/" in value:
        return False
    lowered = value.lower()
    for blocked in _SVG_CSS_BLOCKED_SUBSTRINGS:
        if blocked in lowered:
            return False
    for index, ch in enumerate(lowered):
        if ch != "(":
            continue
        name_start = index
        while name_start > 0 and _svg_css_ident_char(lowered[name_start - 1]):
            name_start -= 1
        name = lowered[name_start:index]
        if name not in _SVG_CSS_ALLOWED_FUNCTIONS:
            return False
        if name == "url" and not lowered.startswith("#", index + 1):
            return False
        if name == "var":
            arg = index + 1
            while arg < len(lowered) and lowered[arg] in " \t\n\r\f":
                arg += 1
            if not lowered.startswith("--", arg):
                return False
    return True


def _svg_fragment_ref(value: str) -> Optional[str]:
    """The value to EMIT for a ``<use href>``: `value` with leading and
    trailing code points <= 0x20 (ASCII whitespace and C0 controls)
    removed and its case preserved (``#Mark`` must keep pointing at
    ``id="Mark"``), or None if that is not a pure same-document
    ``#fragment``. A value with whitespace, a control character or DEL
    INSIDE it is refused rather than repaired: deleting the space in
    ``#a b`` would silently point the reference at a different id.

    The judgement is made on exactly the string that is emitted, which
    is exactly what a browser sees after it decodes the escaped
    attribute. normalize_url is deliberately NOT the judge here: it
    entity-decodes a second time, and for an accept-if-it-starts-with
    test over-decoding errs toward ACCEPTING — ``&amp;#35;a`` would
    normalize to ``#a`` while the browser reads the relative URL
    ``&#35;a`` and fetches it."""
    stripped = value.strip("".join(chr(c) for c in range(0x21)))
    if not stripped.startswith("#"):
        return None
    if any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in stripped):
        return None
    return stripped


# Text-only elements. In a browser <title> and <desc> are HTML
# integration points: their children are parsed as HTML, so a child
# <title /> becomes an HTML <title> (RCDATA — the self-closing slash is
# ignored) and swallows the rest of the PAGE up to the next </title>.
# Nothing html.parser reports can see that, so no element is ever
# emitted inside them; their text is escaped like all other text.
_SVG_TEXT_ONLY_TAGS = frozenset({"title", "desc"})


class _SVGSanitizer(HTMLParser):
    """Allowlist rebuild of one root <svg> element (see the section
    comment above). Mirrors _Sanitizer's discard-until-matching-close
    bookkeeping, but drops every non-allowlisted element WITH its
    content and emits nothing outside the single root."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self.root_seen = False
        self.root_open = False
        # Lowercased names of currently open allowlisted elements; the
        # root <svg> is stack[0]. Close tags pop back to the named
        # element so mis-nested input still yields balanced output.
        self._stack: List[str] = []
        # How many of each name are open, kept in step with _stack, so
        # "is a <g> open?" is one lookup: scanning the stack for every
        # stray close tag made `<g>`*n + `</circle>`*n quadratic.
        self._open_counts = {}  # lowercased tag name -> open count
        # While set, everything (tags and text) is discarded until the
        # matching close tag at the recorded nesting depth.
        self._discard_tag: Optional[str] = None
        self._discard_depth = 0
        # Values of every `id` attribute that was actually emitted, in
        # document order — read from parsed attributes, never from the
        # output text (where <text>id="x"</text> would look like one).
        # render._splice_figures uses them to refuse two figures that
        # define the same id.
        self.ids: List[str] = []

    # -- tag emission --------------------------------------------------

    def _format_tag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]], self_closing: bool
    ) -> str:
        parts = ["<", _SVG_CAMEL_CASE.get(tag, tag)]
        seen = set()
        for name, value in attrs:
            name = name.lower()
            if name.startswith("on"):
                continue  # never, on any element, regardless of allowlists
            if name not in _SVG_ALLOWED_ATTRS:
                continue
            if name in seen:
                # A repeated attribute: a browser keeps the FIRST and
                # ignores the rest, so only the first is judged/emitted
                # (and only its id is recorded).
                continue
            seen.add(name)
            if value is None:
                value = ""
            if name in ("href", "xlink:href"):
                # A reference survives only on <use>, only as a pure
                # #fragment: same-document reuse, never an external
                # fetch or a script-scheme URL. The emitted value is the
                # stripped, case-preserving form (_svg_fragment_ref).
                fragment = _svg_fragment_ref(value) if tag == "use" else None
                if fragment is None:
                    continue
                value = fragment
            elif name not in _SVG_NON_CSS_ATTRS:
                # style, the paint/reference attributes
                # (_SVG_CSS_VALUE_ATTRS) and every other presentation
                # or geometry attribute: see _SVG_NON_CSS_ATTRS.
                if not _svg_css_value_ok(value):
                    continue
            if name == "id":
                self.ids.append(value)
            parts.append(
                " {0}=\"{1}\"".format(
                    _SVG_CAMEL_CASE.get(name, name), html.escape(value, quote=True)
                )
            )
        parts.append(" />" if self_closing else ">")
        return "".join(parts)

    # -- parser callbacks ----------------------------------------------

    def handle_starttag(self, tag, attrs):
        if self._discard_tag is not None:
            if tag == self._discard_tag:
                self._discard_depth += 1
            return
        if not self._stack:
            # Outside the root only <svg> itself may open one.
            if tag != "svg":
                self._discard_tag = tag
                self._discard_depth = 1
                return
            if self.root_seen:
                raise ValueError(
                    "more than one <svg> root element — a figure must be "
                    "exactly one <svg> element"
                )
            self.root_seen = True
            self.root_open = True
        elif tag not in _SVG_ALLOWED_TAGS or self._stack[-1] in _SVG_TEXT_ONLY_TAGS:
            # Not on the allowlist, or a child of a text-only element
            # (title/desc): dropped with its whole subtree.
            self._discard_tag = tag
            self._discard_depth = 1
            return
        self._stack.append(tag)
        self._open_counts[tag] = self._open_counts.get(tag, 0) + 1
        self.out.append(self._format_tag(tag, attrs, self_closing=False))

    def handle_startendtag(self, tag, attrs):
        if self._discard_tag is not None:
            return
        if not self._stack:
            # Outside the root, a self-closing tag carries no content:
            # <svg/> opens (and closes) the root; anything else is
            # stray markup, dropped without entering discard mode.
            if tag != "svg":
                return
            if self.root_seen:
                raise ValueError(
                    "more than one <svg> root element — a figure must be "
                    "exactly one <svg> element"
                )
            self.root_seen = True
            self.out.append(self._format_tag(tag, attrs, self_closing=True))
            return
        if tag not in _SVG_ALLOWED_TAGS or self._stack[-1] in _SVG_TEXT_ONLY_TAGS:
            return
        self.out.append(self._format_tag(tag, attrs, self_closing=True))

    def handle_endtag(self, tag):
        if self._discard_tag is not None:
            if tag == self._discard_tag:
                self._discard_depth -= 1
                if self._discard_depth == 0:
                    self._discard_tag = None
            return
        if not self._open_counts.get(tag):
            return  # stray close tag: nothing open by that name
        while self._stack:
            open_tag = self._stack.pop()
            self._open_counts[open_tag] -= 1
            self.out.append("</{0}>".format(_SVG_CAMEL_CASE.get(open_tag, open_tag)))
            if open_tag == tag:
                break
        if not self._stack:
            self.root_open = False

    def handle_data(self, data):
        if self._discard_tag is not None or not self._stack:
            return  # discarded, or outside the root: dropped
        # convert_charrefs=True delivered this decoded, so one escape
        # round-trips existing entities without double-escaping.
        self.out.append(html.escape(data, quote=False))

    def handle_comment(self, data):
        pass

    def handle_decl(self, decl):
        pass

    def handle_pi(self, data):
        pass

    def unknown_decl(self, data):
        pass


def sanitize_svg_with_ids(svg_text: str) -> Tuple[str, List[str]]:
    """``(sanitized_svg, ids)`` for `svg_text`: the rebuilt markup (see
    sanitize_svg, which returns just that) and the value of every ``id``
    attribute that survived, in document order. The ids come from parsed
    start-tag attributes, never from scanning the output text.

    Raises ValueError when the input has no <svg> root (or nothing
    survives), opens a second <svg> root after the first has closed,
    never closes its root, or is malformed enough that html.parser
    itself raises (some CPython versions raise AssertionError or
    NotImplementedError on input such as ``<![bogus]>``; every such
    failure is reported as ValueError so callers have one error to
    handle). Anything else outside the root — text, other elements,
    whatever follows it — is dropped without an error. An unterminated
    discard-mode element discards the rest of the document, which
    leaves the root unclosed: fail closed, like sanitize_html."""
    parser = _SVGSanitizer()
    try:
        # A browser's HTML parser turns U+0000 into U+FFFD wherever this
        # markup can carry one (attribute values, text in foreign
        # content); html.parser passes it through. Doing the same here
        # first makes both parsers see one document — otherwise
        # id="g\x00" and id="g\ufffd" differ in Python and collide in
        # the page.
        parser.feed(svg_text.replace("\x00", "\ufffd"))
        parser.close()
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001 - any parser failure, by design
        raise ValueError("malformed SVG markup: {0}".format(e)) from e
    if parser.root_open:
        raise ValueError("the <svg> root element is never closed")
    if not parser.root_seen:
        raise ValueError(
            "no <svg> root element found (or nothing survived sanitization)"
        )
    return "".join(parser.out), list(parser.ids)


def sanitize_svg(svg_text: str) -> str:
    """Rebuild `svg_text` keeping only allowlisted SVG elements,
    attributes, CSS values and #fragment references (see the section
    comment above), returning exactly one root ``<svg>`` element, safe
    to inline into an HTML page. Raises ValueError for anything that is
    not one well-formed-enough <svg> root (see sanitize_svg_with_ids for
    the cases); the render pipeline turns that into a ContentError
    naming the figure (render._splice_figures).

    Public contract, relied on outside this module: the output contains
    no element or attribute outside _SVG_ALLOWED_TAGS /
    _SVG_ALLOWED_ATTRS, no ``on*`` attribute, no href except a
    ``#fragment`` on <use>, no element inside <title>/<desc>, and no
    ``style``/paint value that _svg_css_value_ok refuses."""
    return sanitize_svg_with_ids(svg_text)[0]
