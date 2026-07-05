"""Parse-only reader for a Claude-design export directory.

A "Claude-design export" (see docs/superpowers/plans/
2026-07-05-jimemo-phase6-design-import.md) is a folder of design tokens,
fonts, and React components produced by the design-system Skill. jimemo
only ever wants the tokens and font metadata out of it; the rest
(`_ds_bundle.js`, `*.jsx`, `*.ts`, `_adherence.oxlintrc.json`,
guidelines/components/slides/templates) is untrusted export CODE and
must never be opened, imported, or executed here.

Two sources, tried in order:
  1. `_ds_manifest.json` (preferred) — a JSON object already listing
     tokens/fonts/brandFonts/namespace. Reading it is `json.load`, never
     `eval`/`exec`; a hostile manifest can at worst supply bad DATA
     (handled by the value sanitization below), not run code.
  2. `tokens/*.css` (fallback, for an export that lacks a manifest) — a
     small regex scan of `:root { --x: value; }` custom properties and
     `@font-face` rules. CSS is DATA (parsed, never executed) but the
     fallback's kind/family inference is necessarily best-effort since
     the CSS carries no explicit `kind`/`brandFonts` metadata.

Every token VALUE is validated by `validate_token_value` before it's
accepted, because it is destined to be dropped verbatim into a generated
theme's `--name: <value>;` declaration (jimemo.design.mapping, a later
task): an export is untrusted input, so a value that could break out of
that declaration (inject a new rule, load a remote resource, run a CSS
`expression()`) must be rejected up front rather than filtered later.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from ..errors import DesignImportError
from ..sanitize import is_allowed_data_uri, is_protocol_relative, url_scheme

__all__ = [
    "Token",
    "FontFace",
    "BrandFont",
    "DesignExport",
    "read_export",
    "validate_token_value",
]


@dataclass
class Token:
    name: str
    value: str
    kind: str
    defined_in: Optional[str] = None


@dataclass
class FontFace:
    family: str
    weight: str
    style: str
    files: List[str] = field(default_factory=list)


@dataclass
class BrandFont:
    family: str
    referencing_token_names: List[str]
    status: str


@dataclass
class DesignExport:
    tokens: List[Token]
    fonts: List[FontFace]
    brand_fonts: List[BrandFont]
    namespace: str


def read_export(export_dir: Path) -> DesignExport:
    """Parse `export_dir` into a DesignExport.

    Prefers `_ds_manifest.json`; falls back to scanning `tokens/*.css`
    (or, absent a tokens/ dir, any top-level `*.css`) for `:root`
    custom properties. Raises DesignImportError if neither source
    yields any tokens, or a token value fails safety validation.
    """
    export_dir = Path(export_dir)
    manifest = _read_manifest(export_dir)
    if manifest is not None:
        return _from_manifest(manifest)
    return _from_css_fallback(export_dir)


# -- manifest path -----------------------------------------------------


def _read_manifest(export_dir: Path) -> Optional[dict]:
    path = export_dir / "_ds_manifest.json"
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise DesignImportError(f"cannot read manifest {path}: {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise DesignImportError(f"manifest {path} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise DesignImportError(f"manifest {path} must be a JSON object")
    return data


def _from_manifest(manifest: dict) -> DesignExport:
    raw_tokens = manifest.get("tokens")
    if not isinstance(raw_tokens, list) or not raw_tokens:
        raise DesignImportError("manifest has no usable 'tokens' list")

    tokens: List[Token] = []
    for i, t in enumerate(raw_tokens):
        if not isinstance(t, dict) or "name" not in t or "value" not in t:
            raise DesignImportError(
                f"manifest tokens[{i}] must be an object with 'name'/'value', "
                f"got {t!r}"
            )
        name, value = t["name"], t["value"]
        if not isinstance(name, str) or not isinstance(value, str):
            raise DesignImportError(
                f"manifest tokens[{i}] 'name'/'value' must be strings, got {t!r}"
            )
        validate_token_value(name, value)
        kind = t.get("kind")
        defined_in = t.get("definedIn")
        tokens.append(
            Token(
                name=name,
                value=value,
                kind=kind if isinstance(kind, str) else "other",
                defined_in=defined_in if isinstance(defined_in, str) else None,
            )
        )

    fonts: List[FontFace] = []
    for f in manifest.get("fonts") or []:
        if not isinstance(f, dict):
            continue
        files = [p for p in (f.get("files") or []) if isinstance(p, str)]
        family = f.get("family")
        family = family if isinstance(family, str) else ""
        raw_weight = f.get("weight")
        weight = "" if raw_weight is None else str(raw_weight)
        raw_style = f.get("style")
        style = raw_style if isinstance(raw_style, str) and raw_style else "normal"
        # Trust boundary: family/weight/style are interpolated unescaped
        # into generated CSS, so validate them before they reach any
        # consumer (importer @font-face blocks, mapping role values).
        _validate_font_metadata(family, weight, style)
        fonts.append(
            FontFace(family=family, weight=weight, style=style, files=files)
        )

    brand_fonts: List[BrandFont] = []
    for b in manifest.get("brandFonts") or []:
        if not isinstance(b, dict):
            continue
        referencing = [tn for tn in (b.get("tokens") or []) if isinstance(tn, str)]
        family = b.get("family")
        family = family if isinstance(family, str) else ""
        # BrandFont.family flows into mapping._font_declaration's quoted
        # `"<family>", <stack>` role value -- same interpolation risk as a
        # FontFace family, so it gets the same validation here.
        _validate_font_family(family)
        brand_fonts.append(
            BrandFont(
                family=family,
                referencing_token_names=referencing,
                status=b.get("status") or "unknown",
            )
        )

    namespace = manifest.get("namespace")
    if not isinstance(namespace, str):
        namespace = ""

    return DesignExport(
        tokens=tokens, fonts=fonts, brand_fonts=brand_fonts, namespace=namespace
    )


# -- css fallback path ---------------------------------------------------

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_ROOT_BLOCK_RE = re.compile(r":root\s*\{([^}]*)\}", re.DOTALL)
_CUSTOM_PROP_RE = re.compile(r"(--[a-zA-Z0-9_-]+)\s*:\s*([^;]+);")
_FONT_FACE_RE = re.compile(r"@font-face\s*\{([^}]*)\}", re.DOTALL)
_FONT_FACE_FAMILY_RE = re.compile(r"font-family\s*:\s*([^;]+);")
_FONT_FACE_WEIGHT_RE = re.compile(r"font-weight\s*:\s*([^;]+);")
_FONT_FACE_STYLE_RE = re.compile(r"font-style\s*:\s*([^;]+);")
_FONT_FACE_SRC_RE = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.IGNORECASE)


def _fallback_css_paths(export_dir: Path) -> List[Path]:
    """CSS files to scan when there's no manifest. Prefers tokens/*.css
    (the documented export layout); falls back to top-level *.css for
    an export shaped differently. Never touches .js/.jsx/.ts."""
    tokens_dir = export_dir / "tokens"
    if tokens_dir.is_dir():
        paths = sorted(tokens_dir.glob("*.css"))
        if paths:
            return paths
    return sorted(export_dir.glob("*.css"))


def _infer_kind(name: str, value: str) -> str:
    """Best-effort `kind` for a CSS-fallback token: the manifest carries
    an explicit kind, but raw CSS custom properties don't, so this is a
    heuristic (used only when no manifest is present)."""
    lname = name.lower()
    lvalue = value.strip().lower()
    if re.match(r"^#[0-9a-f]{3,8}$", lvalue) or lvalue.startswith(
        ("rgb(", "rgba(", "hsl(", "hsla(")
    ):
        return "color"
    if "radius" in lname:
        return "radius"
    if any(w in lname for w in ("font", "weight", "tracking", "leading")):
        return "font"
    if re.match(r"^-?\d+(\.\d+)?(px|rem|em|%)?$", lvalue):
        return "spacing"
    return "other"


def _from_css_fallback(export_dir: Path) -> DesignExport:
    paths = _fallback_css_paths(export_dir)
    if not paths:
        raise DesignImportError(
            f"no _ds_manifest.json and no token CSS files found under {export_dir}"
        )

    tokens: List[Token] = []
    seen_names = set()
    fonts: List[FontFace] = []
    found_root_block = False

    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise DesignImportError(f"cannot read {path}: {e}") from e
        text = _COMMENT_RE.sub("", text)
        try:
            rel = str(path.relative_to(export_dir))
        except ValueError:
            rel = path.name

        for block in _ROOT_BLOCK_RE.finditer(text):
            found_root_block = True
            for m in _CUSTOM_PROP_RE.finditer(block.group(1)):
                name, value = m.group(1).strip(), m.group(2).strip()
                if name in seen_names:
                    continue  # first definition wins across files
                seen_names.add(name)
                validate_token_value(name, value)
                tokens.append(
                    Token(
                        name=name,
                        value=value,
                        kind=_infer_kind(name, value),
                        defined_in=rel,
                    )
                )

        for block in _FONT_FACE_RE.finditer(text):
            body = block.group(1)
            family_m = _FONT_FACE_FAMILY_RE.search(body)
            if not family_m:
                continue
            weight_m = _FONT_FACE_WEIGHT_RE.search(body)
            style_m = _FONT_FACE_STYLE_RE.search(body)
            # Font src paths are carried as data only, exactly like the
            # manifest path's fonts[].files -- not opened/validated here
            # (that's a later task's embedding concern).
            files = [url for _q, url in _FONT_FACE_SRC_RE.findall(body)]
            family = family_m.group(1).strip().strip("\"'")
            weight = weight_m.group(1).strip() if weight_m else ""
            style = style_m.group(1).strip() if style_m else "normal"
            # Same trust boundary as the manifest path: these are
            # interpolated unescaped downstream, so validate here too.
            _validate_font_metadata(family, weight, style)
            fonts.append(
                FontFace(family=family, weight=weight, style=style, files=files)
            )

    if not found_root_block or not tokens:
        raise DesignImportError(
            f"no :root custom properties found in token CSS under {export_dir}"
        )

    # No manifest means no brandFonts/namespace metadata to fall back
    # on; leaving these empty is honest about what the CSS alone tells us.
    return DesignExport(tokens=tokens, fonts=fonts, brand_fonts=[], namespace="")


# -- value sanitization ---------------------------------------------------

_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)

# A `data:<type>/<subtype>;base64,<payload>` URI whose payload is
# restricted to the base64 alphabet -- anchored start-to-end so a
# candidate can't smuggle extra text (e.g. `...,AAAA; color:red`) past
# the mime check below by having only a *prefix* look like a data URI.
_DATA_URI_RE = re.compile(
    r"^data:[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+$",
    re.IGNORECASE,
)


def validate_token_value(name: str, value: str) -> None:
    """Reject a token value that isn't safe to drop verbatim into a
    generated theme's `--name: <value>;` declaration. None of these
    characters/constructs are needed by a legitimate color/spacing/font
    token value, so any occurrence is treated as hostile input rather
    than something to selectively strip.

    The one deliberate exception is a well-formed, allowlisted-mime
    `data:` URI (image or font, see `is_allowed_data_uri`): its own
    `;base64,` separator is not declaration-injection syntax, so it's
    carved out of the blanket ';' check below rather than rejected --
    this is what lets `--icon: data:image/png;base64,...` (and, for a
    later font-embedding task, `url(data:font/ttf;base64,...)`) through.
    Everything else keeps failing on '<', a brace, 'expression(', a
    remote/protocol-relative/non-data url(), or any other ';'.

    (Note: 'expression(' is matched as a bare substring, so a hostile
    manifest could still spell it past this check with an interleaved
    CSS comment on the regex-based CSS-fallback path, e.g.
    `expr/**/ession(...)`. CSS `expression()` is an IE-only legacy
    feature with no effect in any browser this pipeline targets, so
    that gap is noted rather than closed.)"""
    if "<" in value:
        raise DesignImportError(f"token {name!r} has unsafe value (contains '<'): {value!r}")
    if "{" in value or "}" in value:
        raise DesignImportError(
            f"token {name!r} has unsafe value (contains a brace): {value!r}"
        )
    if "expression(" in value.lower():
        raise DesignImportError(
            f"token {name!r} has unsafe value (CSS expression()): {value!r}"
        )

    # Carve out a bare data: URI value (the whole token value, not
    # wrapped in url()) before the blanket ';' check, provided its mime
    # is allowlisted. `_DATA_URI_RE` is fully anchored, so this only
    # matches when `stripped` -- and therefore `value` -- IS the data
    # URI, with nothing else appended for the ';' check to have missed.
    semicolon_target = value
    stripped = value.strip()
    if _DATA_URI_RE.match(stripped):
        if not is_allowed_data_uri(stripped):
            raise DesignImportError(
                f"token {name!r} has unsafe value (data: URI mime type not "
                f"image/font-allowlisted): {value!r}"
            )
        semicolon_target = semicolon_target.replace(stripped, "", 1)

    for match in _URL_RE.finditer(value):
        target = match.group(2)
        if _DATA_URI_RE.match(target):
            if not is_allowed_data_uri(target):
                raise DesignImportError(
                    f"token {name!r} has unsafe value (url() data: URI mime "
                    f"type not image/font-allowlisted): {value!r}"
                )
            # Only the matched url(...) span's ';base64,' is vetted;
            # drop that span so a ';' elsewhere in the value still trips
            # the check below.
            semicolon_target = semicolon_target.replace(match.group(0), "", 1)
            continue
        if is_protocol_relative(target):
            raise DesignImportError(
                f"token {name!r} has unsafe value (protocol-relative url()): {value!r}"
            )
        scheme = url_scheme(target)
        if scheme and scheme != "data":
            raise DesignImportError(
                f"token {name!r} has unsafe value (url() scheme {scheme!r} is "
                f"not local/data): {value!r}"
            )

    if ";" in semicolon_target:
        raise DesignImportError(
            f"token {name!r} has unsafe value (contains ';', possible "
            f"declaration injection): {value!r}"
        )


# -- font metadata sanitization -------------------------------------------
#
# Token VALUES go through validate_token_value above, but font METADATA
# (FontFace.family/weight/style and BrandFont.family) is a separate,
# equally untrusted channel: it comes straight from the manifest's
# fonts[]/brandFonts[] (or the CSS fallback's @font-face rules) and is
# interpolated UNESCAPED into generated CSS -- family into a quoted
# `font-family: "<family>"` string (importer._font_face_block) and a
# `"<family>", <stack>` role value (mapping._font_declaration); weight and
# style into bare `font-weight: <weight>;` / `font-style: <style>;`
# declarations inside the @font-face block. A hostile value like the
# weight `400} body{...} @font-face{font-weight:400` breaks out of that
# block and injects a sibling rule, and the css_reference_errors self-check
# does NOT catch brace/declaration injection (only url()/@import). So these
# fields are validated here at the reader trust boundary -- fail-closed:
# any injection-y content aborts the import with a clear error rather than
# being silently dropped -- which protects BOTH the importer and the
# mapping consumers at once, since every downstream reads these same
# already-validated dataclasses.

# Control chars (incl. newline/CR/tab) and DEL: never valid in a family,
# and a newline could start a fresh CSS line inside the interpolation.
_FONT_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

# CSS font-weight allowlist: the four keywords, or an integer 1-1000
# (400/700/... in practice); see _validate_font_weight.
_FONT_WEIGHT_KEYWORDS = frozenset({"normal", "bold", "lighter", "bolder"})

# CSS font-style allowlist: normal/italic/oblique, plus `oblique <angle>deg`
# (a single trivial angle) -- see _validate_font_style.
_FONT_STYLE_KEYWORDS = frozenset({"normal", "italic", "oblique"})
_OBLIQUE_ANGLE_RE = re.compile(r"^oblique\s+-?\d+(?:\.\d+)?deg$", re.IGNORECASE)


def _validate_font_family(family: str) -> None:
    """Reject a font family name that isn't safe to interpolate into the
    generated CSS, both inside a quoted `font-family: "<family>"` string
    AND as a bare token in a `"<family>", <stack>` role value. A legit
    family (letters, digits, spaces, hyphens -- e.g. ``Finder``,
    ``Ro NOW Std``) contains none of the constructs rejected here.

    An empty family is allowed (it's the reader's sentinel for a font
    entry that named no family; it can't inject anything). Everything else
    fails on a quote/backslash (would break out of the quoted string), a
    ``<``/brace/``;`` (declaration/rule injection), a newline/control
    char, or a ``url(``/``javascript:``/``expression(`` substring (none of
    which belongs in a family name)."""
    for bad in ('"', "\\", "<", "{", "}", ";"):
        if bad in family:
            raise DesignImportError(
                f"font family {family!r} has an unsafe character {bad!r} in "
                f"'family' -- refusing to interpolate it into CSS"
            )
    if _FONT_CONTROL_RE.search(family):
        raise DesignImportError(
            f"font family {family!r} has a newline/control character in "
            f"'family' -- refusing to interpolate it into CSS"
        )
    lowered = family.lower()
    for construct in ("url(", "javascript:", "expression("):
        if construct in lowered:
            raise DesignImportError(
                f"font family {family!r} contains {construct!r} in 'family' "
                f"-- refusing to interpolate it into CSS"
            )


def _validate_font_weight(family: str, weight: str) -> None:
    """Allowlist a CSS font-weight: empty (unspecified -> the importer's
    `weight or "normal"` handles it), a keyword in
    {normal, bold, lighter, bolder}, or an integer 1-1000. Anything else
    -- notably the PoC `400} body{...} @font-face{font-weight:400` -- is
    rejected, since weight is dropped verbatim into `font-weight: <weight>;`
    inside the @font-face block. (A variable-font range like `100 900` is
    also rejected by this single-value allowlist; single weights are what
    every design export seen carries, and fail-closed is the safer default
    for this trust boundary.)"""
    w = weight.strip()
    if w == "" or w.lower() in _FONT_WEIGHT_KEYWORDS:
        return
    if w.isdigit() and 1 <= int(w) <= 1000:
        return
    raise DesignImportError(
        f"font {family!r} has an invalid 'weight' {weight!r} -- expected a "
        f"number 1-1000 or one of normal/bold/lighter/bolder"
    )


def _validate_font_style(family: str, style: str) -> None:
    """Allowlist a CSS font-style: empty (-> the importer's
    `style or "normal"`), a keyword in {normal, italic, oblique}, or
    `oblique <angle>deg`. Anything else (e.g. `italic;x`) is rejected --
    style is dropped verbatim into `font-style: <style>;`."""
    s = style.strip()
    if s == "" or s.lower() in _FONT_STYLE_KEYWORDS or _OBLIQUE_ANGLE_RE.match(s):
        return
    raise DesignImportError(
        f"font {family!r} has an invalid 'style' {style!r} -- expected "
        f"normal, italic, or oblique (optionally 'oblique <angle>deg')"
    )


def _validate_font_metadata(family: str, weight: str, style: str) -> None:
    """Validate all three interpolated FontFace fields together (family,
    weight, style) at the reader trust boundary."""
    _validate_font_family(family)
    _validate_font_weight(family, weight)
    _validate_font_style(family, style)
