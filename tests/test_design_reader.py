import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.errors import DesignImportError
from jimemo.design.reader import DesignExport, read_export

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "design-export"


def _copy_fixture(tmp_path: Path) -> Path:
    dest = tmp_path / "export"
    shutil.copytree(FIXTURE_DIR, dest)
    return dest


# -- manifest path -------------------------------------------------------


def test_reads_manifest_tokens():
    export = read_export(FIXTURE_DIR)
    raw = json.loads((FIXTURE_DIR / "_ds_manifest.json").read_text())

    assert isinstance(export, DesignExport)
    assert len(export.tokens) == len(raw["tokens"])
    assert {t.kind for t in export.tokens} == {"color", "font", "other", "radius", "spacing"}


def test_reads_manifest_namespace():
    export = read_export(FIXTURE_DIR)
    assert export.namespace == "ChibaTechDesignSystem_9e0e92"


def test_reads_manifest_brand_fonts():
    export = read_export(FIXTURE_DIR)
    finder = next(b for b in export.brand_fonts if b.family == "Finder")
    assert finder.status == "ok"
    assert "--ct-font" in finder.referencing_token_names
    assert "--ct-font-pixel" in finder.referencing_token_names
    assert "--ct-font-pixel-5" in finder.referencing_token_names


def test_reads_manifest_fonts():
    export = read_export(FIXTURE_DIR)
    families = {f.family for f in export.fonts}
    assert "Finder" in families
    assert "Ro NOW Std" in families
    finder_regular = next(
        f for f in export.fonts if f.family == "Finder" and f.weight == "400"
    )
    assert finder_regular.files == ["assets/fonts/Finder-Regular.ttf"]


# -- css fallback path -----------------------------------------------------


def test_css_fallback_extracts_root_tokens(tmp_path):
    export_dir = _copy_fixture(tmp_path)
    (export_dir / "_ds_manifest.json").unlink()

    export = read_export(export_dir)

    names = {t.name for t in export.tokens}
    assert "--ct-black" in names
    assert "--ct-blue-core" in names
    assert "--ct-font" in names
    assert "--ct-space-1" in names

    black = next(t for t in export.tokens if t.name == "--ct-black")
    assert black.value == "#000000"
    assert black.defined_in == "tokens/colors.css"


def test_css_fallback_no_manifest_and_no_css_raises(tmp_path):
    export_dir = tmp_path / "empty-export"
    export_dir.mkdir()
    (export_dir / "readme.md").write_text("nothing useful here")

    with pytest.raises(DesignImportError):
        read_export(export_dir)


# -- css fallback: data-URI values must not be truncated at their own ';' --
#
# The fallback scanner used to capture a custom-property value with
# `[^;]+`, which stops at the FIRST ';' in the block -- but a legitimate
# `url(data:...)` or bare `data:...;base64,...` value contains a ';' that
# is not a declaration terminator. Only exercised on this manifest-less
# path: the manifest path's values come out of JSON, which never gets
# split on ';' in the first place.


def _css_fallback_export(tmp_path: Path, css_body: str) -> Path:
    export_dir = tmp_path / "css-export"
    (export_dir / "tokens").mkdir(parents=True)
    (export_dir / "tokens" / "colors.css").write_text(css_body)
    return export_dir


def test_css_fallback_preserves_url_data_uri_semicolon(tmp_path):
    export_dir = _css_fallback_export(
        tmp_path, ":root { --logo: url(data:image/png;base64,AAAA); }\n"
    )
    export = read_export(export_dir)
    logo = next(t for t in export.tokens if t.name == "--logo")
    assert logo.value == "url(data:image/png;base64,AAAA)"


def test_css_fallback_preserves_bare_data_uri_semicolon(tmp_path):
    export_dir = _css_fallback_export(
        tmp_path, ":root { --x: data:font/ttf;base64,BBBB; }\n"
    )
    export = read_export(export_dir)
    x = next(t for t in export.tokens if t.name == "--x")
    assert x.value == "data:font/ttf;base64,BBBB"


def test_css_fallback_normal_value_still_parsed(tmp_path):
    export_dir = _css_fallback_export(tmp_path, ":root { --c: #fff; }\n")
    export = read_export(export_dir)
    assert export.tokens[0].name == "--c"
    assert export.tokens[0].value == "#fff"


