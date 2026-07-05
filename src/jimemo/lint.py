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
there is legal in exactly two forms:

  * an inlined raster ``data:image/{png,jpeg,jpg,gif,webp}`` URI, and
    only on an attribute that displays an image (_IMAGE_DATA_URI_ATTRS
    — what inline_images produces); or
  * a pure ``#fragment``, which never fetches.

Everything else is an error: remote http(s), protocol-relative
``//host/...``, any other scheme, non-raster or non-image ``data:``
payloads, ``data:`` anywhere outside an image attribute, empty values,
and surviving bare local paths — a local path that reached the final
HTML means a sidecar-file dependency inline_images could not localize,
so the output is not self-contained. Enumerating bad forms is a losing
game; anything not explicitly allowed fails closed.

Separate from the fetch allowlist, execution checks remain: ``on*``
attributes and ``javascript:``/``vbscript:`` URLs are never allowed
anywhere, ``<script src>`` is never allowed, ``<script>`` requires the
manifest to declare charts, and ``<meta http-equiv="refresh">`` (a
navigation at view time) is never allowed.
"""
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

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

# Tag -> attribute(s) the browser fetches automatically at page-load
# time, as opposed to e.g. <a href>, which fetches only on click.
# Deliberately inclusive: legacy (frame, body/table/cell background,
# html manifest), experimental (portal), the <image> alias the HTML
# parser rewrites to <img>, and SVG's fetching elements (use, image) are
# all listed even though jimemo never emits them — an unlisted vector is
# an unchecked vector, and a false listing costs nothing on pages that
# don't use the tag. srcset-family attributes hold multiple candidates
# and are validated per candidate URL.
_FETCH_ON_LOAD_ATTRS: Dict[str, Tuple[str, ...]] = {
    "audio": ("src",),
    "body": ("background",),
    "embed": ("src",),
    "frame": ("src",),
    "html": ("manifest",),
    "iframe": ("src",),
    "image": ("src", "srcset", "href", "xlink:href"),
    "img": ("src", "srcset"),
    "input": ("src",),
    "link": ("href", "imagesrcset"),
    "object": ("data",),
    "portal": ("src",),
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
# (iframe src, object data, link href, embed src, audio/video/track
# src, ...) NO data: URI is allowed at all: those attributes embed or
# apply their target as markup, style, or plugin content, not pixels.
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


class _Linter(HTMLParser):
    def __init__(self, charts_declared: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.charts_declared = charts_declared
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def handle_starttag(self, tag, attrs):
        self._check_tag(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._check_tag(tag, attrs)

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

        if tag == "meta":
            http_equiv = next(
                (v for n, v in attrs if n.lower() == "http-equiv"), None
            )
            if http_equiv is not None and normalize_url(http_equiv) == "refresh":
                self.errors.append(
                    '<meta http-equiv="refresh"> is never allowed — it '
                    "navigates away from the page at view time"
                )

        for name, value in attrs:
            name = name.lower()
            if name.startswith("on"):
                self.errors.append(
                    f"inline event handler found ({name!r} on <{tag}>) — "
                    "on* attributes are never allowed"
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
        as a raster image data: URI on an image-displaying attribute, or
        as a pure #fragment. Everything else appends an error naming the
        tag, attribute, value, and reason."""
        shown = value if len(value) <= _MAX_URL_IN_MESSAGE else (
            value[:_MAX_URL_IN_MESSAGE] + "..."
        )
        where = f"<{tag} {attr_name}={shown!r}>"

        # Allowances are judged in the browser-faithful form (see
        # browser_url_form): the over-normalized form may read "#x" or
        # "data:..." out of a value the browser would actually treat as
        # a relative path and fetch.
        if browser_url_form(value).startswith("#"):
            return  # pure fragment: never fetches

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
                "(image attributes only) or a #fragment is"
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


def lint_html(html: str, manifest: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    linter = _Linter(charts_declared=bool(manifest.get("charts")))
    linter.feed(html)
    linter.close()

    errors, warnings = linter.errors, linter.warnings

    size = len(html.encode("utf-8"))
    if size > MAX_OUTPUT_BYTES:
        warnings.append(f"output is {size} bytes, over the {MAX_OUTPUT_BYTES}-byte guideline")

    return errors, warnings
