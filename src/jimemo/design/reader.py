"""Parse-only reader for a Claude-design export directory.

A "Claude-design export" is a folder of design tokens,
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

Token NAMES and the export namespace are validated at the same boundary
(`validate_token_name`, `validate_namespace`): the name is re-declared
verbatim as `<name>: <value>;` and referenced as `var(<name>)` in the
generated theme, and the namespace is interpolated into that theme's
header comment -- each is an injection channel exactly like a value.
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
    "validate_token_name",
    "validate_token_value",
    "validate_namespace",
    "validate_font_family",
    "THEME_NAME_RE",
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
    except (OSError, UnicodeDecodeError) as e:
        raise DesignImportError(f"cannot read manifest {path}: {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise DesignImportError(f"manifest {path} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise DesignImportError(f"manifest {path} must be a JSON object")
    return data


def _manifest_list(value: object, description: str) -> list:
    """Coerce a manifest field documented as a JSON array into a Python
    list, or raise DesignImportError if it's present but some other JSON
    type. This replaces the `value or []` idiom used below, which is only
    safe when the field is MISSING (`None`): for a malformed manifest
    where the field is present but e.g. an int, bool, or string, `value or
    []` evaluates to `value` itself (if truthy) and the caller's
    `for x in value` then either raises a raw TypeError (int, bool) or
    silently iterates the wrong thing (a string's characters, a dict's
    keys) instead of the intended list of entries. A manifest is untrusted
    DATA, so any shape mismatch must fail closed here with a clean
    DesignImportError, not surface an implementation-detail TypeError past
    cmd_import_design's DesignImportError-only catch."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise DesignImportError(
            f"manifest {description} must be a list, got "
            f"{type(value).__name__}: {value!r}"
        )
    return value


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
        validate_token_name(name)
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
    for f in _manifest_list(manifest.get("fonts"), "'fonts'"):
        if not isinstance(f, dict):
            continue
        files = [
            p
            for p in _manifest_list(f.get("files"), "font 'files'")
            if isinstance(p, str)
        ]
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
    for b in _manifest_list(manifest.get("brandFonts"), "'brandFonts'"):
        if not isinstance(b, dict):
            continue
        referencing = [
            tn
            for tn in _manifest_list(b.get("tokens"), "brandFont 'tokens'")
            if isinstance(tn, str)
        ]
        # A referencing token name is NOT merely informational: mapping picks
        # one of these as the font role's `source_token`, which build_theme
        # interpolates UNESCAPED into the generated theme's header COMMENT
        # (mapping._build_header). Each names a token exactly like a tokens[]
        # entry, so it is the same injection channel as a token name -- an
        # unvalidated name like `*/:root{--jm-font-mono:serif}/*` would break
        # the header comment open AND inject a second :root block carrying a
        # reserved --jm- override. Validate every entry with the same
        # allowlist + reserved-prefix check applied to token names, and
        # fail-closed (raise) for consistency with the family/weight/style/
        # namespace validation here -- so no unvalidated referencing name can
        # reach mapping.
        for tn in referencing:
            validate_token_name(tn)
        family = b.get("family")
        family = family if isinstance(family, str) else ""
        # BrandFont.family flows into mapping._font_declaration's quoted
        # `"<family>", <stack>` role value -- same interpolation risk as a
        # FontFace family, so it gets the same validation here.
        validate_font_family(family)
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
    validate_namespace(namespace)

    return DesignExport(
        tokens=tokens, fonts=fonts, brand_fonts=brand_fonts, namespace=namespace
    )