def test_css_fallback_two_declarations_one_line_still_split(tmp_path):
    export_dir = _css_fallback_export(tmp_path, ":root { --a: 1; --b: 2; }\n")
    export = read_export(export_dir)
    values = {t.name: t.value for t in export.tokens}
    assert values == {"--a": "1", "--b": "2"}


def test_css_fallback_data_uri_and_following_token_both_parsed(tmp_path):
    # A data: URI value followed by an ordinary declaration on the same
    # line -- the ';' inside the data URI must not be mistaken for the
    # terminator, and the SUBSEQUENT declaration must still be found.
    export_dir = _css_fallback_export(
        tmp_path,
        ":root { --icon: url(data:image/png;base64,PHN2Zz4=); --gap: 4px; }\n",
    )
    export = read_export(export_dir)
    values = {t.name: t.value for t in export.tokens}
    assert values == {
        "--icon": "url(data:image/png;base64,PHN2Zz4=)",
        "--gap": "4px",
    }


def test_css_fallback_data_prefix_with_no_comma_handled_sanely(tmp_path):
    # `data:evil` never reaches a ',', so it doesn't look like a real
    # data: URI metadata prefix and gets no special treatment: the first
    # ';' terminates it (same as any other value), and the declaration
    # that follows is still parsed correctly rather than being swallowed
    # into a runaway scan for a comma that never comes.
    export_dir = _css_fallback_export(
        tmp_path, ":root { --bad: data:evil; --ok: 1px; }\n"
    )
    export = read_export(export_dir)
    values = {t.name: t.value for t in export.tokens}
    assert values == {"--bad": "data:evil", "--ok": "1px"}


def test_css_fallback_non_base64_data_shaped_value_rejected(tmp_path):
    # This DOES reach a comma (`data:text/plain;charset=utf8,payload`),
    # so it's captured WHOLE rather than truncated at the first ';' --
    # but it isn't an allowlisted `;base64,` data URI, so it still
    # carries a bare ';' when it reaches validate_token_value, which
    # correctly rejects it as a possible declaration injection. This is
    # the intended backstop: a genuinely malformed ';'-bearing value that
    # isn't a real data: URI or url() must still end up rejected, not
    # silently truncated into something that looks safe.
    export_dir = _css_fallback_export(
        tmp_path, ":root { --bad: data:text/plain;charset=utf8,payload; }\n"
    )
    with pytest.raises(DesignImportError, match="--bad"):
        read_export(export_dir)


# -- security: value sanitization -----------------------------------------


def _manifest_with_token_value(
    tmp_path: Path,
    value: str,
    *,
    name: str = "--evil-token",
    namespace: str = "Evil",
) -> Path:
    export_dir = tmp_path / "malicious-export"
    export_dir.mkdir()
    manifest = {
        "namespace": namespace,
        "tokens": [
            {"name": name, "value": value, "kind": "color", "definedIn": "tokens/colors.css"}
        ],
        "fonts": [],
        "brandFonts": [],
        "globalCssPaths": [],
        "themes": [],
    }
    (export_dir / "_ds_manifest.json").write_text(json.dumps(manifest))
    return export_dir


@pytest.mark.parametrize(
    "bad_value",
    [
        "url(https://evil/x.png)",
        "url(//evil/x.png)",
        "red;}body{x",
        "red; } body { display:none }",
        "expression(alert(1))",
        "EXPRESSION(alert(1))",
        "<script>alert(1)</script>",
        "data:text/html;base64,PHNjcmlwdD4=",
        "url(data:text/html;base64,PHNjcmlwdD4=)",
        "data:image/png;base64,AAAA; color:red",
    ],
)
def test_unsafe_token_value_rejected(tmp_path, bad_value):
    export_dir = _manifest_with_token_value(tmp_path, bad_value)
    with pytest.raises(DesignImportError, match="--evil-token"):
        read_export(export_dir)


@pytest.mark.parametrize(
    "safe_value",
    [
        "#4c4499",
        "1.25rem",
        '"Finder", sans-serif',
        "var(--ct-black)",
        "2px solid var(--ct-black)",
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB",
        "data:font/ttf;base64,AAAA",
        "data:font/woff2;base64,AAAA",
        "url(data:font/ttf;base64,AAAA)",
    ],
)
def test_safe_token_value_accepted(tmp_path, safe_value):
    export_dir = _manifest_with_token_value(tmp_path, safe_value)
    export = read_export(export_dir)
    assert export.tokens[0].value == safe_value


