"""Deterministic token -> jimemo theme mapping.

`build_theme` turns a `DesignExport` (`reader.read_export`'s output) into a
jimemo *theme*: a `:root { --jm-*: ...; }` CSS override file that
`assemble_css` (src/jimemo/inline.py) layers on top of toolkit/tokens.css.
It does two things:

  1. Re-declares every imported token verbatim, under the export's own
     name (e.g. `--ct-black`) — nothing is lost, and a user can hand-edit
     any of these to refine a mapping below it.
  2. Maps a handful of those tokens onto jimemo's semantic `--jm-*` roles,
     by NAME heuristics (never guessing from color math alone, though
     luminance breaks ties -- see _luminance below). Roles this targets:
     --jm-font-prose / --jm-font-ui, --jm-accent / --jm-accent-contrast,
     --jm-text, --jm-bg, --jm-surface, --jm-muted, --jm-border,
     --jm-positive, --jm-negative.

No LLM, no export-specific special-casing: same export in, same theme
string out (`test_deterministic_across_runs` below).

One deliberate deviation from the plan text's literal `--jm-font`: toolkit/
tokens.css (Phase 3) ships a two-voice type system (--jm-font-prose for
content, --jm-font-ui for apparatus/labels; --jm-font-mono for code) and
defines no bare --jm-font at all -- no toolkit CSS reads it, so setting it
would be a silent no-op. A design export's single primary typeface is
applied to both prose and ui voices instead, since these exports are
brand systems built around one family, not jimemo's editorial split.

Mapped role values are emitted as `var(--<ns>-name)` referencing the
matching *source* token (not its resolved color), so editing the raw
token after import still moves the mapped role -- the two blocks stay
live, not a one-time copy. `--jm-accent-contrast` is the one exception:
it is a computed literal (#ffffff/#000000), not a source-token alias,
because no export in the wild publishes an explicit "text-on-accent"
token to alias.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..errors import DesignImportError
from ..lint import css_reference_errors
from .reader import (
    BrandFont,
    DesignExport,
    Token,
    validate_namespace,
    validate_token_name,
    validate_token_value,
    validate_font_family,
)

__all__ = ["build_theme", "theme_structure_errors"]


# ---------------------------------------------------------------------------
# structural output validation
# ---------------------------------------------------------------------------

_THEME_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_THEME_BLOCK_RE = re.compile(r"(?::root|@font-face)\s*\{[^{}]*\}")
# A single top-level :root block. build_theme emits EXACTLY ONE, and the
# importer only ever appends @font-face blocks -- never a second :root. More
# than one after comment-stripping means an injected `:root{...}` (e.g. via
# a header-comment breakout that reopened live CSS) rode in alongside the
# legitimate one. _THEME_BLOCK_RE alone cannot catch that: it strips EVERY
# :root block, so an injected extra one leaves an empty remainder and would
# pass. Counting them is the safety net regardless of which field was the
# vector.
_THEME_ROOT_BLOCK_RE = re.compile(r":root\s*\{[^{}]*\}")


def theme_structure_errors(css: str) -> List[str]:
    """Structural safety errors in a generated theme, or [] if its shape
    is inert: braces balanced, no comment delimiter left outside a
    well-formed `/* ... */`, EXACTLY ONE top-level `:root` block, and
    nothing at top level except that `:root` block and zero or more
    `@font-face` blocks. `css_reference_errors` (lint) only catches
    url()/@import -- it is blind to brace/comment/declaration injection --
    so this is the OUTPUT-side gate that closes that whole class no
    matter which untrusted field was the vector, including one the
    reader's input validation forgot to enumerate. A failure means
    either a build_theme bug or an injection that slipped the reader;
    either way the theme must not be written (fail closed). Also run by
    importer._embed_fonts, whose appended @font-face blocks build_theme
    never sees."""
    errors: List[str] = []
    stripped = _THEME_COMMENT_RE.sub("", css)
    if "/*" in stripped or "*/" in stripped:
        errors.append(
            "comment delimiter outside a well-formed /* ... */ comment"
        )
    if stripped.count("{") != stripped.count("}"):
        errors.append(
            "unbalanced braces ({} '{{' vs {} '}}')".format(
                stripped.count("{"), stripped.count("}")
            )
        )
    root_blocks = _THEME_ROOT_BLOCK_RE.findall(stripped)
    if len(root_blocks) > 1:
        errors.append(
            "multiple :root blocks ({}) -- possible injection".format(
                len(root_blocks)
            )
        )
    remainder = _THEME_BLOCK_RE.sub("", stripped).strip()
    if remainder:
        errors.append(
            "unexpected top-level CSS outside :root/@font-face blocks: "
            + repr(remainder[:80])
        )
    return errors


# ---------------------------------------------------------------------------
# font mapping
# ---------------------------------------------------------------------------

# Fallback stacks keyed by the generic CSS family the export's own token
# already ends in (see _generic_family_of) -- reusing the export author's
# own choice of voice (serif vs. sans vs. mono) rather than guessing one.
_FALLBACK_STACKS = {
    "serif": '"Iowan Old Style", Palatino, "Palatino Linotype", Georgia, serif',
    "sans-serif": (
        "-apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, "
        "Helvetica, Arial, sans-serif"
    ),
    "system-ui": "system-ui, -apple-system, \"Segoe UI\", Roboto, sans-serif",
    "monospace": 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
}
_DEFAULT_GENERIC = "sans-serif"

_GENERIC_FAMILY_RE = re.compile(
    r"(serif|sans-serif|system-ui|monospace)\s*$", re.IGNORECASE
)

# A referencing-token name shaped like "--<ns>-font" (no suffix) marks the
# export's *primary* family token, as opposed to a variant like
# "--ct-font-jp" or "--ct-font-pixel" -- used only as a tie-breaker.
_PRIMARY_FONT_TOKEN_RE = re.compile(r"^--[\w-]+-font$")


def _generic_family_of(value: str) -> Optional[str]:
    """The trailing generic CSS family (serif/sans-serif/system-ui/
    monospace) a font-stack token value ends in, or None if it ends in
    something else (a bare family name, `var(...)`, ...)."""
    m = _GENERIC_FAMILY_RE.search(value)
    return m.group(1).lower() if m else None


def _pick_primary_font(export: DesignExport) -> Optional[BrandFont]:
    """The brand font to apply to both jimemo font voices: the "ok"
    (actually used) brand font referenced by the most tokens, tied first
    to whichever is referenced by a `--*-font` (undecorated) token, then
    by family name for determinism. Families the export marked
    unreferenced/unknown are never picked -- a brand font that the export
    itself flagged as unused is not a confident signal.

    Returns the SELECTED BrandFont object (not just its family), so the
    caller uses exactly the entry chosen here -- already guaranteed
    status "ok" with a non-empty `referencing_token_names`. Returning the
    family alone forced the caller to re-find "the first brand font with
    this family", which, given duplicate brandFonts entries for one
    family where the first has no tokens, mis-picked the token-less
    duplicate and then IndexError'd on its empty referencing list."""
    candidates = [b for b in export.brand_fonts if b.status == "ok" and b.referencing_token_names]
    if not candidates:
        return None

    def sort_key(b):
        has_primary_token = any(
            _PRIMARY_FONT_TOKEN_RE.match(tn) for tn in b.referencing_token_names
        )
        return (not has_primary_token, -len(b.referencing_token_names), b.family)

    return sorted(candidates, key=sort_key)[0]


