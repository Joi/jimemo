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
# allowlist parser in the same spirit as _Sanitizer, with two
# SVG-specific rules: an element outside the allowlist is dropped WITH
# its whole subtree (inside an SVG there is no prose to unwrap — every
# unrecognized element is assumed payload), and the result must be
# exactly one root <svg> element or the input is refused outright.
# Nothing here touches the markdown path above: new names only.

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

# Attributes whose VALUE is a CSS paint/reference and may therefore
# carry a url(...) reference: fill, stroke, clip-path, marker-start,
# marker-mid, marker-end. Only url(#fragment) survives there.
_SVG_URL_VALUE_ATTRS = frozenset({
    "fill", "stroke", "clip-path", "marker-start", "marker-mid", "marker-end",
})

_SVG_URL_OPEN_RE = re.compile(r"url\(", re.IGNORECASE)
_SVG_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
# A CSS escape: backslash + 1-6 hex digits + one optional whitespace,
# or backslash + any other single character (identity escape).
_SVG_CSS_ESCAPE_RE = re.compile(
    r"\\(?:[0-9a-fA-F]{1,6}[ \t\r\n\f]?|.)", re.DOTALL
)
_SVG_STYLE_BLOCKED_SUBSTRINGS = (
    "@import", "expression(", "javascript:", "behavior:", "-moz-binding",
)


def _svg_url_value_ok(value: str) -> bool:
    """True if every ``url(`` in `value` (case-insensitive) is followed
    by ``#`` — a same-document fragment/paint-server reference such as
    ``url(#grad)``, which fetches nothing. Any other url( target is a
    resource the browser would fetch at view time, so the attribute is
    refused wholesale."""
    for match in _SVG_URL_OPEN_RE.finditer(value):
        if value[match.end():match.end() + 1] != "#":
            return False
    return True


def _svg_style_ok(value: str) -> bool:
    """True if `value` is safe to keep as a ``style`` attribute — the
    make-or-break allowance for theming, since ``fill: var(--jm-*)``
    must come through verbatim. Judged on the lowercased value with CSS
    comments and backslash escapes REMOVED, so neither can hide a
    forbidden token; removal can only over-reject (a deleted escape
    never introduces characters that were not literally present, and a
    browser decodes e.g. ``url\\28...\\29`` to an identifier, not a
    url( function token). Blocked: @import, expression(, javascript:,
    behavior:, -moz-binding, and any url( whose target is not a
    ``#fragment`` (see _svg_url_value_ok)."""
    reduced = _SVG_CSS_ESCAPE_RE.sub(
        "", _SVG_CSS_COMMENT_RE.sub("", value.lower())
    )
    for blocked in _SVG_STYLE_BLOCKED_SUBSTRINGS:
        if blocked in reduced:
            return False
    return _svg_url_value_ok(reduced)


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
        # While set, everything (tags and text) is discarded until the
        # matching close tag at the recorded nesting depth.
        self._discard_tag: Optional[str] = None
        self._discard_depth = 0

    # -- tag emission --------------------------------------------------

    def _format_tag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]], self_closing: bool
    ) -> str:
        parts = ["<", _SVG_CAMEL_CASE.get(tag, tag)]
        for name, value in attrs:
            name = name.lower()
            if name.startswith("on"):
                continue  # never, on any element, regardless of allowlists
            if name not in _SVG_ALLOWED_ATTRS:
                continue
            if value is None:
                value = ""
            if name in ("href", "xlink:href"):
                # A reference survives only on <use>, only as a pure
                # #fragment after entity decoding and whitespace/
                # control stripping (normalize_url over-decodes only in
                # the reject direction): same-document reuse, never an
                # external fetch or a script-scheme URL.
                if tag != "use" or not normalize_url(value).startswith("#"):
                    continue
            elif name == "style":
                if not _svg_style_ok(value):
                    continue
            elif name in _SVG_URL_VALUE_ATTRS:
                if not _svg_url_value_ok(value):
                    continue
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
        elif tag not in _SVG_ALLOWED_TAGS:
            self._discard_tag = tag
            self._discard_depth = 1
            return
        self._stack.append(tag)
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
        if tag not in _SVG_ALLOWED_TAGS:
            return
        self.out.append(self._format_tag(tag, attrs, self_closing=True))

    def handle_endtag(self, tag):
        if self._discard_tag is not None:
            if tag == self._discard_tag:
                self._discard_depth -= 1
                if self._discard_depth == 0:
                    self._discard_tag = None
            return
        if tag not in self._stack:
            return  # stray close tag: nothing open by that name
        while self._stack:
            open_tag = self._stack.pop()
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


def sanitize_svg(svg_text: str) -> str:
    """Rebuild `svg_text` keeping only allowlisted SVG elements,
    attributes, and #fragment URL references (see the section comment
    above), returning exactly one root ``<svg>`` element. Raises
    ValueError when the input has no <svg> root (or nothing survives
    sanitization), carries more than one root element, or never closes
    its root — the render pipeline turns those into a ContentError
    naming the figure (render._splice_figures). An unterminated
    discard-mode element discards the rest of the document, failing
    closed like sanitize_html."""
    parser = _SVGSanitizer()
    parser.feed(svg_text)
    parser.close()
    if parser.root_open:
        raise ValueError("the <svg> root element is never closed")
    if not parser.root_seen:
        raise ValueError(
            "no <svg> root element found (or nothing survived sanitization)"
        )
    return "".join(parser.out)