# -- security: token name / namespace sanitization -------------------------
#
# Token NAMES are re-declared verbatim in the generated theme's :root block
# and referenced via var(<name>); the namespace lands in the theme's header
# COMMENT. Both come straight from the untrusted manifest, so both are
# injection channels exactly like a token value and must fail at the reader.


@pytest.mark.parametrize(
    "bad_name",
    [
        "x: red } body { display:none } :root{ --y",  # :root breakout PoC
        "--x:red}body{display:none}--y",
        "--x;y",
        "--x y",
        "--x*/y",
        "evil",  # no leading --
        "--",    # no ident at all
        "--x\n",  # trailing newline must not slip past the anchor
    ],
)
def test_unsafe_token_name_rejected(tmp_path, bad_name):
    export_dir = _manifest_with_token_value(tmp_path, "#111111", name=bad_name)
    with pytest.raises(DesignImportError, match="token name"):
        read_export(export_dir)


def test_valid_token_name_accepted(tmp_path):
    export_dir = _manifest_with_token_value(tmp_path, "#111111", name="--ct-black")
    export = read_export(export_dir)
    assert export.tokens[0].name == "--ct-black"


@pytest.mark.parametrize(
    "reserved_name",
    [
        "--jm-bg",
        "--JM-accent",
        "--jm-anything",
    ],
)
def test_reserved_jm_prefix_token_name_rejected(tmp_path, reserved_name):
    # --jm- is jimemo's own role-token namespace; an imported token
    # claiming it would silently override a toolkit role (raw
    # re-declaration) or produce a self-referential var() (if mapping
    # picked it as a role's source) -- rejected outright instead.
    export_dir = _manifest_with_token_value(tmp_path, "#111111", name=reserved_name)
    with pytest.raises(DesignImportError, match="reserved"):
        read_export(export_dir)


@pytest.mark.parametrize("normal_name", ["--ct-black", "--brand-blue"])
def test_non_reserved_token_name_accepted(tmp_path, normal_name):
    export_dir = _manifest_with_token_value(tmp_path, "#111111", name=normal_name)
    export = read_export(export_dir)
    assert export.tokens[0].name == normal_name


def test_css_fallback_cannot_extract_an_injection_shaped_name(tmp_path):
    # The fallback scanner's regex shares validate_token_name's charset,
    # so a hostile name in raw CSS never even parses as a token.
    export_dir = tmp_path / "export"
    (export_dir / "tokens").mkdir(parents=True)
    (export_dir / "tokens" / "colors.css").write_text(
        ":root { --ok: #ffffff; --bad name: red; x: red; }\n"
    )
    export = read_export(export_dir)
    assert [t.name for t in export.tokens] == ["--ok"]


@pytest.mark.parametrize(
    "bad_namespace",
    [
        "Evil*/}body{display:none}/*",  # comment-breakout PoC
        "Evil*/x",
        "Evil/*x",
        "Evil{x}",
        "Evil;x",
        "Evil<x",
        "Evil x",
        'Evil"x',
        "Evil\\x",
        "Evil\nx",
    ],
)
def test_unsafe_namespace_rejected(tmp_path, bad_namespace):
    export_dir = _manifest_with_token_value(
        tmp_path, "#111111", namespace=bad_namespace
    )
    with pytest.raises(DesignImportError, match="namespace"):
        read_export(export_dir)


def test_real_namespace_accepted(tmp_path):
    export_dir = _manifest_with_token_value(
        tmp_path, "#111111", namespace="ChibaTechDesignSystem_9e0e92"
    )
    export = read_export(export_dir)
    assert export.namespace == "ChibaTechDesignSystem_9e0e92"


# -- security: font metadata sanitization ---------------------------------
#
# Font family/weight/style come straight from the untrusted manifest and
# are interpolated UNESCAPED into generated CSS (importer @font-face
# blocks; mapping role values). The reader is the trust boundary that must
# reject injection-y values before any consumer sees them -- the
# css_reference_errors self-check does NOT catch brace/declaration
# injection, so these MUST fail here.


def _manifest_with_font(tmp_path: Path, font: dict, *, brand_fonts=None) -> Path:
    export_dir = tmp_path / "font-export"
    export_dir.mkdir()
    manifest = {
        "namespace": "Evil",
        "tokens": [
            {"name": "--evil-token", "value": "#111111", "kind": "color"}
        ],
        "fonts": [font],
        "brandFonts": brand_fonts or [],
        "globalCssPaths": [],
        "themes": [],
    }
    (export_dir / "_ds_manifest.json").write_text(json.dumps(manifest))
    return export_dir


