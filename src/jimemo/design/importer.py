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
manifest lists FOR A FACE THE GENERATED THEME REFERENCES (family named
in its CSS, weight/style it states — see the face-selection section
below) is read, base64-encoded, and appended as an `@font-face` rule
with a `data:font/...` `src`. That embedding step is intentionally
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
from typing import List, Optional, Set, Tuple, Union

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
    "SkippedFontFace",
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


@dataclass(frozen=True)
class SkippedFontFace:
    """A face `--embed-fonts` declined to embed because the generated
    theme does not reference it — its family is never named in the
    theme's CSS, or its weight/style is not one the CSS states. Carries
    family/weight/style exactly as the export declared them, so a user
    who wanted a dropped weight can see that it was dropped. The face's
    files were never resolved, opened, or read by the embed step. (The
    manifest-less reader is a separate, earlier boundary: it confines
    every `@font-face` url to the export directory before any face is
    selected, so an escaping url there fails the import whether or not
    its face would have been skipped.)"""

    family: str
    weight: str
    style: str


@dataclass
class ImportResult:
    name: str
    theme_path: Path
    css: str
    header: str
    embedded_font_families: List[str] = field(default_factory=list)
    embedded_bytes: int = 0
    skipped_font_faces: List[SkippedFontFace] = field(default_factory=list)


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


# -- which faces the generated theme actually references ------------------
#
# _embed_fonts used to append a block for every face the export listed;
# a real export carries a dozen faces across weights and styles and the
# self-contained theme ballooned to megabytes for faces the theme never
# uses. The rule below decides which faces earn their bytes:
#
#   family  -- the theme's own CSS names it, in a font declaration
#              (`font-family:`) or any custom property whose value is a
#              font stack (the theme re-declares the export's own
#              `--*-font*` tokens verbatim, so a family can be named by
#              a property jimemo's roles never mention). EVERY concrete
#              family of a comma-separated list counts, not the first
#              alone: a browser falls back per CHARACTER, so
#              `"Latin Brand", "CJK Brand", sans-serif` draws its kana
#              from the second family while the first is installed and
#              embedded. jimemo's own fallback stacks (`Roboto`,
#              `"Segoe UI"`, ...) cost nothing by this: a candidate
#              only ever selects a face the export ships under that
#              name. Unquoted generic families (serif, sans-serif,
#              system-ui, ...) and var() references name no shippable
#              face and never count; a QUOTED entry is always a family
#              name, whatever it spells (`"serif"`, `"Brand (Display)"`,
#              `"ACME, Inc"`), as CSS reads it.
#              Comparison is case-insensitive and quote-insensitive
#              ("Inter" / 'Inter' / Inter / inter), with whitespace
#              runs collapsed.
#   weight/style -- the (weight, style) pairs the theme's CSS states in
#              `font-weight:` / `font-style:` declarations, read as one
#              global set (a theme :root cannot scope a weight to one
#              family). A theme stating neither — every theme
#              build_theme generates today, its roles being family
#              stacks — uses the regular face alone: weight
#              400 / normal / regular / unspecified, style normal.
#              A referenced family with NO exact match gets the face a
#              browser would settle on instead: the nearest weight of
#              the stated style, in CSS matching order (see
#              _nearest_weight_fallbacks).
#
# An unreferenced face is dropped from the EMBED only; the export on
# disk is never touched, and neither is the face's file — a skipped
# face's path is never resolved or opened, so a missing or traversal
# path on a skipped face cannot fail (or reach) the import.
# (That is the embed step's promise. The manifest-less reader confines
# every @font-face url to the export before selection, and an escaping
# url there still fails the import.)

_THEME_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# CSS generic family keywords (incl. the ui-* system aliases): never a
# face an export could ship, so an UNQUOTED one is skipped when reading
# the concrete families out of a stack.
_GENERIC_FAMILIES = frozenset(
    {
        "serif",
        "sans-serif",
        "monospace",
        "cursive",
        "fantasy",
        "system-ui",
        "ui-serif",
        "ui-sans-serif",
        "ui-monospace",
        "ui-rounded",
        "math",
        "emoji",
        "fangsong",
    }
)

# One declaration the reference rule reads: a custom property (any
# name) or a `font-family:` property. The head must sit at a declaration
# boundary (start of the css, after `{`, `;`, or a newline) so a value
# merely CONTAINING e.g. `font-family:` cannot pose as a declaration;
# the value stops at the next `;` / `}` / `{`. A value carrying a `;`
# inside a url(data:...) therefore truncates early — harmless here,
# because the truncated fragment (`url(data:font/ttf`) can never equal a
# family an export lists, and a candidate that matches nothing in
# export.fonts embeds nothing.
_FONT_VALUE_DECL_RE = re.compile(
    r"(?:^|[;{\n])\s*(--[a-zA-Z0-9_-]+|font-family)\s*:\s*([^;{}]*)",
    re.IGNORECASE,
)
_FONT_WEIGHT_DECL_RE = re.compile(
    r"(?:^|[;{\n])\s*font-weight\s*:\s*([^;{}]*)", re.IGNORECASE
)
_FONT_STYLE_DECL_RE = re.compile(
    r"(?:^|[;{\n])\s*font-style\s*:\s*([^;{}]*)", re.IGNORECASE
)
_QUOTED_FAMILY_RE = re.compile(r"""^(['\"])(.*)\1$""")