# -- css fallback path ---------------------------------------------------

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_ROOT_BLOCK_RE = re.compile(r":root\s*\{([^}]*)\}", re.DOTALL)
# One custom-property NAME shape, shared by the fallback scanner here and
# `validate_token_name`'s anchored allowlist below, so the two paths can
# never disagree about what a token name may contain.
_TOKEN_NAME_BODY = r"--[a-zA-Z0-9_-]+"
# A declaration's NAME and its trailing `:` -- deliberately not the whole
# `--name: value;` shape, because the value can legitimately contain a
# `;` (a data: URI's own separator; see `_value_end` below), so the value
# span has to be found by scanning forward from here rather than by a
# `[^;]+` regex group that would stop at that first `;`.
_DECL_HEAD_RE = re.compile(r"(" + _TOKEN_NAME_BODY + r")\s*:\s*")
# The metadata prefix of a *bare* (not url()-wrapped) data: URI value --
# `data:<mediatype>[;param]*,` -- matched only anchored at the value's own
# start. None of these character classes admit whitespace, ',', or ';',
# so this can only ever match a well-formed-looking prefix that actually
# reaches a real ','; a value that merely starts with `data:` but never
# reaches such a comma (malformed, or not a data: URI at all) simply
# fails to match and falls through to ordinary first-';'-terminates
# handling in `_value_end` -- there is no speculative semicolon-skipping
# that could run away past the true end of this declaration.
_BARE_DATA_URI_HEAD_RE = re.compile(
    r"data:[a-zA-Z0-9.+/-]*(?:;[a-zA-Z0-9=_.+-]*)*,", re.IGNORECASE
)
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


def _value_end(text: str, start: int) -> Optional[int]:
    """The index of the `;` in `text` that terminates the declaration
    value beginning at `start`, or None if it never terminates
    (unclosed/truncated CSS -- same as the old `[^;]+;` regex simply not
    matching). A `;` does NOT terminate the value when it is either:

      - inside `url( ... )` -- tracked via paren depth, so a url()
        argument (most commonly a data: URI, e.g.
        `url(data:image/png;base64,AAAA)`) can contain a literal `;`
        without ending the declaration early; or
      - part of a bare (not url()-wrapped) data: URI's own
        `data:<mediatype>[;param]*,<payload>` metadata prefix, matched
        via `_BARE_DATA_URI_HEAD_RE` anchored at `start` -- see that
        regex's docstring for why this can't run away past the true end
        of the declaration on malformed input."""
    i = start
    head = _BARE_DATA_URI_HEAD_RE.match(text, start)
    if head is not None:
        i = head.end()
    paren_depth = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "(":
            paren_depth += 1
        elif c == ")":
            if paren_depth > 0:
                paren_depth -= 1
        elif c == ";" and paren_depth == 0:
            return i
        i += 1
    return None


def _iter_custom_properties(body: str):
    """Yield `(name, value)` for each `--name: value;` declaration in
    `body` (a :root block's interior), splitting on the `;` that
    actually terminates each declaration -- see `_value_end` -- rather
    than the first `;` found, which truncates a legitimate
    `url(data:...)` or bare `data:...;base64,...` value. Declaration
    heads are found the same way `re.finditer` would (searching for the
    next match anywhere after the previous one), so text between
    declarations that doesn't fit the `--name:` shape (e.g. an
    injection-shaped name lacking the shared token-name charset) is
    skipped exactly as before."""
    pos = 0
    n = len(body)
    while pos < n:
        head = _DECL_HEAD_RE.search(body, pos)
        if head is None:
            return
        value_start = head.end()
        end = _value_end(body, value_start)
        if end is None:
            return  # unterminated declaration -- nothing further to find
        yield head.group(1).strip(), body[value_start:end].strip()
        pos = end + 1


