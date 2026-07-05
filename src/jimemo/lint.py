"""Static checks on rendered HTML output.

Errors block writing the output file; warnings are advisory (printed to
stderr by the render pipeline, output still written).
"""
import re
from typing import Any, Dict, List, Tuple

_SCRIPT_TAG_RE = re.compile(r"<script\b", re.IGNORECASE)
# Last-gate tripwires on the assembled page. The markdown sanitizer
# (sanitize.py) should make these unreachable for slot content; they
# exist so a template-authored handler or script-scheme URL fails
# closed too.
_EVENT_HANDLER_RE = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
_SCRIPT_SCHEME_RE = re.compile(r"(javascript|vbscript):", re.IGNORECASE)
_SCRIPT_REMOTE_SRC_RE = re.compile(
    r'<script\b[^>]*\ssrc=["\'](https?://[^"\']*)', re.IGNORECASE
)
_IMG_REMOTE_RE = re.compile(r'<img\b[^>]*\ssrc=["\'](https?://[^"\']*)', re.IGNORECASE)
_LINK_REMOTE_RE = re.compile(r'<link\b[^>]*\shref=["\'](https?://[^"\']*)', re.IGNORECASE)

MAX_OUTPUT_BYTES = 8_000_000


def lint_html(html: str, manifest: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    for m in _SCRIPT_REMOTE_SRC_RE.finditer(html):
        errors.append(f"external <script src=\"{m.group(1)}\"> is never allowed")

    if not manifest.get("charts") and _SCRIPT_TAG_RE.search(html):
        errors.append(
            "<script> tag found but this template declares no charts "
            "(manifest 'charts' is empty)"
        )

    for m in _EVENT_HANDLER_RE.finditer(html):
        errors.append(
            f"inline event handler found ({m.group(0).strip()!r}) — "
            "on* attributes are never allowed"
        )

    for m in _SCRIPT_SCHEME_RE.finditer(html):
        errors.append(f"{m.group(1).lower()}: URI found — script-scheme URLs are never allowed")

    for m in _IMG_REMOTE_RE.finditer(html):
        warnings.append(f"external image not inlined: {m.group(1)}")

    for m in _LINK_REMOTE_RE.finditer(html):
        warnings.append(f"external stylesheet reference: {m.group(1)}")

    size = len(html.encode("utf-8"))
    if size > MAX_OUTPUT_BYTES:
        warnings.append(f"output is {size} bytes, over the {MAX_OUTPUT_BYTES}-byte guideline")

    return errors, warnings
