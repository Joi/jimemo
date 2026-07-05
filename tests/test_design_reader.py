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


# -- security: value sanitization -----------------------------------------


def _manifest_with_token_value(tmp_path: Path, value: str) -> Path:
    export_dir = tmp_path / "malicious-export"
    export_dir.mkdir()
    manifest = {
        "namespace": "Evil",
        "tokens": [
            {"name": "--evil-token", "value": value, "kind": "color", "definedIn": "tokens/colors.css"}
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


def test_chiba_fixture_fonts_still_import_cleanly():
    # Every real font in the checked-in fixture is legit (Finder / Ro NOW
    # Std, weights 300/400/500/700/900, style normal), so the fix must not
    # regress it.
    export = read_export(FIXTURE_DIR)
    families = {f.family for f in export.fonts}
    assert "Finder" in families
    assert "Ro NOW Std" in families
    assert {b.family for b in export.brand_fonts}  # brand fonts parsed, none rejected


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
