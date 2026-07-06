"""CSS assembly and image inlining for self-contained HTML output."""
import base64
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from ._paths import REPO_ROOT
from .design.reader import THEME_NAME_RE
from .errors import ContentError, ManifestError
from .sanitize import is_allowed_image_data_uri, parse_srcset

TOOLKIT_DIR = REPO_ROOT / "toolkit"

# Built-in mode names: `--theme light` / `--theme dark` pin the OS-following
# `[data-theme]` CSS in tokens.css/base.css and never resolve to a file, so
# `_resolve_theme_path` legitimately returns None for them -- that must not
# be treated as an unknown theme (see `assemble_css`). Mirrors
# `design.importer.RESERVED_THEME_NAMES`, which reserves the same two names
# so an imported theme can never collide with them; duplicated rather than
# imported to avoid a cycle (importer.py imports `personal_themes_dir` from
# this module).
_BUILTIN_THEME_MODES = {"light", "dark"}

# Extensions inline_images will read and embed. Raster only, mirroring
# is_allowed_image_data_uri: a local .svg could only ever produce a
# data:image/svg+xml URI that lint rejects anyway (SVG can carry
# markup/script), so it's rejected here, earlier and with a clearer
# message.
_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _attr_re(tag: str, attr: str) -> "re.Pattern":
    """Matches <tag ...attr="..."> / <tag ...attr='...'>. Deliberately
    simple: it covers the single-attribute shape the toolkit macros emit
    (one occurrence, double- or single-quoted, no markup inside the
    quoted value). It will NOT handle a value containing an escaped
    quote of the same kind, nor tags built by string concatenation
    across multiple lines in a way that splits the attribute itself."""
    return re.compile(
        r'(<{tag}\b[^>]*?\s{attr}=)(["\'])(.*?)\2'.format(tag=tag, attr=attr),
        re.IGNORECASE | re.DOTALL,
    )


# Attributes holding a single image URL, and attributes holding a
# srcset-style candidate list. Together these are exactly the
# image-displaying attributes lint's allowlist permits an inlined
# data:image URI on (_IMAGE_DATA_URI_ATTRS); resource attributes with no
# inlineable form (iframe src, object data, link href, ...) are not
# rewritten here, so any URL there survives to lint and errors — which
# is correct: jimemo pages don't embed iframes/objects/stylesheets.
_SINGLE_URL_RES = (
    _attr_re("img", "src"),
    _attr_re("source", "src"),
    _attr_re("video", "poster"),
)
_SRCSET_RES = (
    _attr_re("img", "srcset"),
    _attr_re("source", "srcset"),
)


def personal_themes_dir() -> Path:
    """Where `jimemo import-design` installs a generated theme, and the
    first place `assemble_css` looks when resolving `--theme NAME` (see
    `_resolve_theme_path`). Mirrors discovery.py's `~/.jimemo/templates`
    personal dir; respects a `HOME` override (Path.home() reads it),
    which is what lets tests point this at a temp directory."""
    return Path.home() / ".jimemo" / "themes"


def _theme_search_dirs() -> List[Path]:
    """Directories to look for `<theme>.css` in, in resolution order.
    Personal comes first — unlike discovery.py's template search, where
    the repo copy wins a name collision, a theme a user just imported
    (jimemo.design.importer) should actually take effect even if it
    happens to share a name with a repo theme, since applying the
    import is the entire point of running the command."""
    return [personal_themes_dir(), TOOLKIT_DIR / "themes"]


def _resolve_theme_path(theme: str) -> Optional[Path]:
    """The `<theme>.css` path a `--theme NAME` value resolves to, or None
    if no search dir has one (see `assemble_css`). `theme` is a CLI
    value, so it is validated against `THEME_NAME_RE` -- the same
    lowercase-alnum-and-hyphens shape `design.importer.slugify_name`
    always produces -- before it ever touches a path: unvalidated, a
    name like `../../etc/passwd` or an absolute path would let `--theme`
    read an arbitrary local .css file and inline it into the page.
    Belt-and-braces after that: the resolved candidate must still land
    inside the search dir it came from, in case a future charset change
    ever reintroduces a path separator."""
    if not THEME_NAME_RE.match(theme):
        raise ManifestError(
            f"theme name {theme!r} is not a valid theme name (expected "
            "lowercase letters/digits in hyphen-separated segments, e.g. "
            "'chiba-tech') -- refusing to resolve it to a file"
        )
    for base in _theme_search_dirs():
        base_resolved = base.resolve()
        candidate = (base_resolved / f"{theme}.css").resolve()
        if candidate.is_relative_to(base_resolved) and candidate.is_file():
            return candidate
    return None


def _available_theme_names() -> List[str]:
    """Every theme name currently resolvable via `_theme_search_dirs`,
    sorted and de-duplicated, for the unknown-theme error message (see
    `assemble_css`). Empty if neither search dir exists or has any
    `*.css` file yet."""
    names = set()
    for base in _theme_search_dirs():
        if base.is_dir():
            names.update(css_path.stem for css_path in base.glob("*.css"))
    return sorted(names)


