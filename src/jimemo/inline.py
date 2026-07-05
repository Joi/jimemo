"""CSS assembly and image inlining for self-contained HTML output."""
import base64
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from ._paths import REPO_ROOT
from .errors import ContentError

TOOLKIT_DIR = REPO_ROOT / "toolkit"

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}

# Matches <img ...src="..."> / <img ...src='...'>. Deliberately simple:
# it covers the single <img src=...> shape the toolkit macros emit (one
# src attribute, double- or single-quoted, no markup inside the quoted
# value). It will NOT handle a src value containing an escaped quote of
# the same kind, nor <img> tags built by string concatenation across
# multiple lines in a way that splits the src attribute itself.
_IMG_SRC_RE = re.compile(r'(<img\b[^>]*?\ssrc=)(["\'])(.*?)\2', re.IGNORECASE | re.DOTALL)


def assemble_css(manifest: Dict[str, Any], theme: Optional[str] = None) -> str:
    """tokens.css + base.css + only the components the manifest lists
    (+ a toolkit/themes/<theme>.css override, if one exists) + print-force.css,
    always last.

    print-force.css is re-appended unconditionally, after the theme, even
    though base.css already contains the same rules: a theme file is free
    to redefine `:root` at the same specificity as base.css's print block,
    and CSS breaks specificity ties by source order, so an unguarded theme
    `:root` rule occurring after base.css in the assembly would otherwise
    win over the print force and leak screen colors into print output. See
    toolkit/print-force.css's own header comment for the full explanation.
    """
    parts = [
        (TOOLKIT_DIR / "tokens.css").read_text(encoding="utf-8"),
        (TOOLKIT_DIR / "base.css").read_text(encoding="utf-8"),
    ]
    for name in manifest.get("components", []):
        css_path = TOOLKIT_DIR / "components" / f"{name}.css"
        if not css_path.is_file():
            raise ContentError(f"manifest lists unknown component {name!r} ({css_path})")
        parts.append(css_path.read_text(encoding="utf-8"))
    if theme:
        theme_path = TOOLKIT_DIR / "themes" / f"{theme}.css"
        if theme_path.is_file():
            parts.append(theme_path.read_text(encoding="utf-8"))
    parts.append((TOOLKIT_DIR / "print-force.css").read_text(encoding="utf-8"))
    return "\n".join(parts)


def _is_remote(src: str) -> bool:
    return urlsplit(src).scheme in ("http", "https")


def inline_images(html: str, base_dir: Path) -> Tuple[str, List[str]]:
    """Rewrite local <img src> paths to data URIs. Remote (http/https)
    sources are left alone (lint warns about them separately). Missing
    local files are collected and raised together as a ContentError."""
    base_dir = Path(base_dir)
    warnings: List[str] = []
    missing: List[str] = []

    def replace(match: "re.Match") -> str:
        prefix, quote, src = match.group(1), match.group(2), match.group(3)
        if not src or src.startswith("data:"):
            return match.group(0)
        if _is_remote(src):
            warnings.append(f"external image not inlined: {src}")
            return match.group(0)

        src_path = Path(src)
        img_path = src_path if src_path.is_absolute() else (base_dir / src_path)
        if not img_path.is_file():
            missing.append(src)
            return match.group(0)

        mime = _MIME_BY_EXT.get(img_path.suffix.lower(), "application/octet-stream")
        data = base64.b64encode(img_path.read_bytes()).decode("ascii")
        return f"{prefix}{quote}data:{mime};base64,{data}{quote}"

    new_html = _IMG_SRC_RE.sub(replace, html)

    if missing:
        raise ContentError(
            "missing local image(s) referenced by <img src>: " + ", ".join(missing)
        )

    return new_html, warnings