def _font_declaration(export: DesignExport) -> Optional[Tuple[str, str, str]]:
    """(css_value, family, source_token_name) for the primary brand font,
    or None if no confident family was found. `source_token_name` is
    whichever referencing token supplied the fallback stack's generic
    family, used only for the header's mapping table.

    brand_fonts (manifest metadata) is the primary source when present.
    When there's no *confident* brand font to use -- a manifest lacking
    `brandFonts`, the CSS-fallback path (which never populates it at
    all), or a manifest whose brand_fonts are all unreferenced/unknown
    -- falls back to `_infer_font_declaration`'s name/FontFace
    heuristics instead of leaving every such export unable to map a
    font at all. An unreferenced-only brand_fonts list is treated the
    same as an empty one; a brand font the export itself flagged as
    unused is not a reason to skip inference."""
    brand = _pick_primary_font(export)
    if brand:
        family = brand.family
        tokens_by_name = {t.name: t for t in export.tokens}
        generic = None
        # _pick_primary_font only returns a brand with a non-empty
        # referencing_token_names, so [0] is always safe -- no re-find by
        # family (which could land on a token-less duplicate) and no
        # IndexError.
        source_token = brand.referencing_token_names[0]
        for tn in brand.referencing_token_names:
            t = tokens_by_name.get(tn)
            if t is None:
                continue
            found = _generic_family_of(t.value)
            if found:
                generic, source_token = found, tn
                break
        stack = _FALLBACK_STACKS.get(generic or _DEFAULT_GENERIC, _FALLBACK_STACKS[_DEFAULT_GENERIC])
        value = '"{}", {}'.format(family, stack)
        return value, family, source_token

    has_confident_brand_font = any(
        b.status == "ok" and b.referencing_token_names for b in export.brand_fonts
    )
    if not has_confident_brand_font:
        return _infer_font_declaration(export)
    return None


