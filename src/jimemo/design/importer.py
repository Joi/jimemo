"""`jimemo import-design` orchestration.

Ties `reader.read_export` (parse-only) and `mapping.build_theme`
(deterministic token->role mapping) together into
one call that installs the result as a jimemo theme under
`~/.jimemo/themes/<name>.css` — the personal directory `inline.py`'s
`assemble_css` already checks (ahead of the repo's toolkit/themes/) when
resolving `--theme NAME`.

Fonts are family-name-only by default: the mapped theme already sets
`--jm-font-prose`/`--jm-font-ui` to `"<family>", <fallback stack>`
(mapping.py), which renders correctly only if that family happens to be
installed on the viewer's machine — no font bytes are read or embedded
unless `embed_fonts=True` is passed, in which case each font file the
manifest lists is read, base64-encoded, and appended as an `@font-face`
rule with a `data:font/...` `src`. That embedding step is intentionally
separate from `build_theme`: it operates on font FILES (binary, on
disk), which are a different trust/licensing concern from the token
VALUES `build_theme` already validated, and it is the one part of this
module that reads bytes outside the manifest/CSS text `reader.py`
parses.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from ..errors import DesignImportError
from ..inline import personal_themes_dir
from ..lint import css_reference_errors
from .mapping import build_theme, theme_structure_errors
from .reader import (
    THEME_NAME_RE,
    DesignExport,
    FontFace,
    invalid_theme_name_reason,
    read_export,
)

__all__ = [
    "ImportResult",
    "import_design",
    "slugify_name",
    "design_systems_dir",
    "resolve_from_name",
]


# The toolkit's own `:root[data-theme="light"]` / `[data-theme="dark"]`
# mode selectors (specificity 0-2-0) beat a generated theme's `:root`
# block (0-1-0), so a theme installed under either of these names would
# load but have its core role overrides silently overridden by the
# built-in mode tokens. Reserved regardless of case or how the name was
# derived (explicit --name or the export's own namespace/dirname).
RESERVED_THEME_NAMES = {"light", "dark"}

_SLUG_COLLAPSE_RE = re.compile(r"[^a-z0-9]+")


def slugify_name(raw: str) -> str:
    """`raw` lowercased with every run of non-alphanumeric characters
    collapsed to a single hyphen, and leading/trailing hyphens trimmed —
    e.g. ``"NorthwindFieldKit_7b3f21"`` -> ``"northwindfieldkit-7b3f21"``.
    Used only for the DEFAULT name derived from the export's namespace or
    directory name (`_default_name`) when the caller passed no `--name`,
    so that default is always a well-formed CSS-file-safe,
    `--theme`-typeable identifier regardless of what the export
    declared. An explicit, user-typed `--name` is deliberately NOT
    slugified — `import_design` validates it as-is against the same
    shape (`invalid_theme_name_reason`) `render --theme` enforces, and
    rejects it outright rather than silently transforming it, so a name
    import accepts is always a name render accepts too."""
    slug = _SLUG_COLLAPSE_RE.sub("-", raw.strip().lower()).strip("-")
    if not slug:
        raise DesignImportError(
            f"cannot derive a theme name from {raw!r} -- pass --name explicitly"
        )
    return slug


# Font file extensions this module will read for --embed-fonts, and the
# `format()` hint / mime each maps to in the generated @font-face. Only
# real binary font formats -- never .js/.jsx/.ts, matching reader.py's
# parse-only guarantee for the export at large.
_FONT_EXT_INFO = {
    ".ttf": ("font/ttf", "truetype"),
    ".otf": ("font/otf", "opentype"),
    ".woff": ("font/woff", "woff"),
    ".woff2": ("font/woff2", "woff2"),
}


@dataclass
class ImportResult:
    name: str
    theme_path: Path
    css: str
    header: str
    embedded_font_families: List[str] = field(default_factory=list)
    embedded_bytes: int = 0


def design_systems_dir() -> Path:
    """The conventional home for a personal collection of design
    exports (e.g. a private repo of Claude-design exports cloned
    here) that `--from NAME` resolves against. Respects a `HOME`
    override the same way `personal_themes_dir` does (`Path.home()`
    reads it), so tests can point it at a tmp dir. jimemo never
    creates, clones into, or otherwise manages this directory -- it
    only reads from `<design_systems_dir()>/NAME` if the caller
    points `--from` there."""
    return Path.home() / ".jimemo" / "design-systems"


def resolve_from_name(name: str) -> Path:
    """Resolve `--from NAME` to `<design_systems_dir()>/NAME`.

    `name` is validated against `THEME_NAME_RE` -- the same
    lowercase-alnum-and-single-hyphens slug shape a theme name must
    match -- before it ever touches a path. Without that check a
    hostile `--from ../../etc` (or an absolute path smuggled in as a
    "name") would escape `design_systems_dir()` the same way an
    unvalidated `--theme` value would escape `personal_themes_dir()`
    (see `inline.py`'s resolution of `--theme`). Raises
    DesignImportError if the name doesn't validate or the resulting
    directory doesn't exist -- the caller (a friend who hasn't cloned
    their design-systems repo yet, or mistyped a name) needs to know
    exactly what path was expected."""
    if not THEME_NAME_RE.match(name):
        raise DesignImportError(
            f"--from name {name!r} is not a valid slug (expected lowercase "
            f"letters, digits, and single hyphens, e.g. 'northwind-tech') -- "
            f"refusing to resolve it against {design_systems_dir()}"
        )
    candidate = design_systems_dir() / name
    if not candidate.is_dir():
        raise DesignImportError(
            f"no design system named {name!r} at {candidate} -- clone your "
            f"private design-systems repo there (e.g. "
            f"'git clone <your-repo> {design_systems_dir()}'), or pass an "
            f"export directory path directly instead of --from"
        )
    return candidate


def _default_name(export: DesignExport, export_dir: Path) -> str:
    """The export's own namespace, if it declared one (the manifest
    path always does; the CSS-fallback path never does), else the
    export directory's own name -- both slugified so the result is
    always a valid theme file stem."""
    return slugify_name(export.namespace or export_dir.name)


def _theme_header(css: str) -> str:
    """The leading `/* ... */` comment block `build_theme` emits (what
    got auto-mapped, what needs review), for printing to the user --
    everything in `css` before the `:root {` it always emits after that
    comment. Falls back to the whole string if the shape is ever
    unrecognized (never expected from build_theme's own output, but the
    CLI display path should not itself crash on it)."""
    idx = css.find(":root")
    return css[:idx].strip() if idx != -1 else css.strip()


def _resolve_font_file(export_dir: Path, rel_path: str) -> Path:
    """`export_dir / rel_path`, refusing anything that isn't a real
    font file confined to the export directory: an absolute path or a
    `..` escape (a hostile manifest's `fonts[].files` entry, same
    threat model as reader.py's token values) is rejected before the
    file is ever opened, and so is any extension outside
    `_FONT_EXT_INFO` -- this is the one place in the design import path
    that reads bytes rather than text, so it is the one place that
    needs a path-traversal check."""
    try:
        export_root = export_dir.resolve()
        candidate = export_root / rel_path
        resolved = candidate.resolve()
    except (OSError, ValueError, RuntimeError) as e:
        # A hostile export can make resolve() itself fail rather than
        # just landing outside export_root -- e.g. a symlink LOOP
        # (a -> b -> a) raises RuntimeError ("Symlink loop") on some
        # platforms, not OSError, which would otherwise bypass this
        # function's DesignImportError contract and surface as a raw
        # traceback under --embed-fonts. Fail closed on any of these.
        raise DesignImportError(
            f"cannot resolve font path {rel_path!r}: {e}"
        ) from e
    if not resolved.is_relative_to(export_root):
        raise DesignImportError(
            f"font file {rel_path!r} escapes the export directory "
            f"{export_root} -- refusing to read it"
        )
    if resolved.suffix.lower() not in _FONT_EXT_INFO:
        raise DesignImportError(
            f"font file {rel_path!r} has an unrecognized extension "
            f"(expected one of {', '.join(sorted(_FONT_EXT_INFO))})"
        )
    if not resolved.is_file():
        raise DesignImportError(f"font file not found: {resolved}")
    return resolved


def _font_face_block(font: FontFace, export_dir: Path) -> "tuple[str, int]":
    """One `@font-face` rule per file `font` lists (a family commonly
    has separate regular/italic/bold files, each its own rule), and the
    total bytes read (for the CLI's size warning). Returns `("", 0)` for
    a font with no files to embed (family-only entries some exports
    carry alongside real ones)."""
    blocks: List[str] = []
    total_bytes = 0
    for rel_path in font.files:
        path = _resolve_font_file(export_dir, rel_path)
        try:
            data = path.read_bytes()
        except OSError as e:
            raise DesignImportError(f"could not read font file {path}: {e}") from e
        total_bytes += len(data)
        mime, fmt = _FONT_EXT_INFO[path.suffix.lower()]
        b64 = base64.b64encode(data).decode("ascii")
        blocks.append(
            "@font-face {{\n"
            '  font-family: "{family}";\n'
            "  font-weight: {weight};\n"
            "  font-style: {style};\n"
            '  src: url(data:{mime};base64,{b64}) format("{fmt}");\n'
            "}}".format(
                family=font.family,
                weight=font.weight or "normal",
                style=font.style or "normal",
                mime=mime,
                b64=b64,
                fmt=fmt,
            )
        )
    return "\n".join(blocks), total_bytes


def _embed_fonts(css: str, export: DesignExport, export_dir: Path) -> "tuple[str, List[str], int]":
    """`css` with one `@font-face` block appended per font file the
    export lists, plus the family names embedded and total bytes (for
    the CLI's size/licensing warning). Re-validates the result against
    the same self-contained-CSS check `build_theme` already ran, since
    an embedded font is new content `build_theme` never saw -- this is
    defense in depth, not expected to ever fire (every appended `url()`
    is a data:font URI `lint.css_reference_errors` allows), but a
    silent hole here would ship the exact resource-loading risk this
    whole pipeline exists to prevent."""
    blocks: List[str] = []
    families: List[str] = []
    total_bytes = 0
    for font in export.fonts:
        if not font.files:
            continue
        block, nbytes = _font_face_block(font, export_dir)
        if not block:
            continue
        blocks.append(block)
        families.append(font.family)
        total_bytes += nbytes

    if not blocks:
        return css, families, total_bytes

    embedded_css = (
        css.rstrip("\n")
        + "\n\n/* -- embedded fonts (--embed-fonts): licensed redistribution "
        "is the importer's responsibility, not jimemo's -- see the CLI's "
        "printed warning -- */\n"
        + "\n\n".join(blocks)
        + "\n"
    )
    lint_errors = css_reference_errors(embedded_css)
    if lint_errors:
        raise DesignImportError(
            "theme with embedded fonts failed the self-contained CSS check: "
            + "; ".join(lint_errors)
        )
    # Same output-side shape gate build_theme ran, re-run because the
    # appended @font-face blocks are new content it never saw -- the lint
    # above is blind to brace/comment/declaration injection.
    structure_errors = theme_structure_errors(embedded_css)
    if structure_errors:
        raise DesignImportError(
            "theme with embedded fonts failed structural safety check: "
            + "; ".join(structure_errors)
        )
    return embedded_css, families, total_bytes


def import_design(
    export_dir: Path,
    *,
    name: Optional[str] = None,
    embed_fonts: bool = False,
) -> ImportResult:
    """Read `export_dir` (a Claude-design export), map it to a jimemo
    theme, and install it at `~/.jimemo/themes/<name>.css`. Raises
    DesignImportError (from `read_export`, `build_theme`, or this
    module's own font handling) on anything that doesn't parse, fails
    value/path validation, or would fail the self-contained CSS check —
    in every case, nothing is written.

    An explicit `name` is validated as-is against the same slug shape
    `render --theme` requires (`invalid_theme_name_reason`) and rejected
    outright if it doesn't conform -- it is never silently lowercased or
    otherwise transformed, so a name this call accepts is always a name
    `render --theme` later accepts too. Omit `name` to derive one from
    the export's own namespace or directory name instead (`_default_name`,
    which slugifies since that input was never user-typed)."""
    export_dir = Path(export_dir)
    if not export_dir.is_dir():
        raise DesignImportError(f"export directory not found: {export_dir}")

    if name:
        reason = invalid_theme_name_reason(name)
        if reason is not None:
            raise DesignImportError(f"--name {reason} -- refusing to import it")

    export = read_export(export_dir)
    theme_name = name if name else _default_name(export, export_dir)
    if theme_name in RESERVED_THEME_NAMES:
        raise DesignImportError(
            f"theme name {theme_name!r} is reserved (it is one of the "
            f"toolkit's data-theme mode values) -- choose another with --name"
        )

    css = build_theme(export, theme_name)
    header = _theme_header(css)

    embedded_font_families: List[str] = []
    embedded_bytes = 0
    if embed_fonts:
        css, embedded_font_families, embedded_bytes = _embed_fonts(css, export, export_dir)

    themes_dir = personal_themes_dir()
    theme_path = themes_dir / f"{theme_name}.css"
    try:
        themes_dir.mkdir(parents=True, exist_ok=True)
        theme_path.write_text(css, encoding="utf-8")
    except OSError as e:
        raise DesignImportError(f"could not write theme to {theme_path}: {e}") from e

    return ImportResult(
        name=theme_name,
        theme_path=theme_path,
        css=css,
        header=header,
        embedded_font_families=embedded_font_families,
        embedded_bytes=embedded_bytes,
    )