def _normalize_family(name: str) -> str:
    """`name` as a family-comparison key: whitespace runs collapsed to
    single spaces, trimmed, casefolded — so `"Inter"`, `'Inter'`,
    `Inter`, and `inter` all compare equal."""
    return re.sub(r"\s+", " ", name.strip()).casefold()


def _split_font_stack(value: str) -> List[str]:
    """`value` split on the commas that separate font-stack entries --
    a comma inside a quoted family (`"ACME, Inc"`) is part of the name,
    not a separator. The reader refuses a backslash in a family, so no
    escape handling is needed to find where a string ends."""
    entries: List[str] = []
    current: List[str] = []
    quote = ""
    for ch in value:
        if quote:
            if ch == quote:
                quote = ""
        elif ch in ("\"", "'"):
            quote = ch
        elif ch == ",":
            entries.append("".join(current))
            current = []
            continue
        current.append(ch)
    entries.append("".join(current))
    return entries


def _concrete_families(value: str) -> List[str]:
    """Every family in a comma-separated font-stack `value` that could
    name a shippable face, normalized (see `_normalize_family`), in
    order. A quoted entry (double or single, matching pair) is a family
    name as CSS reads it, whatever it spells -- `"serif"` is a family
    called serif, not the generic. An unquoted entry counts unless it is
    empty, a parenthesized construct (var(), url(), ...), or a CSS
    generic family. Later entries count as much as the first: fallback
    is per character, so a second family supplies the glyphs the first
    lacks even while the first is available."""
    families: List[str] = []
    for entry in _split_font_stack(value):
        entry = entry.strip()
        quoted = _QUOTED_FAMILY_RE.match(entry)
        if quoted:
            entry = quoted.group(2).strip()
        elif "(" in entry or entry.casefold() in _GENERIC_FAMILIES:
            continue
        if entry:
            families.append(_normalize_family(entry))
    return families


def _referenced_font_families(css: str) -> Set[str]:
    """Every family the theme CSS names in a font declaration or a
    custom property (see the section comment), as normalized
    comparison keys. Comments are stripped first: build_theme's header
    carries review notes that NAME families the mapping deliberately
    did not apply, and a comment is not a reference."""
    text = _THEME_COMMENT_RE.sub("", css)
    families: Set[str] = set()
    for match in _FONT_VALUE_DECL_RE.finditer(text):
        families.update(_concrete_families(match.group(2)))
    return families


def _canonical_font_weight(weight: str) -> Union[int, str]:
    """`weight` as a comparison key against a theme-stated
    font-weight: the regular-face spellings (empty / normal / regular)
    fold to 400, bold to 700, a plain number to its int. `lighter` and
    `bolder` are RELATIVE keywords with no absolute value, so they
    compare verbatim — a face and a theme stating the same keyword
    still match each other."""
    w = weight.strip().casefold()
    if w in ("", "normal", "regular"):
        return 400
    if w == "bold":
        return 700
    if w.isdigit():
        return int(w)
    return w


def _canonical_font_style(style: str) -> str:
    """`style` as a comparison key: its leading keyword, casefolded —
    `oblique 10deg` compares as `oblique`, since CSS font matching
    treats any oblique angle as an oblique face. Empty is the
    unspecified sentinel and folds to normal (matching
    `_font_face_block`'s `style or "normal"`)."""
    s = style.strip().casefold()
    return s.split()[0] if s else "normal"


def _used_font_variants(css: str) -> Set[Tuple[Union[int, str], str]]:
    """The (weight, style) pairs the theme CSS states, as the cartesian
    product of its distinct `font-weight:` and `font-style:`
    declaration values — a theme :root cannot scope either property to
    one family, so the pairing cannot be tighter than that (the product
    may over-embed when the theme states a weight and a style in
    different rules; over-embedding is the safe direction). A theme
    stating neither — every theme build_theme generates today — uses
    the regular face alone: (400, normal)."""
    text = _THEME_COMMENT_RE.sub("", css)
    weights = {
        _canonical_font_weight(w)
        for w in _FONT_WEIGHT_DECL_RE.findall(text)
        if w.strip()
    }
    styles = {
        _canonical_font_style(s)
        for s in _FONT_STYLE_DECL_RE.findall(text)
        if s.strip()
    }
    if not weights:
        weights = {400}
    if not styles:
        styles = {"normal"}
    return {(w, s) for w in weights for s in styles}