def assemble_css(manifest: Dict[str, Any], theme: Optional[str] = None) -> str:
    """tokens.css + base.css + only the components the manifest lists
    (+ a <theme>.css override, if one resolves — see `_resolve_theme_path`:
    ~/.jimemo/themes/ is checked before the repo's toolkit/themes/) +
    print-force.css, always last.

    print-force.css is re-appended unconditionally, after the theme, even
    though base.css already contains the same rules: a theme file is free
    to redefine `:root` at the same specificity as base.css's print block,
    and CSS breaks specificity ties by source order, so an unguarded theme
    `:root` rule occurring after base.css in the assembly would otherwise
    win over the print force and leak screen colors into print output. See
    toolkit/print-force.css's own header comment for the full explanation.

    Raises ManifestError if `theme` is neither a built-in mode name
    (`_BUILTIN_THEME_MODES`, which never resolve to a file by design) nor
    a name `_resolve_theme_path` can find a file for — otherwise a typo'd
    or never-imported theme would render successfully but unthemed, with
    a `data-theme` attribute matching nothing, and no error to say why.
    """
    parts = [
        (TOOLKIT_DIR / "tokens.css").read_text(encoding="utf-8"),
        (TOOLKIT_DIR / "base.css").read_text(encoding="utf-8"),
    ]
    for name in manifest.get("components", []):
        css_path = TOOLKIT_DIR / "components" / f"{name}.css"
        if not css_path.is_file():
            raise ManifestError(f"manifest lists unknown component {name!r} ({css_path})")
        parts.append(css_path.read_text(encoding="utf-8"))
    if theme:
        theme_path = _resolve_theme_path(theme)
        if theme_path is not None:
            parts.append(theme_path.read_text(encoding="utf-8"))
        elif theme not in _BUILTIN_THEME_MODES:
            available = _available_theme_names()
            hint = f" (available: {', '.join(available)})" if available else ""
            raise ManifestError(
                f"unknown theme {theme!r} (not found in repo themes/ or "
                f"~/.jimemo/themes/; import one with `jimemo import-design`)"
                f"{hint}"
            )
    parts.append((TOOLKIT_DIR / "print-force.css").read_text(encoding="utf-8"))
    return "\n".join(parts)


def _is_remote(src: str) -> bool:
    return urlsplit(src).scheme in ("http", "https")


def inline_images(html: str, base_dir: Path) -> Tuple[str, List[str]]:
    """Rewrite local image paths to data URIs on every image-displaying
    attribute: <img src>, <img srcset>, <source src>, <source srcset>
    and <video poster>. srcset values are rewritten per candidate
    (parse_srcset — the same splitting lint validates with). Remote
    (http/https) sources are left alone with a warning (lint rejects
    them separately); pure #fragment references are left alone (they
    never fetch). Missing local files are collected and raised together
    as a ContentError.

    A ``data:`` value is passed through unchanged, except a disallowed
    subtype (``svg+xml``, non-raster, or a non-image ``data:`` URI
    entirely) is rejected here too — defense in depth ahead of lint,
    which remains the authoritative gate for slot values written
    straight into a template's attribute (bypassing markdown
    sanitization, and possibly this function's regexes, entirely).

    Content may be untrusted, and this function reads local files, so it
    fails closed on any path that could leak a file from outside the
    content's own directory: absolute paths, paths whose resolved real
    location (after ``..`` segments and symlinks) escapes `base_dir`'s
    subtree, and non-image extensions all raise ContentError naming the
    offending value — they are never silently skipped. The same rules
    apply to every attribute and every srcset candidate."""
    base_dir = Path(base_dir).resolve()
    warnings: List[str] = []
    missing: List[str] = []
    rejected: List[str] = []

    def localize(url: str) -> Optional[str]:
        """data: URI replacement for a local image path, or None to keep
        `url` as written (empty/fragment/data:/remote — lint judges what
        survives)."""
        if not url or url.startswith("#"):
            return None
        if url.startswith("data:"):
            if not is_allowed_image_data_uri(url):
                rejected.append(f"{url} (not an allowed image data URI)")
            return None
        if _is_remote(url):
            warnings.append(f"external image not inlined: {url}")
            return None

        src_path = Path(url)
        if src_path.is_absolute():
            rejected.append(f"{url} (absolute path)")
            return None
        img_path = (base_dir / src_path).resolve()
        if not img_path.is_relative_to(base_dir):
            rejected.append(f"{url} (escapes the content file's directory)")
            return None
        if img_path.suffix.lower() not in _MIME_BY_EXT:
            rejected.append(
                f"{url} (extension {img_path.suffix!r} is not an allowed "
                f"image type: {', '.join(sorted(_MIME_BY_EXT))})"
            )
            return None
        if not img_path.is_file():
            missing.append(url)
            return None

        mime = _MIME_BY_EXT[img_path.suffix.lower()]
        data = base64.b64encode(img_path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{data}"

    def replace_single(match: "re.Match") -> str:
        prefix, quote, url = match.group(1), match.group(2), match.group(3)
        new_url = localize(url)
        if new_url is None:
            return match.group(0)
        return f"{prefix}{quote}{new_url}{quote}"

    def replace_srcset(match: "re.Match") -> str:
        prefix, quote, value = match.group(1), match.group(2), match.group(3)
        rebuilt: List[str] = []
        changed = False
        for url, descriptor in parse_srcset(value):
            new_url = localize(url)
            if new_url is not None:
                url, changed = new_url, True
            rebuilt.append(f"{url} {descriptor}" if descriptor else url)
        if not changed:
            return match.group(0)  # keep the author's formatting untouched
        return prefix + quote + ", ".join(rebuilt) + quote

    for pattern in _SINGLE_URL_RES:
        html = pattern.sub(replace_single, html)
    for pattern in _SRCSET_RES:
        html = pattern.sub(replace_srcset, html)

    if rejected:
        raise ContentError(
            "unsafe local image path(s) in image attributes: " + "; ".join(rejected)
        )
    if missing:
        raise ContentError(
            "missing local image(s): " + ", ".join(missing)
        )

    return html, warnings
