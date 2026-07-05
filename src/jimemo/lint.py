"""Static checks on rendered HTML output.

Errors block writing the output file; warnings are advisory (printed to
stderr by the render pipeline, output still written).

Checks run at structural positions only — real tags and attributes found
by parsing the document (stdlib html.parser, same approach as
sanitize.py) — never on escaped text. Prose like "phase one = done" or
"never use javascript: URLs" is inert once escaped and must not block a
render; only an actual event-handler attribute, script tag, or
script-scheme/remote URL in a live attribute counts. These are last-gate
tripwires on the assembled page: the markdown sanitizer (sanitize.py)
should make them unreachable for slot content; they exist so a
template-authored handler or script-scheme URL fails closed too.
"""
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

from .sanitize import is_allowed_image_data_uri, is_protocol_relative, url_scheme

MAX_OUTPUT_BYTES = 8_000_000

# Attributes whose values are URLs the browser may act on.
_URL_ATTRS = frozenset({"href", "src", "action", "formaction", "xlink:href"})

# Tag -> attribute(s) the browser fetches automatically at page-load time,
# as opposed to <a href>, which fetches only on click. A surviving remote
# URL in any of these is a hard error: output must be fully self-contained
# at view time, and inline_images() only ever localizes <img> (src and
# srcset), so nothing else could have been made local automatically.
# Markdown-authored content never reaches most of these -- sanitize.py
# discards iframe/object/embed outright and unwraps video/audio/source/
# track (tag stripped, children kept) -- so this guards template-authored
# markup and text/data-slot values written straight into a macro's
# attribute, which bypass markdown sanitization entirely.
_FETCH_ON_LOAD_ATTRS: Dict[str, Tuple[str, ...]] = {
    "img": ("src", "srcset"),
    "link": ("href",),
    "iframe": ("src",),
    "embed": ("src",),
    "object": ("data",),
    "source": ("src", "srcset"),
    "video": ("src",),
    "audio": ("src",),
    "track": ("src",),
}


def _srcset_urls(value: str) -> List[str]:
    """URL candidates out of a ``srcset`` value: a comma-separated list of
    ``<url> <descriptor>?`` entries. Only the URL (the first whitespace-
    delimited token of each candidate) is returned; width/density
    descriptors like ``2x`` or ``600w`` are irrelevant to this check."""
    urls = []
    for candidate in value.split(","):
        tokens = candidate.split()
        if tokens:
            urls.append(tokens[0])
    return urls


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
            value = next((v for n, v in attrs if n.lower() == attr_name), None)
            if value is None:
                continue
            candidates = _srcset_urls(value) if attr_name == "srcset" else [value]
            for candidate in candidates:
                self._check_fetch_on_load_url(tag, attr_name, candidate)

    def _check_fetch_on_load_url(self, tag: str, attr_name: str, value: str) -> None:
        # Output must be self-contained: nothing may fetch at view time.
        # <a href> is fine (fetches only on click); the tags/attrs in
        # _FETCH_ON_LOAD_ATTRS fetch on load, so a surviving remote URL
        # there is a hard error (inline_images already converted every
        # legitimate local image to a data: URI). A protocol-relative URL
        # (``//host/x``) has no scheme but is just as much a fetch — the
        # browser resolves it against the page's own scheme.
        scheme = url_scheme(value)
        if tag == "img" and attr_name == "src" and scheme == "data":
            if not is_allowed_image_data_uri(value):
                self.errors.append(
                    f"<img src={value!r}> is not an allowed image "
                    "data URI — only data:image/{png,jpeg,jpg,gif,webp} "
                    "are permitted (svg+xml can carry markup; other "
                    "data: subtypes aren't images at all)"
                )
            return
        if scheme in ("http", "https") or is_protocol_relative(value):
            self.errors.append(
                f"external <{tag} {attr_name}={value!r}> would fetch at view "
                "time — use a local file so the output stays self-contained"
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
