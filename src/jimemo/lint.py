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

from .sanitize import url_scheme

MAX_OUTPUT_BYTES = 8_000_000

# Attributes whose values are URLs the browser may act on.
_URL_ATTRS = frozenset({"href", "src", "action", "formaction", "xlink:href"})


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
            elif scheme in ("http", "https"):
                # Output must be self-contained: nothing may fetch at
                # view time. <a href> is fine (fetches only on click);
                # img/link fetch on load, so a surviving remote URL there
                # is a hard error (inline_images already converted every
                # legitimate local image to a data: URI).
                if tag == "img" and name == "src":
                    self.errors.append(
                        f"external image {value!r} would fetch at view time — "
                        "use a local file so it can be inlined"
                    )
                elif tag == "link" and name == "href":
                    self.errors.append(
                        f"external <link href={value!r}> would fetch at view "
                        "time — external stylesheets/resources are never allowed"
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
