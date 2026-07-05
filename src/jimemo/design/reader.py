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

Every token VALUE is validated by `_validate_token_value` before it's
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
from ..sanitize import is_protocol_relative, url_scheme

__all__ = ["Token", "FontFace", "BrandFont", "DesignExport", "read_export"]


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
        _validate_token_value(name, value)
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
        fonts.append(
            FontFace(
                family=f.get("family") or "",
                weight=str(f.get("weight", "")),
                style=f.get("style") or "normal",
                files=files,
            )
        )

    brand_fonts: List[BrandFont] = []
    for b in manifest.get("brandFonts") or []:
        if not isinstance(b, dict):
            continue
        referencing = [tn for tn in (b.get("tokens") or []) if isinstance(tn, str)]
        brand_fonts.append(
            BrandFont(
                family=b.get("family") or "",
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
                _validate_token_value(name, value)
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
            fonts.append(
                FontFace(
                    family=family_m.group(1).strip().strip("\"'"),
                    weight=(weight_m.group(1).strip() if weight_m else ""),
                    style=(style_m.group(1).strip() if style_m else "normal"),
                    files=files,
                )
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


def _validate_token_value(name: str, value: str) -> None:
    """Reject a token value that isn't safe to drop verbatim into a
    generated theme's `--name: <value>;` declaration. None of these
    characters/constructs are needed by a legitimate color/spacing/font
    token value, so any occurrence is treated as hostile input rather
    than something to selectively strip."""
    if "<" in value:
        raise DesignImportError(f"token {name!r} has unsafe value (contains '<'): {value!r}")
    if "{" in value or "}" in value:
        raise DesignImportError(
            f"token {name!r} has unsafe value (contains a brace): {value!r}"
        )
    if ";" in value:
        raise DesignImportError(
            f"token {name!r} has unsafe value (contains ';', possible "
            f"declaration injection): {value!r}"
        )
    if "expression(" in value.lower():
        raise DesignImportError(
            f"token {name!r} has unsafe value (CSS expression()): {value!r}"
        )
    for match in _URL_RE.finditer(value):
        target = match.group(2)
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
