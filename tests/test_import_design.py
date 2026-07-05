import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo import inline
from jimemo.cli import main
from jimemo.design.importer import import_design, slugify_name
from jimemo.errors import DesignImportError

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "design-export"
BRIEFING_SAMPLE = Path(__file__).parents[1] / "templates" / "briefing" / "sample" / "content.md"


def _copy_fixture(tmp_path: Path, name: str = "export") -> Path:
    dest = tmp_path / name
    shutil.copytree(FIXTURE_DIR, dest)
    return dest


def _manual_export(
    tmp_path: Path,
    *,
    dirname: str = "export",
    font_rel_path: str = "assets/fonts/Testy-Regular.ttf",
    write_font: bool = True,
    font_bytes: bytes = b"FAKEFONTDATA-NOT-A-REAL-FONT",
    font_files=None,
) -> Path:
    """A minimal hand-built export (manifest + one font reference) --
    small and self-contained, unlike the checked-in Chiba fixture, which
    has no font BINARIES on disk at all (see the plan's "no font
    binaries in the repo" constraint) and so can't exercise
    --embed-fonts on its own. `font_files` overrides the manifest's
    fonts[0].files list entirely (e.g. `[]`, for a family the export
    only names, with no file to embed); it defaults to `[font_rel_path]`.
    """
    files = [font_rel_path] if font_files is None else font_files
    export_dir = tmp_path / dirname
    export_dir.mkdir(parents=True)
    manifest = {
        "namespace": "TestBrand",
        "tokens": [
            {"name": "--tb-ink", "value": "#111111", "kind": "color", "definedIn": "tokens/colors.css"},
            {"name": "--tb-paper", "value": "#eeeeee", "kind": "color", "definedIn": "tokens/colors.css"},
            {"name": "--tb-font", "value": '"Testy", sans-serif', "kind": "font", "definedIn": "tokens/fonts.css"},
        ],
        "fonts": [
            {
                "family": "Testy",
                "weight": "400",
                "style": "normal",
                "cssPath": "tokens/fonts.css",
                "files": files,
            }
        ],
        "brandFonts": [
            {"family": "Testy", "status": "ok", "tokens": ["--tb-font"]}
        ],
        "globalCssPaths": [],
        "themes": [],
    }
    (export_dir / "_ds_manifest.json").write_text(json.dumps(manifest))
    if write_font:
        font_path = export_dir / font_rel_path
        font_path.parent.mkdir(parents=True, exist_ok=True)
        font_path.write_bytes(font_bytes)
    return export_dir


# -- slugify_name ------------------------------------------------------


def test_slugify_name_collapses_and_lowercases():
    assert slugify_name("ChibaTechDesignSystem_9e0e92") == "chibatechdesignsystem-9e0e92"
    assert slugify_name("My Brand!!") == "my-brand"


def test_slugify_name_empty_result_raises():
    with pytest.raises(DesignImportError):
        slugify_name("???")


# -- reserved theme names: collide with the toolkit's data-theme modes --
#
# `:root[data-theme="light"]` / `[data-theme="dark"]` (specificity 0-2-0)
# beat a generated theme's own `:root` block (0-1-0), so a theme named
# "light" or "dark" would load but have its role overrides silently
# overridden by the built-in mode tokens. Rejected outright instead.