def _manifest_with_brand_font(tmp_path: Path, brand_font: dict) -> Path:
    export_dir = tmp_path / "brand-font-export"
    export_dir.mkdir()
    manifest = {
        "namespace": "Evil",
        "tokens": [
            {"name": "--evil-token", "value": "#111111", "kind": "color"}
        ],
        "fonts": [],
        "brandFonts": [brand_font],
        "globalCssPaths": [],
        "themes": [],
    }
    (export_dir / "_ds_manifest.json").write_text(json.dumps(manifest))
    return export_dir


def test_font_weight_css_injection_poc_rejected(tmp_path):
    # The reviewer's exact proof-of-concept: a weight that closes the
    # @font-face block and injects a sibling rule.
    font = {
        "family": "Evil",
        "weight": "400} body{display:none} @font-face{font-weight:400",
        "style": "normal",
        "files": [],
    }
    export_dir = _manifest_with_font(tmp_path, font)
    with pytest.raises(DesignImportError, match="weight"):
        read_export(export_dir)


def test_font_family_quote_breakout_rejected(tmp_path):
    font = {
        "family": '"} body{display:none} .x{font-family:"Y',
        "weight": "400",
        "style": "normal",
        "files": [],
    }
    export_dir = _manifest_with_font(tmp_path, font)
    with pytest.raises(DesignImportError, match="family"):
        read_export(export_dir)


@pytest.mark.parametrize("bad_family", ["Ev}il", "Ev;il", "Ev<il", "back\\slash", "url(x)", "javascript:alert(1)"])
def test_font_family_unsafe_chars_rejected(tmp_path, bad_family):
    font = {"family": bad_family, "weight": "400", "style": "normal", "files": []}
    export_dir = _manifest_with_font(tmp_path, font)
    with pytest.raises(DesignImportError, match="family"):
        read_export(export_dir)


@pytest.mark.parametrize("bad_family", ["Evil*/x", "Evil/*x"])
def test_font_family_comment_delimiters_rejected(tmp_path, bad_family):
    # A brand-font family also reaches the generated theme's header
    # comment (review notes), where */ would break out of it.
    font = {"family": bad_family, "weight": "400", "style": "normal", "files": []}
    export_dir = _manifest_with_font(tmp_path, font)
    with pytest.raises(DesignImportError, match="family"):
        read_export(export_dir)


@pytest.mark.parametrize("bad_weight", ["abc", "12x", "400 700", "-100", "1001", "0"])
def test_font_weight_invalid_rejected(tmp_path, bad_weight):
    font = {"family": "Legit", "weight": bad_weight, "style": "normal", "files": []}
    export_dir = _manifest_with_font(tmp_path, font)
    with pytest.raises(DesignImportError, match="weight"):
        read_export(export_dir)


@pytest.mark.parametrize("good_weight", ["400", "700", "1", "1000", "normal", "bold", "lighter", "bolder", ""])
def test_font_weight_valid_accepted(tmp_path, good_weight):
    font = {"family": "Legit", "weight": good_weight, "style": "normal", "files": []}
    export_dir = _manifest_with_font(tmp_path, font)
    export = read_export(export_dir)
    assert export.fonts[0].weight == good_weight


@pytest.mark.parametrize("bad_style", ["italic;x", "italic} body{x", "slanted", "italic<"])
def test_font_style_invalid_rejected(tmp_path, bad_style):
    font = {"family": "Legit", "weight": "400", "style": bad_style, "files": []}
    export_dir = _manifest_with_font(tmp_path, font)
    with pytest.raises(DesignImportError, match="style"):
        read_export(export_dir)


@pytest.mark.parametrize("good_style", ["normal", "italic", "oblique", "oblique 14deg"])
def test_font_style_valid_accepted(tmp_path, good_style):
    font = {"family": "Legit", "weight": "400", "style": good_style, "files": []}
    export_dir = _manifest_with_font(tmp_path, font)
    export = read_export(export_dir)
    assert export.fonts[0].style == good_style


def test_brand_font_family_injection_rejected(tmp_path):
    # BrandFont.family flows into mapping's quoted role value, so it is
    # validated at the same boundary as a FontFace family.
    brand = {"family": '"} body{display:none} .x{font-family:"Y', "status": "ok", "tokens": ["--x-font"]}
    export_dir = _manifest_with_brand_font(tmp_path, brand)
    with pytest.raises(DesignImportError, match="family"):
        read_export(export_dir)