def _first_family_in_stack(value: str) -> Optional[str]:
    """The first font-family in a CSS font-stack VALUE (comma-separated),
    quotes stripped -- e.g. '"Sample Sans", -apple-system, ...' -> "Sample Sans",
    or None if the stack's first entry is empty (empty stack, whitespace
    only, or a leading comma) OR is an empty quoted family (`""` / `''`).

    Unquoting requires a MATCHING pair (`"..."` or `'...'`, backreferenced)
    so a mismatched-quote fragment like `"Sample Sans'` is not silently
    treated as a clean family -- it's returned as-is and left for the caller's
    `validate_font_family` to reject. The extracted family is never trusted
    on its own: `_infer_font_declaration` runs it through
    `validate_font_family` (rejecting quotes, braces, ';', comment
    delimiters, url()/js/expression) before interpolating it, so this
    function only has to find the first entry, not sanitize it."""
    first = value.split(",", 1)[0].strip()
    if not first:
        return None
    m = re.match(r"""^(['"])(.*)\1$""", first)
    if m:
        return m.group(2).strip() or None
    return first


def _pick_font_name_token(export: DesignExport) -> Optional[Token]:
    """A token to infer the primary family from when there's no
    brand_fonts metadata to consult: the export's own undecorated
    `--*-font` token if one exists (the same "primary marker" shape
    `_PRIMARY_FONT_TOKEN_RE` already uses to tie-break brand_fonts
    candidates), else the first token (in export order) whose name
    merely contains "font" (e.g. `--ct-font-jp` in an export with no
    undecorated primary token). None if the export defines no
    font-named token at all. First-in-export-order at each tier keeps
    this deterministic."""
    named = [t for t in export.tokens if "font" in t.name.lower()]
    if not named:
        return None
    primary = [t for t in named if _PRIMARY_FONT_TOKEN_RE.match(t.name)]
    return (primary or named)[0]