def _face_is_referenced(
    font: FontFace,
    families: Set[str],
    variants: Set[Tuple[Union[int, str], str]],
) -> bool:
    """True if `font` is a face the theme CSS actually uses: its family
    is one the CSS names, and its (weight, style) is one the CSS
    states. A face failing this is skipped without its file ever being
    resolved, opened, or read."""
    if _normalize_family(font.family) not in families:
        return False
    key = (_canonical_font_weight(font.weight), _canonical_font_style(font.style))
    return key in variants


def _nearest_weight(wanted: int, available: Set[int]) -> Optional[int]:
    """The weight CSS font matching settles on when no face has
    `wanted` (CSS Fonts 4, font-matching algorithm, font-weight step):
    a wanted weight of 400-500 looks upward as far as 500 first, then
    downward, then above 500; below 400 it looks downward then upward;
    above 500, upward then downward. None when `available` is empty."""
    below = sorted((w for w in available if w < wanted), reverse=True)
    above = sorted(w for w in available if w > wanted)
    if 400 <= wanted <= 500:
        order = [w for w in above if w <= 500] + below + [w for w in above if w > 500]
    elif wanted < 400:
        order = below + above
    else:
        order = above + below
    return order[0] if order else None


def _nearest_weight_fallbacks(
    fonts: List[FontFace],
    families: Set[str],
    variants: Set[Tuple[Union[int, str], str]],
) -> Set[int]:
    """Indexes into `fonts` of the faces embedded by FALLBACK: for a
    referenced family in which no file-backed face matches any stated
    (weight, style) exactly, the face a browser would settle on instead
    -- the nearest weight, in CSS matching order, among the family's
    faces of the stated style. Without this a display family the export
    ships only in Bold gets no @font-face at all under a theme that
    states no weight, and the page silently renders a system font.

    A family with any exact match is left alone, so an export that
    ships the regular face embeds exactly what it did before. The style
    is never substituted (no italic standing in for normal), and a
    relative weight (`lighter` / `bolder`) has no number to be near, on
    either side -- those stay skipped and are listed as such."""
    by_family: "dict[str, List[int]]" = {}
    for i, font in enumerate(fonts):
        if font.files:
            by_family.setdefault(_normalize_family(font.family), []).append(i)
    chosen: Set[int] = set()
    for family in families:
        indexes = by_family.get(family, [])
        if any(_face_is_referenced(fonts[i], families, variants) for i in indexes):
            continue
        for wanted_weight, wanted_style in variants:
            if not isinstance(wanted_weight, int):
                continue
            weights = {
                i: _canonical_font_weight(fonts[i].weight)
                for i in indexes
                if _canonical_font_style(fonts[i].style) == wanted_style
            }
            nearest = _nearest_weight(
                wanted_weight, {w for w in weights.values() if isinstance(w, int)}
            )
            chosen.update(i for i, w in weights.items() if w == nearest)
    return chosen


def _embed_fonts(
    css: str, export: DesignExport, export_dir: Path
) -> "tuple[str, List[str], int, List[SkippedFontFace]]":
    """`css` with one `@font-face` block appended per font file the
    export lists FOR A FACE THE THEME REFERENCES (see the
    face-selection section above), plus the family names embedded, the
    total bytes read (for the CLI's size/licensing warning), and the
    faces that were skipped. Re-validates the result against
    the same self-contained-CSS check `build_theme` already ran, since
    an embedded font is new content `build_theme` never saw -- this is
    defense in depth, not expected to ever fire (every appended `url()`
    is a data:font URI `lint.css_reference_errors` allows), but a
    silent hole here would ship the exact resource-loading risk this
    whole pipeline exists to prevent."""
    referenced = _referenced_font_families(css)
    variants = _used_font_variants(css)
    blocks: List[str] = []
    families: List[str] = []
    skipped: List[SkippedFontFace] = []
    total_bytes = 0
    fallbacks = _nearest_weight_fallbacks(export.fonts, referenced, variants)
    for index, font in enumerate(export.fonts):
        if not font.files:
            continue
        if index not in fallbacks and not _face_is_referenced(font, referenced, variants):
            # Dropped from the embed only -- the export itself is never
            # touched -- and the file is never resolved or opened, so a
            # missing or traversal path on a skipped face cannot fail
            # (or reach) the import.
            skipped.append(
                SkippedFontFace(
                    family=font.family, weight=font.weight, style=font.style
                )
            )
            continue
        block, nbytes = _font_face_block(font, export_dir)
        if not block:
            continue
        blocks.append(block)
        families.append(font.family)
        total_bytes += nbytes

    if not blocks:
        return css, families, total_bytes, skipped

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
    return embedded_css, families, total_bytes, skipped


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
    skipped_font_faces: List[SkippedFontFace] = []
    if embed_fonts:
        css, embedded_font_families, embedded_bytes, skipped_font_faces = _embed_fonts(
            css, export, export_dir
        )

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
        skipped_font_faces=skipped_font_faces,
    )