# -- security: manifest shape validation (fail closed, not TypeError) -----
#
# A manifest is untrusted DATA (see module docstring): a malformed shape
# -- the wrong JSON type for a field that's normally a list, or a list
# entry that isn't a dict -- must never surface a raw TypeError from an
# unguarded `for x in <field>` or `<field>[i]`. cmd_import_design only
# catches DesignImportError, so anything else would escape as an
# unhandled traceback instead of the CLI's clean rc=1 message.


def _write_manifest(tmp_path: Path, manifest: dict, *, dirname: str = "malformed-export") -> Path:
    export_dir = tmp_path / dirname
    export_dir.mkdir()
    (export_dir / "_ds_manifest.json").write_text(json.dumps(manifest))
    return export_dir


def _base_manifest(**overrides) -> dict:
    manifest = {
        "namespace": "Evil",
        "tokens": [{"name": "--ok", "value": "#111111", "kind": "color"}],
        "fonts": [],
        "brandFonts": [],
        "globalCssPaths": [],
        "themes": [],
    }
    manifest.update(overrides)
    return manifest


@pytest.mark.parametrize("bad_field", ["tokens", "fonts", "brandFonts"])
def test_manifest_top_level_field_wrong_type_rejected(tmp_path, bad_field):
    # e.g. {"fonts": 1} used to reach `for f in 1 or []:` -> raw TypeError.
    export_dir = _write_manifest(tmp_path, _base_manifest(**{bad_field: 1}))
    with pytest.raises(DesignImportError):
        read_export(export_dir)


def test_manifest_font_files_wrong_type_rejected(tmp_path):
    manifest = _base_manifest(
        fonts=[{"family": "Legit", "weight": "400", "style": "normal", "files": 1}]
    )
    export_dir = _write_manifest(tmp_path, manifest)
    with pytest.raises(DesignImportError, match="files"):
        read_export(export_dir)


def test_manifest_brand_font_tokens_wrong_type_rejected(tmp_path):
    manifest = _base_manifest(
        brandFonts=[{"family": "Legit", "status": "ok", "tokens": 1}]
    )
    export_dir = _write_manifest(tmp_path, manifest)
    with pytest.raises(DesignImportError, match="tokens"):
        read_export(export_dir)


def test_manifest_non_dict_font_entry_skipped(tmp_path):
    # Consistent with the existing `if not isinstance(f, dict): continue`
    # behavior for a malformed individual entry (as opposed to a
    # malformed 'fonts' container, which is rejected above).
    export_dir = _write_manifest(tmp_path, _base_manifest(fonts=[1, "x"]))
    export = read_export(export_dir)
    assert export.fonts == []


def test_manifest_non_dict_brand_font_entry_skipped(tmp_path):
    export_dir = _write_manifest(tmp_path, _base_manifest(brandFonts=[1, "x"]))
    export = read_export(export_dir)
    assert export.brand_fonts == []


def test_manifest_non_dict_token_entry_rejected(tmp_path):
    export_dir = _write_manifest(tmp_path, _base_manifest(tokens=[1]))
    with pytest.raises(DesignImportError, match=r"tokens\[0\]"):
        read_export(export_dir)


def test_manifest_token_missing_name_rejected(tmp_path):
    export_dir = _write_manifest(
        tmp_path, _base_manifest(tokens=[{"value": "#111111"}])
    )
    with pytest.raises(DesignImportError, match=r"tokens\[0\]"):
        read_export(export_dir)


def test_manifest_token_non_string_name_rejected(tmp_path):
    export_dir = _write_manifest(
        tmp_path, _base_manifest(tokens=[{"name": 1, "value": "#111111"}])
    )
    with pytest.raises(DesignImportError, match=r"tokens\[0\]"):
        read_export(export_dir)


def test_chiba_fixture_fonts_still_import_cleanly():
    # Every real font in the checked-in fixture is legit (Finder / Ro NOW
    # Std, weights 300/400/500/700/900, style normal), so the fix must not
    # regress it.
    export = read_export(FIXTURE_DIR)
    families = {f.family for f in export.fonts}
    assert "Finder" in families
    assert "Ro NOW Std" in families
    assert {b.family for b in export.brand_fonts}  # brand fonts parsed, none rejected