def _infer_font_declaration(export: DesignExport) -> Optional[Tuple[str, str, str]]:
    """(css_value, family, source_description) inferred WITHOUT
    brand_fonts metadata, for a manifest that omits `brandFonts` or the
    CSS-fallback path (which never populates it). Tried in order:

      (a) a `--*font*`-named token's own stack value -- its first family
          becomes the primary, and its trailing generic (serif/
          sans-serif/...) still picks the fallback stack exactly as the
          brand_fonts path does; or
      (b) the first parsed FontFace's family, if (a) found no usable
          token, or (a)'s family failed validation -- there's no
          font-stack value to read a generic from here, so the default
          fallback stack applies.

    None if neither yields a family, so the caller leaves jimemo's font
    defaults alone rather than emit a broken/empty family.

    (a)'s family is carved out of a token VALUE that only passed
    `validate_token_value` -- a generic CSS-value check that allows a
    bare `"` (needed for quoted families), not the stricter
    `validate_font_family` reader.py applies to brand_fonts/fonts[]
    metadata. A value like `--x-font: "Bad"Name, sans-serif` passes
    validate_token_value but would re-quote into a malformed
    `--jm-font-prose: ""Bad"Name", ...` if the carved family were
    interpolated as-is. So the extracted family gets that same stricter
    check here; one that fails it isn't safely fixable by this function,
    so it falls through to (b) (itself already validated at
    read_export time, so it needs no re-check here) rather than emit the
    broken declaration."""
    token = _pick_font_name_token(export)
    if token is not None:
        family = _first_family_in_stack(token.value)
        if family:
            try:
                validate_font_family(family)
            except DesignImportError:
                family = None
            if family:
                generic = _generic_family_of(token.value)
                stack = _FALLBACK_STACKS.get(generic or _DEFAULT_GENERIC, _FALLBACK_STACKS[_DEFAULT_GENERIC])
                value = '"{}", {}'.format(family, stack)
                return value, family, token.name

    if export.fonts and export.fonts[0].family:
        family = export.fonts[0].family
        value = '"{}", {}'.format(family, _FALLBACK_STACKS[_DEFAULT_GENERIC])
        return value, family, "fonts[0] ({!r})".format(family)

    return None


# ---------------------------------------------------------------------------
# color mapping
# ---------------------------------------------------------------------------

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_VAR_REF_RE = re.compile(r"^var\(\s*(--[\w-]+)\s*\)$")


def _resolve_color(value: str, tokens_by_name: Dict[str, Token], _seen: Optional[set] = None) -> Optional[str]:
    """`value` followed through one or more `var(--x)` indirections to a
    literal color, or None if it bottoms out in something else (a
    shorthand like "2px solid var(--ct-black)", an unresolvable
    reference, or a cycle). Used only to make luminance-based choices
    (contrast, lightest/darkest grey) see through an export's own
    semantic aliases (e.g. `--ct-ink: var(--ct-black)`)."""
    seen = _seen or set()
    m = _VAR_REF_RE.match(value.strip())
    if not m:
        return value.strip()
    ref = m.group(1)
    if ref in seen or ref not in tokens_by_name:
        return None
    seen.add(ref)
    return _resolve_color(tokens_by_name[ref].value, tokens_by_name, seen)


def _luminance(value: str, tokens_by_name: Dict[str, Token]) -> Optional[float]:
    """Perceptual luminance in [0, 1] for a (possibly var()-aliased) hex
    color, or None if it doesn't resolve to a plain `#rgb`/`#rrggbb`
    value (rgba/hsl and non-color values are left as None rather than
    guessed at -- every fixture and real export seen so far uses hex)."""
    resolved = _resolve_color(value, tokens_by_name)
    if resolved is None:
        return None
    m = _HEX_RE.match(resolved)
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def _color_tokens(export: DesignExport) -> List[Token]:
    return [t for t in export.tokens if t.kind == "color"]


# Keyword priority lists: for each jm role, the first keyword (in order)
# with any matching color-token name wins; among tokens matching that
# keyword, the first in the export's own token order is picked. Semantic
# aliases (ink, paper) are listed ahead of raw color names (black, white)
# on the theory that an export author who bothered to name a semantic
# alias meant it as the role to use.
_TEXT_KEYWORDS = ("ink", "text", "black")
_BG_KEYWORDS = ("paper", "background", "bg", "white")
_SURFACE_KEYWORDS = ("surface", "grey-light", "gray-light")
_ACCENT_KEYWORDS = ("accent", "primary", "brand", "core")
_POSITIVE_KEYWORDS = ("positive", "success", "green")
_NEGATIVE_KEYWORDS = ("negative", "error", "danger", "red")
_GREY_KEYWORDS = ("grey", "gray")