def test_import_rejects_name_light(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(DesignImportError, match="reserved"):
        import_design(FIXTURE_DIR, name="light")
    assert not (tmp_path / ".jimemo" / "themes" / "light.css").exists()


def test_import_rejects_name_dark_case_insensitive(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(DesignImportError, match="reserved"):
        import_design(FIXTURE_DIR, name="Dark")
    assert not (tmp_path / ".jimemo" / "themes" / "dark.css").exists()


def test_import_rejects_name_that_slugifies_to_dark(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(DesignImportError, match="reserved"):
        import_design(FIXTURE_DIR, name="_DARK_")


def test_import_rejects_default_name_derived_from_namespace(tmp_path, monkeypatch):
    # No --name: the reserved check must also catch the name derived
    # from the export's own namespace, not just an explicit --name.
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _injection_manifest_export(tmp_path, namespace="Dark")
    with pytest.raises(DesignImportError, match="reserved"):
        import_design(export_dir)
    assert not (tmp_path / ".jimemo" / "themes").exists()


def test_import_accepts_normal_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = import_design(FIXTURE_DIR, name="mybrand")
    assert result.name == "mybrand"
    assert (tmp_path / ".jimemo" / "themes" / "mybrand.css").is_file()


def test_cli_import_design_reserved_name_returns_rc1_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = main(["import-design", str(FIXTURE_DIR), "--name", "light"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "reserved" in err
    assert "Traceback" not in err
    assert not (tmp_path / ".jimemo" / "themes" / "light.css").exists()


# -- basic import: writes the personal theme ----------------------------


def test_import_writes_personal_theme_with_mapped_font_and_accent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = import_design(FIXTURE_DIR, name="chiba")

    expected_path = tmp_path / ".jimemo" / "themes" / "chiba.css"
    assert result.theme_path == expected_path
    assert expected_path.is_file()
    css = expected_path.read_text(encoding="utf-8")
    assert '--jm-font-prose: "Finder"' in css
    assert "--jm-accent: var(--ct-blue-core)" in css


def test_import_default_name_from_export_namespace(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = import_design(FIXTURE_DIR)
    assert result.name == "chibatechdesignsystem-9e0e92"
    assert (tmp_path / ".jimemo" / "themes" / f"{result.name}.css").is_file()


def test_import_default_name_from_dir_when_no_namespace(tmp_path, monkeypatch):
    # No manifest -> the css-fallback reader path, which never yields a
    # namespace, so the directory name is the fallback.
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _copy_fixture(tmp_path, name="My Brand Export")
    (export_dir / "_ds_manifest.json").unlink()

    result = import_design(export_dir)
    assert result.name == "my-brand-export"


def test_import_missing_export_dir_raises_clean_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(DesignImportError, match="not found"):
        import_design(tmp_path / "nope")


def test_import_header_lists_mappings(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = import_design(FIXTURE_DIR, name="chiba")
    assert "Auto-mapped roles" in result.header
    assert "--ct-blue-core -> --jm-accent" in result.header


# -- theme-write filesystem errors: clean DesignImportError, no traceback --


def test_import_theme_dir_mkdir_oserror_wrapped(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    def raising_mkdir(self, *args, **kwargs):
        raise OSError("Read-only file system")

    monkeypatch.setattr(Path, "mkdir", raising_mkdir)

    with pytest.raises(DesignImportError, match="could not write theme"):
        import_design(FIXTURE_DIR, name="chiba")


def test_import_theme_write_text_oserror_wrapped(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    def raising_write_text(self, *args, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr(Path, "write_text", raising_write_text)

    with pytest.raises(DesignImportError, match="could not write theme"):
        import_design(FIXTURE_DIR, name="chiba")


def test_cli_theme_write_oserror_returns_rc1_not_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))

    def raising_write_text(self, *args, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr(Path, "write_text", raising_write_text)

    rc = main(["import-design", str(FIXTURE_DIR), "--name", "chiba"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "could not write theme" in err
    assert "Traceback" not in err


# -- render with an imported theme (end-to-end via the CLI) -------------


def test_render_with_imported_theme_uses_brand_font_and_accent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    import_design(FIXTURE_DIR, name="chiba")

    out_path = tmp_path / "out.html"
    rc = main([
        "render", "briefing", str(BRIEFING_SAMPLE),
        "--theme", "chiba", "-o", str(out_path),
    ])
    assert rc == 0

    html = out_path.read_text(encoding="utf-8")
    assert "Finder" in html
    assert "#4c4499" in html  # --ct-blue-core, the accent's underlying value
    # self-contained: nothing fetched at view time
    assert "http://" not in html
    assert "https://" not in html


# -- theme resolution: personal dir vs. repo toolkit/themes/ -------------


def test_repo_theme_still_resolves_with_no_personal_theme(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    fake_toolkit = tmp_path / "toolkit"
    shutil.copytree(inline.TOOLKIT_DIR, fake_toolkit)
    (fake_toolkit / "themes").mkdir(exist_ok=True)
    (fake_toolkit / "themes" / "housetheme.css").write_text(
        ":root { --jm-accent: #123456; }\n", encoding="utf-8"
    )
    monkeypatch.setattr(inline, "TOOLKIT_DIR", fake_toolkit)

    css = inline.assemble_css({"components": []}, theme="housetheme")
    assert "--jm-accent: #123456;" in css


def test_personal_theme_wins_over_repo_theme_of_the_same_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    personal_dir = tmp_path / ".jimemo" / "themes"
    personal_dir.mkdir(parents=True)
    (personal_dir / "shared.css").write_text(
        ":root { --jm-accent: #111111; }\n", encoding="utf-8"
    )

    fake_toolkit = tmp_path / "toolkit"
    shutil.copytree(inline.TOOLKIT_DIR, fake_toolkit)
    (fake_toolkit / "themes").mkdir(exist_ok=True)
    (fake_toolkit / "themes" / "shared.css").write_text(
        ":root { --jm-accent: #222222; }\n", encoding="utf-8"
    )
    monkeypatch.setattr(inline, "TOOLKIT_DIR", fake_toolkit)

    css = inline.assemble_css({"components": []}, theme="shared")
    assert "#111111" in css
    assert "#222222" not in css


# -- --embed-fonts: happy path -------------------------------------------


def test_embed_fonts_appends_font_face_data_uri(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _manual_export(tmp_path)

    result = import_design(export_dir, name="testy", embed_fonts=True)

    assert "@font-face" in result.css
    assert 'font-family: "Testy"' in result.css
    assert "data:font/ttf;base64," in result.css
    assert result.embedded_font_families == ["Testy"]
    assert result.embedded_bytes == len(b"FAKEFONTDATA-NOT-A-REAL-FONT")
    # the theme file is the only place the bytes land, and it lives
    # entirely under the (monkeypatched) HOME, never under the repo
    assert result.theme_path.is_relative_to(tmp_path)

    # the generated CSS (with the embedded font) still passes the same
    # self-contained lint a rendered page's <style> block is held to
    from jimemo.lint import css_reference_errors
    assert css_reference_errors(result.css) == []


def test_embed_fonts_with_no_files_listed_notes_nothing_to_embed(tmp_path, monkeypatch):
    # A brand font the export names but ships no file for (as opposed to
    # a listed file that's simply missing on disk -- that's the "missing
    # file" error case below, not "nothing to embed").
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _manual_export(tmp_path, write_font=False, font_files=[])

    result = import_design(export_dir, name="testy", embed_fonts=True)
    assert result.embedded_font_families == []
    assert "@font-face" not in result.css


def test_cli_embed_fonts_prints_licensing_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _manual_export(tmp_path)

    rc = main(["import-design", str(export_dir), "--name", "testy", "--embed-fonts"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "LICENSING" in out
    assert "Testy" in out
    assert "wrote theme:" in out

    theme_path = tmp_path / ".jimemo" / "themes" / "testy.css"
    assert theme_path.is_file()
    assert "@font-face" in theme_path.read_text(encoding="utf-8")


def test_cli_without_embed_fonts_notes_family_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _manual_export(tmp_path)

    rc = main(["import-design", str(export_dir), "--name", "testy"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "--embed-fonts" in out
    assert "LICENSING" not in out


# -- --embed-fonts: missing / malformed font file ------------------------


def test_embed_fonts_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _manual_export(tmp_path, write_font=False)

    with pytest.raises(DesignImportError, match="not found"):
        import_design(export_dir, name="testy", embed_fonts=True)


def test_embed_fonts_unreadable_file_raises_design_import_error(tmp_path, monkeypatch):
    # A resolved, existing, extension-valid font file whose *read* fails
    # (permission denied, I/O error, ...) used to raise a raw OSError past
    # cmd_import_design's DesignImportError-only catch -- _resolve_font_file
    # validates the path, but nothing wrapped the read_bytes() itself.
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _manual_export(tmp_path)

    original_read_bytes = Path.read_bytes

    def raising_read_bytes(self, *args, **kwargs):
        if self.suffix == ".ttf":
            raise OSError("Permission denied")
        return original_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", raising_read_bytes)

    with pytest.raises(DesignImportError, match="could not read font file"):
        import_design(export_dir, name="testy", embed_fonts=True)


# -- --embed-fonts: security -- font paths confined to the export dir ----


def test_embed_fonts_rejects_traversal_even_when_target_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "evil.ttf").write_bytes(b"SHOULD-NEVER-BE-READ")

    export_dir = _manual_export(
        tmp_path, font_rel_path="../outside/evil.ttf", write_font=False
    )

    original_read_bytes = Path.read_bytes

    def spying_read_bytes(self, *args, **kwargs):
        assert "outside" not in self.parts, f"reader escaped the export dir: {self}"
        return original_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", spying_read_bytes)

    with pytest.raises(DesignImportError, match="escapes"):
        import_design(export_dir, name="evil", embed_fonts=True)


def test_embed_fonts_rejects_absolute_font_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    absolute_evil = outside_dir / "evil2.ttf"
    absolute_evil.write_bytes(b"SHOULD-NEVER-BE-READ")

    export_dir = _manual_export(
        tmp_path, font_rel_path=str(absolute_evil), write_font=False
    )

    with pytest.raises(DesignImportError, match="escapes"):
        import_design(export_dir, name="evil2", embed_fonts=True)


def test_embed_fonts_rejects_non_font_extension(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _manual_export(
        tmp_path, font_rel_path="assets/fonts/not-a-font.exe", write_font=True
    )

    with pytest.raises(DesignImportError, match="unrecognized extension"):
        import_design(export_dir, name="evil3", embed_fonts=True)


# -- --embed-fonts: css-fallback path (no manifest) -----------------------
#
# A CSS url() is relative to the CSS FILE's directory (tokens/fonts.css
# saying ../assets/fonts/X.ttf means <export>/assets/fonts/X.ttf), while
# the manifest's fonts[].files are export-root-relative. The reader
# reconciles the two forms; before it did, --embed-fonts on a
# manifest-less export mis-read every valid ../ font url as an escape.


def _manifestless_font_export(
    tmp_path: Path,
    *,
    font_url: str = "../assets/fonts/Testy-Regular.ttf",
    write_font: bool = True,
) -> Path:
    export_dir = tmp_path / "cssexport"
    (export_dir / "tokens").mkdir(parents=True)
    (export_dir / "tokens" / "colors.css").write_text(
        ":root {\n  --xb-ink: #111111;\n  --xb-paper: #eeeeee;\n}\n"
    )
    (export_dir / "tokens" / "fonts.css").write_text(
        "@font-face {\n"
        '  font-family: "Testy";\n'
        '  src: url("%s") format("truetype");\n'
        "  font-weight: 400;\n"
        "  font-style: normal;\n"
        "}\n" % font_url
    )
    if write_font:
        font_path = export_dir / "assets" / "fonts" / "Testy-Regular.ttf"
        font_path.parent.mkdir(parents=True)
        font_path.write_bytes(b"FAKEFONTDATA-NOT-A-REAL-FONT")
    return export_dir


def test_embed_fonts_css_fallback_resolves_url_relative_to_css_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _manifestless_font_export(tmp_path)

    result = import_design(export_dir, name="cssfb", embed_fonts=True)

    assert "@font-face" in result.css
    assert 'font-family: "Testy"' in result.css
    assert "data:font/ttf;base64," in result.css
    assert result.embedded_font_families == ["Testy"]
    assert result.embedded_bytes == len(b"FAKEFONTDATA-NOT-A-REAL-FONT")

    # the embedded output passes the same structural shape gate
    # build_theme's own output is held to (:root/@font-face blocks only)
    from jimemo.design.mapping import theme_structure_errors
    assert theme_structure_errors(result.css) == []


def test_embed_fonts_css_fallback_real_escape_still_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "outside.ttf").write_bytes(b"SHOULD-NEVER-BE-READ")
    export_dir = _manifestless_font_export(
        tmp_path, font_url="../../outside.ttf", write_font=False
    )

    with pytest.raises(DesignImportError, match="escapes"):
        import_design(export_dir, name="evil", embed_fonts=True)
    assert not (tmp_path / ".jimemo" / "themes" / "evil.css").exists()


# -- security: token-name / namespace CSS injection (end-to-end) ----------


def _injection_manifest_export(tmp_path: Path, *, token_name: str = "--ev-ink", namespace: str = "Evil") -> Path:
    export_dir = tmp_path / "inj-export"
    export_dir.mkdir()
    manifest = {
        "namespace": namespace,
        "tokens": [{"name": token_name, "value": "#111111", "kind": "color"}],
        "fonts": [],
        "brandFonts": [],
        "globalCssPaths": [],
        "themes": [],
    }
    (export_dir / "_ds_manifest.json").write_text(json.dumps(manifest))
    return export_dir


def test_cli_import_design_token_name_injection_rc1_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _injection_manifest_export(
        tmp_path, token_name="x: red } body { display:none } :root{ --y"
    )

    rc = main(["import-design", str(export_dir), "--name", "evil"])
    assert rc == 1
    assert not (tmp_path / ".jimemo" / "themes" / "evil.css").exists()


def test_cli_import_design_namespace_injection_rc1_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _injection_manifest_export(
        tmp_path, namespace="Evil*/}body{display:none}/*"
    )

    rc = main(["import-design", str(export_dir), "--name", "evil"])
    assert rc == 1
    themes_dir = tmp_path / ".jimemo" / "themes"
    assert not themes_dir.exists() or not list(themes_dir.iterdir())


# -- security: font-metadata CSS injection (end-to-end) ------------------


def _malicious_font_export(tmp_path: Path) -> Path:
    """A manifest whose font weight breaks out of the @font-face block --
    the reviewer's proof-of-concept, run through the whole importer."""
    export_dir = tmp_path / "evil-export"
    export_dir.mkdir()
    manifest = {
        "namespace": "Evil",
        "tokens": [
            {"name": "--ev-ink", "value": "#111111", "kind": "color"},
            {"name": "--ev-font", "value": '"Evil", sans-serif', "kind": "font"},
        ],
        "fonts": [
            {
                "family": "Evil",
                "weight": "400} body{display:none} @font-face{font-weight:400",
                "style": "normal",
                "files": ["assets/fonts/Evil.ttf"],
            }
        ],
        "brandFonts": [{"family": "Evil", "status": "ok", "tokens": ["--ev-font"]}],
        "globalCssPaths": [],
        "themes": [],
    }
    (export_dir / "_ds_manifest.json").write_text(json.dumps(manifest))
    font = export_dir / "assets" / "fonts" / "Evil.ttf"
    font.parent.mkdir(parents=True)
    font.write_bytes(b"FAKE")
    return export_dir


def test_import_design_blocks_font_metadata_injection(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _malicious_font_export(tmp_path)

    with pytest.raises(DesignImportError, match="weight"):
        import_design(export_dir, name="evil", embed_fonts=True)

    # fail-closed: nothing was written
    assert not (tmp_path / ".jimemo" / "themes" / "evil.css").exists()


def test_cli_import_design_font_injection_returns_rc1_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _malicious_font_export(tmp_path)

    rc = main(["import-design", str(export_dir), "--name", "evil", "--embed-fonts"])
    assert rc == 1
    theme = tmp_path / ".jimemo" / "themes" / "evil.css"
    assert not theme.exists()


# -- security: malformed manifest shape fails closed (not a traceback) ----


def test_cli_import_design_malformed_manifest_shape_returns_rc1(tmp_path, monkeypatch, capsys):
    # {"fonts": 1} used to reach an unguarded `for f in 1 or []:` in the
    # reader and raise a raw TypeError -- cmd_import_design only catches
    # DesignImportError, so that would have escaped as an unhandled
    # traceback instead of a clean rc=1.
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = tmp_path / "malformed-export"
    export_dir.mkdir()
    manifest = {
        "namespace": "Evil",
        "tokens": [{"name": "--ok", "value": "#111111", "kind": "color"}],
        "fonts": 1,
        "brandFonts": [],
        "globalCssPaths": [],
        "themes": [],
    }
    (export_dir / "_ds_manifest.json").write_text(json.dumps(manifest))

    rc = main(["import-design", str(export_dir), "--name", "evil"])

    assert rc == 1
    assert not (tmp_path / ".jimemo" / "themes" / "evil.css").exists()
    assert "Traceback" not in capsys.readouterr().err