def _fallback_font_files(body: str, css_path: Path, export_dir: Path) -> List[str]:
    """@font-face src url()s from `body`, re-expressed relative to the
    export ROOT -- the form the manifest path's fonts[].files already uses
    and the form importer._resolve_font_file resolves against. A CSS url()
    is relative to the CSS FILE's directory, so tokens/fonts.css saying
    url("../assets/fonts/X.ttf") means <export>/assets/fonts/X.ttf;
    carrying that url verbatim would make the importer resolve it against
    the export root and mis-read a valid in-export file as an escape. A
    url that genuinely resolves outside the export directory fails the
    import here (fail-closed, same boundary as the metadata validation
    below); a scheme'd or protocol-relative url (data:, https:, //cdn) is
    not a local path to reconcile and is carried verbatim -- the importer
    already refuses to embed those (no such file / extension check), and
    nothing else reads FontFace.files."""
    files: List[str] = []
    export_root = export_dir.resolve()
    for _q, url in _FONT_FACE_SRC_RE.findall(body):
        if url_scheme(url) or is_protocol_relative(url):
            files.append(url)
            continue
        try:
            resolved = (css_path.parent / url).resolve()
        except (OSError, ValueError) as e:
            raise DesignImportError(
                f"@font-face src {url!r} in {css_path.name} is not a usable "
                f"path: {e}"
            ) from e
        if not resolved.is_relative_to(export_root):
            raise DesignImportError(
                f"@font-face src {url!r} in {css_path.name} escapes the "
                f"export directory {export_root} -- refusing to carry it"
            )
        files.append(resolved.relative_to(export_root).as_posix())
    return files


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
        except (OSError, UnicodeDecodeError) as e:
            raise DesignImportError(f"cannot read {path}: {e}") from e
        text = _COMMENT_RE.sub("", text)
        try:
            rel = str(path.relative_to(export_dir))
        except ValueError:
            rel = path.name

        for block in _ROOT_BLOCK_RE.finditer(text):
            found_root_block = True
            for name, value in _iter_custom_properties(block.group(1)):
                if name in seen_names:
                    continue  # first definition wins across files
                seen_names.add(name)
                # _DECL_HEAD_RE shares validate_token_name's charset, so
                # this can't fail here -- called anyway so the guarantee
                # is enforced at the boundary, not assumed of a regex.
                validate_token_name(name)
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
            files = _fallback_font_files(body, path, export_dir)
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
    Everything else keeps failing on '<', a brace, a comment delimiter
    ('/*' or '*/'), 'expression(', a remote/protocol-relative/non-data
    url(), or any other ';'.

    The '/*' / '*/' rejection is unconditional (checked before the data:
    URI carve-out) because a well-formed allowlisted data: URI can never
    contain either: its mime is `type/subtype` (no '*' either side of the
    '/'), and its base64 payload is drawn from `[A-Za-z0-9+/=]`, which
    has no '*'. Rejecting comment delimiters also closes the one gap the
    substring 'expression(' check otherwise left open -- a value could
    previously spell `expr/**/ession(...)` to slip an IE `expression()`
    past the bare-substring match (the CSS-fallback path strips comments
    before this check, but the manifest path does not), and that spelling
    necessarily contains '/*' and '*/', so it now fails here first."""
    if "<" in value:
        raise DesignImportError(f"token {name!r} has unsafe value (contains '<'): {value!r}")
    if "{" in value or "}" in value:
        raise DesignImportError(
            f"token {name!r} has unsafe value (contains a brace): {value!r}"
        )
    if "/*" in value or "*/" in value:
        raise DesignImportError(
            f"token {name!r} has unsafe value (contains a comment delimiter "
            f"'/*' or '*/'): {value!r}"
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


# -- name / namespace sanitization ------------------------------------------

# validate_token_name's anchored form of _TOKEN_NAME_BODY (the css-fallback
# scanner's charset). \Z, not $: $ would also match before a trailing
# newline, letting "--x\n" through the allowlist.
_TOKEN_NAME_RE = re.compile(r"^" + _TOKEN_NAME_BODY + r"\Z")

# An export namespace is an identifier like `NorthwindFieldKit_7b3f21`;
# empty is the reader's sentinel for "the export declared none" (always the
# case on the CSS-fallback path).
_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9_-]*\Z")

# The shape of a jimemo theme name: lowercase letters/digits in
# single-hyphen-separated segments -- exactly what
# `design.importer.slugify_name` always produces from arbitrary input.
# Exported so `jimemo.inline` can validate a user-typed `--theme NAME`
# CLI value against the same allowlist before resolving it to
# `<themes_dir>/<name>.css`: an unvalidated name containing '..' or '/'
# (or an absolute path) would otherwise let `--theme` read an arbitrary
# .css file off disk. Lives here rather than in design.importer, which
# imports jimemo.inline for personal_themes_dir -- importing this
# constant from there back into jimemo.inline would be a load-time
# import cycle.
THEME_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\Z")

# jimemo's own semantic role tokens (--jm-bg, --jm-accent, --jm-font-prose,
# ...) all live under this prefix -- reserved for the toolkit, never for an
# import. Letting an imported token claim it would either silently override
# a role via the raw re-declaration (an export naming a token `--jm-accent`
# hijacks the theme) or, if mapping picked that same name as a role's
# source, emit a self-referential `--jm-bg: var(--jm-bg)`. An export has no
# legitimate reason to define jimemo's own roles, so this fails closed
# rather than trying to re-namespace the collision.
_RESERVED_TOKEN_PREFIX = "--jm-"


def validate_token_name(name: str) -> None:
    """Reject a token name that isn't a plain `--ident` CSS custom
    property name (`--` then ASCII letters/digits/_/-). A name is just as
    much an injection channel as a value: the generated theme re-declares
    it verbatim as `<name>: <value>;` inside :root and references it as
    `var(<name>)` in mapped role values (mapping.build_theme), so a
    hostile manifest name like `x: red } body { display:none } :root{ --y`
    would break out of the :root block and inject a live rule -- and
    css_reference_errors does not catch declaration/brace injection. On
    the CSS-fallback path _DECL_HEAD_RE can only ever extract a name
    matching this same charset; on the manifest path this check is the
    only gate.

    Also rejects (case-insensitively) any name starting with the
    `--jm-` prefix reserved for jimemo's own theme roles -- see
    `_RESERVED_TOKEN_PREFIX`."""
    if not _TOKEN_NAME_RE.match(name):
        raise DesignImportError(
            f"token name {name!r} is not a valid CSS custom property name "
            f"(expected '--' followed by letters/digits/_/-) -- refusing "
            f"to emit it into generated CSS"
        )
    if name.lower().startswith(_RESERVED_TOKEN_PREFIX):
        raise DesignImportError(
            f"token name {name!r} uses the reserved --jm- prefix (reserved "
            f"for jimemo theme roles)"
        )


def validate_namespace(namespace: str) -> None:
    """Reject an export namespace that isn't a plain identifier
    (letters/digits/_/-; empty allowed -- the no-manifest sentinel). The
    namespace is interpolated into the generated theme's header COMMENT
    (mapping._build_header), where a `*/` would close the comment and turn
    the rest of the string into live CSS. Every real namespace is an
    identifier (`NorthwindFieldKit_7b3f21`), so allowlisting the
    identifier shape is both safe and strictly stronger than blocklisting
    comment delimiters."""
    if not _NAMESPACE_RE.match(namespace):
        raise DesignImportError(
            f"export namespace {namespace!r} is not a plain identifier "
            f"(letters/digits/_/- only) -- refusing to interpolate it "
            f"into generated CSS"
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


def validate_font_family(family: str) -> None:
    """Reject a font family name that isn't safe to interpolate into the
    generated CSS, both inside a quoted `font-family: "<family>"` string
    AND as a bare token in a `"<family>", <stack>` role value. A legit
    family (letters, digits, spaces, hyphens -- e.g. ``Northwind Sans``,
    ``Northwind Gothic JP``) contains none of the constructs rejected here.

    An empty family is allowed (it's the reader's sentinel for a font
    entry that named no family; it can't inject anything). Everything else
    fails on a quote/backslash (would break out of the quoted string), a
    ``<``/brace/``;`` (declaration/rule injection), a newline/control
    char, or a ``url(``/``javascript:``/``expression(`` substring (none of
    which belongs in a family name). Comment delimiters (``/*``, ``*/``)
    are rejected too: a brand-font family also reaches the generated
    theme's header COMMENT (mapping._build_header's review notes), where
    a ``*/`` would break out of it."""
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
    for construct in ("url(", "javascript:", "expression(", "/*", "*/"):
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
    validate_font_family(family)
    _validate_font_weight(family, weight)
    _validate_font_style(family, style)