# A candidate whose resolved luminance falls in these ranges reads as
# "basically black" / "basically white" -- accent-picking prefers to skip
# such a match (a black/white "core" token is almost certainly a neutral,
# not the brand color) but will still use it if nothing else matches.
_NEAR_BLACK_MAX = 0.04
_NEAR_WHITE_MIN = 0.96


def _name_has_keyword(name: str, keyword: str) -> bool:
    """True if `keyword` appears as a `-`-delimited segment (or run of
    segments, for a hyphenated keyword like "grey-light") of `name`,
    not merely as a substring -- a raw substring test would match
    "ink" against "--ct-pink-light" or "core" against a hypothetical
    "--ct-hardcore", which is not the intent."""
    pattern = r"(?:^|-)" + re.escape(keyword) + r"(?:-|$)"
    return re.search(pattern, name.lower()) is not None


def _pick_by_keywords(tokens: List[Token], keywords: Tuple[str, ...]) -> Optional[Token]:
    for kw in keywords:
        for t in tokens:
            if _name_has_keyword(t.name, kw):
                return t
    return None


def _pick_accent(tokens: List[Token], tokens_by_name: Dict[str, Token]) -> Optional[Token]:
    fallback = None
    for kw in _ACCENT_KEYWORDS:
        for t in tokens:
            if not _name_has_keyword(t.name, kw):
                continue
            lum = _luminance(t.value, tokens_by_name)
            if lum is None or (_NEAR_BLACK_MAX < lum < _NEAR_WHITE_MIN):
                return t
            if fallback is None:
                fallback = t
    return fallback


_TONE_SUFFIX_RE = re.compile(r"-(light|dark)$")


def _pick_hue(tokens: List[Token], keywords: Tuple[str, ...]) -> Optional[Token]:
    """First color token matching `keywords`, preferring one with no
    "-light"/"-dark" suffix (an export's base tone, e.g. `--ct-green`
    over `--ct-green-light`/`--ct-green-dark`) since that is normally
    the one meant for solid use, not a tint/shade of it."""
    matches = [t for t in tokens if any(_name_has_keyword(t.name, kw) for kw in keywords)]
    if not matches:
        return None
    base_tones = [t for t in matches if not _TONE_SUFFIX_RE.search(t.name.lower())]
    return (base_tones or matches)[0]


def _pick_greys(
    tokens: List[Token], tokens_by_name: Dict[str, Token]
) -> Tuple[Optional[Token], Optional[Token]]:
    """(border_token, muted_token) from color tokens named grey/gray: the
    lightest becomes the border color (a light grey is the conventional
    hairline/divider tone) and the darkest of the rest becomes muted text
    (needs more contrast against the page than a border does). Tokens
    that don't resolve to a plain hex are excluded -- there is nothing to
    rank them by."""
    greys = [t for t in tokens if any(_name_has_keyword(t.name, kw) for kw in _GREY_KEYWORDS)]
    scored = [(t, _luminance(t.value, tokens_by_name)) for t in greys]
    scored = [(t, lum) for t, lum in scored if lum is not None]
    if not scored:
        return None, None
    scored.sort(key=lambda pair: pair[1])  # darkest first
    border = scored[-1][0]
    rest = scored[:-1]
    muted = rest[0][0] if rest else None
    return border, muted