# -- css fallback: @font-face src paths -------------------------------------
#
# A CSS url() is relative to the CSS FILE's directory, but FontFace.files
# (like the manifest's fonts[].files) is export-root-relative -- the
# fallback must reconcile the two or the importer would reject a valid
# in-export font as an escape.


def test_css_fallback_font_files_are_export_root_relative(tmp_path):
    export_dir = _copy_fixture(tmp_path)
    (export_dir / "_ds_manifest.json").unlink()

    export = read_export(export_dir)

    finder_regular = next(
        f for f in export.fonts if f.family == "Finder" and f.weight == "400"
    )
    # same form the manifest path stores (compare test_reads_manifest_fonts)
    assert finder_regular.files == ["assets/fonts/Finder-Regular.ttf"]
    for f in export.fonts:
        for p in f.files:
            assert not p.startswith("../"), f"{f.family} kept a css-relative path: {p}"


def test_css_fallback_font_url_real_escape_rejected(tmp_path):
    export_dir = tmp_path / "export"
    (export_dir / "tokens").mkdir(parents=True)
    (export_dir / "tokens" / "colors.css").write_text(":root { --x-ink: #111111; }\n")
    (export_dir / "tokens" / "fonts.css").write_text(
        '@font-face { font-family: "Sneaky"; '
        'src: url("../../outside.ttf") format("truetype"); '
        "font-weight: 400; font-style: normal; }\n"
    )
    (tmp_path / "outside.ttf").write_bytes(b"SHOULD-NEVER-BE-CARRIED")

    with pytest.raises(DesignImportError, match="escapes"):
        read_export(export_dir)


# -- parse-only guarantee --------------------------------------------------


def test_never_reads_js_files(tmp_path, monkeypatch):
    export_dir = _copy_fixture(tmp_path)
    (export_dir / "_ds_bundle.js").write_text("this is not valid JS; import('nope')")
    (export_dir / "templates").mkdir()
    (export_dir / "templates" / "evil.jsx").write_text("<script>alert(1)</script>")

    original_read_text = Path.read_text

    def spying_read_text(self, *args, **kwargs):
        assert self.suffix not in (".js", ".jsx", ".ts"), f"reader opened {self}"
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spying_read_text)

    export = read_export(export_dir)
    assert export.namespace == "ChibaTechDesignSystem_9e0e92"


def test_css_fallback_never_reads_js_files(tmp_path, monkeypatch):
    export_dir = _copy_fixture(tmp_path)
    (export_dir / "_ds_manifest.json").unlink()
    (export_dir / "_ds_bundle.js").write_text("this is not valid JS; import('nope')")

    original_read_text = Path.read_text

    def spying_read_text(self, *args, **kwargs):
        assert self.suffix not in (".js", ".jsx", ".ts"), f"reader opened {self}"
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spying_read_text)

    export = read_export(export_dir)
    assert any(t.name == "--ct-black" for t in export.tokens)


# -- read failures fail closed (non-UTF-8 / unreadable) ---------------------
#
# `_read_manifest` and `_from_css_fallback` both used to catch only
# OSError around their `read_text(encoding="utf-8")` calls -- a non-UTF-8
# file (or any other decode failure) raises UnicodeDecodeError, which is
# not an OSError, so it bypassed DesignImportError straight into a
# traceback. An untrusted export can contain either, so both paths must
# fail closed the same way an unreadable file already does.


def test_manifest_invalid_utf8_raises_design_import_error(tmp_path):
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    (export_dir / "_ds_manifest.json").write_bytes(b"\xff\xfe not valid utf-8 {}")

    with pytest.raises(DesignImportError, match="cannot read manifest"):
        read_export(export_dir)


def test_css_fallback_invalid_utf8_raises_design_import_error(tmp_path):
    export_dir = tmp_path / "export"
    (export_dir / "tokens").mkdir(parents=True)
    (export_dir / "tokens" / "colors.css").write_bytes(
        b"\xff\xfe :root { --x: 1px; }"
    )

    with pytest.raises(DesignImportError, match="cannot read"):
        read_export(export_dir)


def test_manifest_unreadable_raises_design_import_error(tmp_path, monkeypatch):
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    (export_dir / "_ds_manifest.json").write_text("{}")

    def raising_read_text(self, *args, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr(Path, "read_text", raising_read_text)

    with pytest.raises(DesignImportError, match="cannot read manifest"):
        read_export(export_dir)