def _color_mappings(export: DesignExport) -> "Dict[str, Tuple[Token, Optional[str]]]":
    """jm role -> (source token, literal override or None to emit
    var(--source)). Only roles with a confident source token are
    present; callers must leave every other role at jimemo's default."""
    tokens_by_name = {t.name: t for t in export.tokens}
    colors = _color_tokens(export)
    result: Dict[str, Tuple[Token, Optional[str]]] = {}

    text = _pick_by_keywords(colors, _TEXT_KEYWORDS)
    if text:
        result["--jm-text"] = (text, None)

    bg = _pick_by_keywords(colors, _BG_KEYWORDS)
    if bg:
        result["--jm-bg"] = (bg, None)

    surface = _pick_by_keywords(colors, _SURFACE_KEYWORDS)
    if surface:
        result["--jm-surface"] = (surface, None)

    border, muted = _pick_greys(colors, tokens_by_name)
    if border:
        result["--jm-border"] = (border, None)
    if muted:
        result["--jm-muted"] = (muted, None)

    accent = _pick_accent(colors, tokens_by_name)
    if accent:
        result["--jm-accent"] = (accent, None)
        lum = _luminance(accent.value, tokens_by_name)
        contrast = "#000000" if (lum is not None and lum > 0.5) else "#ffffff"
        result["--jm-accent-contrast"] = (accent, contrast)

    positive = _pick_hue(colors, _POSITIVE_KEYWORDS)
    if positive:
        result["--jm-positive"] = (positive, None)

    negative = _pick_hue(colors, _NEGATIVE_KEYWORDS)
    if negative:
        result["--jm-negative"] = (negative, None)

    return result


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------

# Emit order for the mapped-roles block: fixed regardless of discovery
# order, so the output is byte-identical across runs (dict insertion
# order would otherwise depend on which heuristics fired first).
_ROLE_ORDER = (
    "--jm-font-prose",
    "--jm-font-ui",
    "--jm-accent",
    "--jm-accent-contrast",
    "--jm-text",
    "--jm-bg",
    "--jm-surface",
    "--jm-muted",
    "--jm-border",
    "--jm-positive",
    "--jm-negative",
)


def _reject_comment_close(*values: str) -> None:
    """Backstop for the header COMMENT: no value interpolated into it may
    contain `*/`, which would close the comment early and turn the rest of
    the header into live CSS. The reader's namespace / token-name /
    font-family validators already reject `*/` in every field that feeds
    the header (and the theme_structure_errors gate is the output-side
    net), so this should never fire on validated input -- it is the
    fail-closed belt-and-braces for a hand-built DesignExport (or a future
    forgotten field) that reached build_theme without the reader, mirroring
    build_theme's own defense-in-depth re-validation of names/values."""
    for v in values:
        if "*/" in v:
            raise DesignImportError(
                "refusing to build theme: value {!r} would close the theme "
                "header comment (contains '*/')".format(v)
            )


def _build_header(
    name: str,
    export: DesignExport,
    mapped_lines: List[Tuple[str, str]],
    review_notes: List[str],
) -> str:
    # Every dynamic string below lands inside the /* ... */ header comment;
    # a stray `*/` in any of them breaks it open. Names/namespace/source
    # tokens/families are already validated upstream, but guard here too.
    _reject_comment_close(name, export.namespace or "")
    for _role, source in mapped_lines:
        _reject_comment_close(source)
    for note in review_notes:
        _reject_comment_close(note)
    lines = [
        "/* jimemo theme {!r} -- auto-generated from a Claude-design export".format(name),
        " * (namespace: {}). Deterministic: re-running the import on the".format(
            export.namespace or "(none)"
        ),
        " * same export regenerates this file byte-for-byte.",
        " *",
        " * Auto-mapped roles (source token -> role):",
    ]
    if mapped_lines:
        for role, source in mapped_lines:
            lines.append(" *   {} -> {}".format(source, role))
    else:
        lines.append(" *   (none -- no confident mapping found; jimemo defaults apply)")
    lines.append(" *")
    lines.append(" * Review / refine:")
    if review_notes:
        for note in review_notes:
            lines.append(" *   - {}".format(note))
    else:
        lines.append(" *   - nothing flagged")
    lines.append(" *")
    lines.append(
        " * Mapped roles above reference the source token via var(), so"
    )
    lines.append(
        " * editing that token's value below also updates the role."
    )
    lines.append(" * All imported tokens are re-declared verbatim below, mapped or not.")
    lines.append(" */")
    return "\n".join(lines) + "\n"


def build_theme(export: DesignExport, name: str) -> str:
    """A jimemo theme CSS string built from `export`: a `:root` block
    re-declaring every imported token verbatim, plus a deterministic
    subset mapped onto jimemo's `--jm-*` roles (see module docstring).

    Raises DesignImportError if a token name/value, the namespace, or a
    brand/font family fails the same safety checks reader.read_export
    already applied (defense in depth for a hand-built DesignExport that
    bypassed the reader -- names land verbatim in the :root block and
    var() refs, the namespace in the header comment, and a family in the
    `_font_declaration` role value / the header's review notes), if the
    assembled CSS still trips the Phase-3 self-contained lint's
    url()/@import scan, or if the output fails `theme_structure_errors`'
    shape check (the output-side gate for the injection class the lint
    cannot see).
    """
    for t in export.tokens:
        validate_token_name(t.name)
        validate_token_value(t.name, t.value)
    validate_namespace(export.namespace)
    # _font_declaration below reads export.brand_fonts / export.fonts
    # straight off the dataclass, the same way it reads export.tokens --
    # a hand-built DesignExport can carry an unvalidated family in either
    # list, so both get the same re-validation the tokens above just did,
    # ahead of any of it reaching _font_declaration/_infer_font_declaration.
    for b in export.brand_fonts:
        validate_font_family(b.family)
    for f in export.fonts:
        validate_font_family(f.family)

    mapped_lines: List[Tuple[str, str]] = []
    review_notes: List[str] = []
    role_values: Dict[str, str] = {}

    font = _font_declaration(export)
    if font:
        value, family, source_token = font
        role_values["--jm-font-prose"] = value
        role_values["--jm-font-ui"] = value
        mapped_lines.append(("--jm-font-prose", source_token))
        mapped_lines.append(("--jm-font-ui", source_token))
        others = [
            b.family
            for b in export.brand_fonts
            if b.status == "ok" and b.family != family and b.referencing_token_names
        ]
        for other in others:
            review_notes.append(
                "{!r} is also an \"ok\" brand font in this export but was not "
                "applied (only one primary family is auto-mapped); set "
                "--jm-font-prose/--jm-font-ui manually if it should be used "
                "instead or alongside {!r}.".format(other, family)
            )
    else:
        review_notes.append(
            "no confident primary font found (no brand font with status "
            "\"ok\" and a referencing token) -- --jm-font-prose/--jm-font-ui "
            "left at jimemo defaults."
        )

    color_map = _color_mappings(export)
    for role, (token, literal) in color_map.items():
        role_values[role] = literal if literal is not None else "var({})".format(token.name)
        mapped_lines.append((role, "computed" if literal is not None else token.name))

    for role in _ROLE_ORDER:
        if role == "--jm-font-prose" or role == "--jm-font-ui":
            continue
        if role not in color_map:
            review_notes.append(
                "{} was not auto-mapped -- no color token name matched this "
                "role's heuristic; set it manually if the imported palette "
                "should cover it.".format(role)
            )

    mapped_lines_ordered = sorted(
        mapped_lines, key=lambda pair: _ROLE_ORDER.index(pair[0]) if pair[0] in _ROLE_ORDER else 999
    )

    header = _build_header(name, export, mapped_lines_ordered, review_notes)

    raw_decls = ["  {}: {};".format(t.name, t.value) for t in export.tokens]
    mapped_decls = ["  {}: {};".format(role, role_values[role]) for role in _ROLE_ORDER if role in role_values]

    body_lines = list(raw_decls)
    if mapped_decls:
        body_lines.append("")
        body_lines.append("  /* -- mapped onto jimemo roles -- */")
        body_lines.extend(mapped_decls)

    css = header + ":root {\n" + "\n".join(body_lines) + "\n}\n"

    lint_errors = css_reference_errors(css)
    if lint_errors:
        raise DesignImportError(
            "generated theme {!r} failed the self-contained CSS check: {}".format(
                name, "; ".join(lint_errors)
            )
        )
    structure_errors = theme_structure_errors(css)
    if structure_errors:
        raise DesignImportError(
            "generated theme {!r} failed structural safety check: {}".format(
                name, "; ".join(structure_errors)
            )
        )
    return css
