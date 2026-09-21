import base64
import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo import inline
from jimemo.cli import main
from jimemo.design.importer import (
    SkippedFontFace,
    _embed_fonts,
    design_systems_dir,
    import_design,
    resolve_from_name,
    slugify_name,
)
from jimemo.design.reader import (
    THEME_NAME_RE,
    DesignExport,
    FontFace,
    read_export,
)
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
    small and self-contained, unlike the checked-in fixture, which
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


# The (family, weight, style) of every @font-face block _font_face_block
# emits, in order -- identity assertions for face-selection tests (count
# alone can't tell WHICH faces were embedded).
_EMBEDDED_FACE_RE = re.compile(
    r'@font-face \{\n  font-family: "([^"]+)";\n'
    r"  font-weight: ([^;]+);\n  font-style: ([^;]+);"
)


def _faces_export(
    tmp_path: Path,
    *,
    dirname: str = "faces-export",
    faces,
    font_token_value: str = '"Testy", sans-serif',
    brand_family: str = "Testy",
    brand_fonts=None,
) -> Path:
    """A manifest export carrying an arbitrary fonts[] list -- the shape
    face-selection needs, since a real export lists many faces of one
    family across weights and styles. `faces` is a list of (family,
    weight, style, filename) tuples; each face's file is written with
    DISTINCT bytes ("FACE-<family>-<weight>-<style>") so a test can
    prove the right face's payload landed in the theme, not just any
    font-family-shaped text. A filename beginning with "../" becomes
    the files[] entry verbatim (export-root-relative) for the traversal
    cases; its bytes are still written where they land. `brand_fonts`
    overrides the default single-entry brandFonts list ([] exercises
    the no-brand-metadata inference path)."""
    export_dir = tmp_path / dirname
    export_dir.mkdir(parents=True)
    fonts = []
    for family, weight, style, filename in faces:
        rel = filename if filename.startswith("../") else "assets/fonts/" + filename
        fonts.append(
            {"family": family, "weight": weight, "style": style, "files": [rel]}
        )
        font_path = export_dir / rel
        font_path.parent.mkdir(parents=True, exist_ok=True)
        font_path.write_bytes(
            "FACE-{family}-{weight}-{style}".format(
                family=family, weight=weight, style=style
            ).encode("utf-8")
        )
    if brand_fonts is None:
        brand_fonts = [{"family": brand_family, "status": "ok", "tokens": ["--tb-font"]}]
    manifest = {
        "namespace": "TestBrand",
        "tokens": [
            {"name": "--tb-ink", "value": "#111111", "kind": "color"},
            {"name": "--tb-paper", "value": "#eeeeee", "kind": "color"},
            {"name": "--tb-font", "value": font_token_value, "kind": "font"},
        ],
        "fonts": fonts,
        "brandFonts": brand_fonts,
        "globalCssPaths": [],
        "themes": [],
    }
    (export_dir / "_ds_manifest.json").write_text(json.dumps(manifest))
    return export_dir


# -- slugify_name ------------------------------------------------------


def test_slugify_name_collapses_and_lowercases():
    assert slugify_name("NorthwindFieldKit_7b3f21") == "northwindfieldkit-7b3f21"
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


def test_import_rejects_uppercase_explicit_name_as_invalid_not_reserved(tmp_path, monkeypatch):
    # An explicit --name is validated as-is (no more silent lowercasing --
    # see test_import_rejects_name_MyBrand_style_case below), so "Dark"
    # fails the slug-shape check before it would ever reach the reserved-
    # name check -- still rejected, but for the right reason.
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(DesignImportError, match="not a valid theme name"):
        import_design(FIXTURE_DIR, name="Dark")
    assert not (tmp_path / ".jimemo" / "themes" / "dark.css").exists()


def test_import_rejects_name_that_used_to_slugify_to_dark(tmp_path, monkeypatch):
    # Previously an explicit --name was silently slugified before the
    # reserved-name check ran, so "_DARK_" -> "dark" got caught there.
    # Now an explicit --name is validated verbatim (no slugification),
    # so "_DARK_" fails the slug-shape check first instead.
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(DesignImportError, match="not a valid theme name"):
        import_design(FIXTURE_DIR, name="_DARK_")
    assert not (tmp_path / ".jimemo" / "themes" / "dark.css").exists()


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


# -- import/render name-rule consistency ---------------------------------
#
# The bug: `import-design --name TestMixed` used to succeed by silently
# slugifying to `testmixed.css`, while `render --theme TestMixed` rejected
# the same string outright -- the two commands disagreed about what a
# valid theme name is. Fixed by having import-design validate an explicit
# --name against the same shape (`invalid_theme_name_reason`) render
# enforces, and reject it outright rather than transform it.


def test_import_rejects_mixed_case_explicit_name_cleanly(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(DesignImportError, match="not a valid theme name") as exc_info:
        import_design(FIXTURE_DIR, name="TestMixed")
    # names the invalid name and shows the expected form, same as render's
    # rejection of the same string (see test_render_rejects_same_invalid_name below)
    assert "'TestMixed'" in str(exc_info.value)
    assert "northwind-field-kit" in str(exc_info.value)
    assert not (tmp_path / ".jimemo" / "themes").exists()


def test_cli_import_design_rejects_mixed_case_name_rc1_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = main(["import-design", str(FIXTURE_DIR), "--name", "MyBrand"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not a valid theme name" in err
    assert "'MyBrand'" in err
    assert "Traceback" not in err
    assert not (tmp_path / ".jimemo" / "themes").exists()


def test_import_accepts_valid_slug_explicit_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = import_design(FIXTURE_DIR, name="my-brand")
    assert result.name == "my-brand"
    assert (tmp_path / ".jimemo" / "themes" / "my-brand.css").is_file()


def test_import_no_name_still_auto_derives_valid_slug(tmp_path, monkeypatch):
    # Regression: omitting --name must still work, deriving+slugifying
    # from the export's namespace (only the EXPLICIT --name path is now
    # validated-and-rejected rather than transformed).
    monkeypatch.setenv("HOME", str(tmp_path))
    result = import_design(FIXTURE_DIR)
    assert result.name == "northwindfieldkit-7b3f21"
    assert THEME_NAME_RE.match(result.name)
    assert (tmp_path / ".jimemo" / "themes" / f"{result.name}.css").is_file()


def test_render_rejects_same_invalid_name_import_rejects(tmp_path, monkeypatch):
    # Round-trip: a name import-design REJECTS, render --theme also
    # rejects (with the same "not a valid theme name" wording) -- no
    # theme file was ever written for it to resolve to anyway.
    monkeypatch.setenv("HOME", str(tmp_path))
    out_path = tmp_path / "out.html"
    for bad_name in ("TestMixed", "MyBrand"):
        rc = main([
            "render", "briefing", str(BRIEFING_SAMPLE),
            "--theme", bad_name, "-o", str(out_path),
        ])
        assert rc == 1
        assert not out_path.exists()


def test_render_accepts_name_import_accepted(tmp_path, monkeypatch):
    # Round-trip, the other direction: a name import-design ACCEPTS is
    # always accepted by render --theme.
    monkeypatch.setenv("HOME", str(tmp_path))
    for good_name in ("my-brand", "northwind"):
        import_design(FIXTURE_DIR, name=good_name)
        out_path = tmp_path / f"out-{good_name}.html"
        rc = main([
            "render", "briefing", str(BRIEFING_SAMPLE),
            "--theme", good_name, "-o", str(out_path),
        ])
        assert rc == 0
        assert out_path.is_file()


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
    result = import_design(FIXTURE_DIR, name="northwind")

    expected_path = tmp_path / ".jimemo" / "themes" / "northwind.css"
    assert result.theme_path == expected_path
    assert expected_path.is_file()
    css = expected_path.read_text(encoding="utf-8")
    assert '--jm-font-prose: "Northwind Sans"' in css
    assert "--jm-accent: var(--nw-blue-core)" in css


def test_import_default_name_from_export_namespace(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = import_design(FIXTURE_DIR)
    assert result.name == "northwindfieldkit-7b3f21"
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
    result = import_design(FIXTURE_DIR, name="northwind")
    assert "Auto-mapped roles" in result.header
    assert "--nw-blue-core -> --jm-accent" in result.header


# -- theme-write filesystem errors: clean DesignImportError, no traceback --


def test_import_theme_dir_mkdir_oserror_wrapped(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    def raising_mkdir(self, *args, **kwargs):
        raise OSError("Read-only file system")

    monkeypatch.setattr(Path, "mkdir", raising_mkdir)

    with pytest.raises(DesignImportError, match="could not write theme"):
        import_design(FIXTURE_DIR, name="northwind")


def test_import_theme_write_text_oserror_wrapped(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    def raising_write_text(self, *args, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr(Path, "write_text", raising_write_text)

    with pytest.raises(DesignImportError, match="could not write theme"):
        import_design(FIXTURE_DIR, name="northwind")


def test_cli_theme_write_oserror_returns_rc1_not_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))

    def raising_write_text(self, *args, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr(Path, "write_text", raising_write_text)

    rc = main(["import-design", str(FIXTURE_DIR), "--name", "northwind"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "could not write theme" in err
    assert "Traceback" not in err


# -- render with an imported theme (end-to-end via the CLI) -------------


def test_render_with_imported_theme_uses_brand_font_and_accent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    import_design(FIXTURE_DIR, name="northwind")

    out_path = tmp_path / "out.html"
    rc = main([
        "render", "briefing", str(BRIEFING_SAMPLE),
        "--theme", "northwind", "-o", str(out_path),
    ])
    assert rc == 0

    html = out_path.read_text(encoding="utf-8")
    assert "Northwind Sans" in html
    assert "#33418f" in html  # --nw-blue-core, the accent's underlying value
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


# -- --embed-fonts: embed only faces the theme references ---------------
#
# _embed_fonts used to append a block for EVERY face the export listed;
# a real export carries a dozen faces across weights and styles and a
# self-contained theme came to megabytes for faces the generated theme
# never uses. A face is embedded only when the theme's own CSS names its
# family (font declarations / custom properties, quote- and
# case-insensitively; a family list references every concrete family in
# it, never a generic) AND its weight/style is one the theme states -- a theme that
# states no weight keeps the regular face (400 / normal) and nothing
# else. Skipped faces are reported on ImportResult, and a skipped face's
# file is never resolved, opened, or read.


def test_embed_fonts_embeds_only_weights_and_styles_the_theme_uses(tmp_path, monkeypatch):
    # build_theme's own output states no weight (its font roles are
    # family stacks), so this drives _embed_fonts directly with a theme
    # CSS that DOES -- the same shape build_theme emits, plus the two
    # font-weight declarations a hand-refined theme carries.
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _faces_export(
        tmp_path,
        faces=[
            ("Testy", "400", "normal", "Testy-Regular.ttf"),
            ("Testy", "400", "italic", "Testy-Italic.ttf"),
            ("Testy", "700", "normal", "Testy-Bold.ttf"),
            ("Testy", "300", "normal", "Testy-Light.ttf"),
        ],
    )
    export = read_export(export_dir)
    css = (
        "/* jimemo theme 'testy' -- auto-generated */\n"
        ":root {\n"
        '  --tb-font: "Testy", sans-serif;\n'
        "  font-weight: 400;\n"
        "  font-weight: 700;\n"
        "}\n"
    )

    embedded, families, nbytes, skipped = _embed_fonts(css, export, export_dir)

    # face COUNT ... (never a byte size: identity, not bulk, is the contract)
    assert embedded.count("@font-face {") == 2
    # ... and IDENTITY: exactly the 400/700 normal faces, in export order
    assert _EMBEDDED_FACE_RE.findall(embedded) == [
        ("Testy", "400", "normal"),
        ("Testy", "700", "normal"),
    ]
    # the embedded payloads really are those faces' bytes, not just any
    # two font-family-shaped blocks
    for weight in ("400", "700"):
        payload = "FACE-Testy-{weight}-normal".format(weight=weight)
        assert base64.b64encode(payload.encode("ascii")).decode("ascii") in embedded
    assert families == ["Testy", "Testy"]
    assert skipped == [
        SkippedFontFace(family="Testy", weight="400", style="italic"),
        SkippedFontFace(family="Testy", weight="300", style="normal"),
    ]


def test_embed_fonts_embeds_the_italic_and_oblique_faces_a_theme_states(tmp_path, monkeypatch):
    # The style half of the rule, positively: a theme stating
    # font-style italic (or oblique, with an angle) gets that face, and
    # the normal face it no longer states is skipped.
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _faces_export(
        tmp_path,
        faces=[
            ("Testy", "400", "normal", "Testy-Regular.ttf"),
            ("Testy", "400", "italic", "Testy-Italic.ttf"),
            ("Testy", "400", "oblique 10deg", "Testy-Oblique.ttf"),
        ],
    )
    export = read_export(export_dir)
    theme = ':root {\n  --tb-font: "Testy", sans-serif;\n  font-style: %s;\n}\n'

    embedded, _families, _nbytes, skipped = _embed_fonts(
        theme % "italic", export, export_dir
    )
    assert _EMBEDDED_FACE_RE.findall(embedded) == [("Testy", "400", "italic")]
    assert [f.style for f in skipped] == ["normal", "oblique 10deg"]

    embedded, _families, _nbytes, skipped = _embed_fonts(
        theme % "oblique", export, export_dir
    )
    assert _EMBEDDED_FACE_RE.findall(embedded) == [("Testy", "400", "oblique 10deg")]
    assert [f.style for f in skipped] == ["normal", "italic"]


def test_embed_fonts_family_without_the_stated_weight_gets_the_nearest(tmp_path, monkeypatch):
    # A referenced family with no exact match embeds the face a browser
    # would settle on (CSS Fonts 4 weight matching), not nothing. The
    # theme states no weight, so 400 is wanted: 400 looks up to 500
    # first, then down, then above 500.
    monkeypatch.setenv("HOME", str(tmp_path))
    cases = [
        (["700"], "700"),                  # the Bold-only display family
        (["300", "500", "700"], "500"),    # up to 500 before down
        (["300", "600"], "300"),           # then down before above 500
    ]
    for i, (shipped, expected) in enumerate(cases):
        export_dir = _faces_export(
            tmp_path,
            dirname="nearest-{}".format(i),
            faces=[
                ("Testy", w, "normal", "Testy-{}.ttf".format(w)) for w in shipped
            ],
        )

        result = import_design(
            export_dir, name="nearest-{}".format(i), embed_fonts=True
        )

        assert _EMBEDDED_FACE_RE.findall(result.css) == [
            ("Testy", expected, "normal")
        ], shipped
        assert sorted(f.weight for f in result.skipped_font_faces) == sorted(
            w for w in shipped if w != expected
        )


def test_embed_fonts_nearest_weight_leaves_exact_matches_and_styles_alone(
    tmp_path, monkeypatch
):
    # The fallback is per family and only for a family with NO exact
    # match: Testy has its 400, so its 700 stays skipped while Display
    # (Bold only) embeds its 700. A style is never substituted: Slanty
    # ships italics only and the theme wants normal, so nothing of it
    # embeds.
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _faces_export(
        tmp_path,
        faces=[
            ("Testy", "400", "normal", "Testy-Regular.ttf"),
            ("Testy", "700", "normal", "Testy-Bold.ttf"),
            ("Display", "700", "normal", "Display-Bold.ttf"),
            ("Slanty", "400", "italic", "Slanty-Italic.ttf"),
        ],
        font_token_value='"Testy", "Display", "Slanty", sans-serif',
        brand_fonts=[],
    )

    result = import_design(export_dir, name="mixed", embed_fonts=True)

    assert _EMBEDDED_FACE_RE.findall(result.css) == [
        ("Testy", "400", "normal"),
        ("Display", "700", "normal"),
    ]
    assert [(f.family, f.weight, f.style) for f in result.skipped_font_faces] == [
        ("Testy", "700", "normal"),
        ("Slanty", "400", "italic"),
    ]


def test_embed_fonts_skips_family_the_theme_never_names(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _faces_export(
        tmp_path,
        faces=[
            ("Testy", "400", "normal", "Testy-Regular.ttf"),
            ("Ghost", "400", "normal", "Ghost-Regular.ttf"),
        ],
    )

    result = import_design(export_dir, name="ghosty", embed_fonts=True)

    assert result.embedded_font_families == ["Testy"]
    assert 'font-family: "Ghost"' not in result.css
    assert _EMBEDDED_FACE_RE.findall(result.css) == [("Testy", "400", "normal")]
    assert [(f.family, f.weight, f.style) for f in result.skipped_font_faces] == [
        ("Ghost", "400", "normal")
    ]


def test_skipped_font_faces_reported_on_import_result(tmp_path, monkeypatch):
    # A user who wanted one of the dropped weights must be able to see
    # it was dropped: ImportResult carries family/weight/style for every
    # face the reference rule removed, in export order.
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _faces_export(
        tmp_path,
        faces=[
            ("Testy", "400", "normal", "Testy-Regular.ttf"),
            ("Testy", "400", "italic", "Testy-Italic.ttf"),
            ("Testy", "700", "normal", "Testy-Bold.ttf"),
            ("Testy", "300", "normal", "Testy-Light.ttf"),
        ],
    )

    result = import_design(export_dir, name="skippy", embed_fonts=True)

    assert result.embedded_font_families == ["Testy"]
    assert _EMBEDDED_FACE_RE.findall(result.css) == [("Testy", "400", "normal")]
    assert [(f.family, f.weight, f.style) for f in result.skipped_font_faces] == [
        ("Testy", "400", "italic"),
        ("Testy", "700", "normal"),
        ("Testy", "300", "normal"),
    ]


def test_embed_fonts_no_weight_stated_keeps_only_regular_face(tmp_path, monkeypatch):
    # The theme states no font-weight anywhere (every theme build_theme
    # generates today), so only the regular face survives -- weight 400
    # spelled any of the ways an export spells it (400 / "normal" /
    # "regular" / unspecified), style normal. "regular" cannot pass
    # read_export's weight allowlist, so this export is hand-built the
    # way a looser reader would have shaped it and driven into
    # _embed_fonts directly.
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = tmp_path / "regfaces"
    export_dir.mkdir()
    faces = [
        FontFace("Testy", "400", "normal", ["assets/fonts/Testy-400.ttf"]),
        FontFace("Testy", "", "normal", ["assets/fonts/Testy-empty.ttf"]),
        FontFace("Testy", "normal", "normal", ["assets/fonts/Testy-normal.ttf"]),
        FontFace("Testy", "regular", "", ["assets/fonts/Testy-regular.ttf"]),
        FontFace("Testy", "700", "normal", ["assets/fonts/Testy-700.ttf"]),
        FontFace("Testy", "400", "italic", ["assets/fonts/Testy-italic.ttf"]),
    ]
    for face in faces:
        font_path = export_dir / face.files[0]
        font_path.parent.mkdir(parents=True, exist_ok=True)
        font_path.write_bytes(b"FAKEFONTDATA-NOT-A-REAL-FONT")
    export = DesignExport(
        tokens=[], fonts=faces, brand_fonts=[], namespace="TestBrand"
    )
    css = (
        "/* jimemo theme 'reggy' -- auto-generated */\n"
        ":root {\n"
        '  --jm-font-prose: "Testy", sans-serif;\n'
        "}\n"
    )

    embedded, families, nbytes, skipped = _embed_fonts(css, export, export_dir)

    # every regular spelling embeds (each is its own FontFace entry);
    # the 700 and the italic do not. _font_face_block prints an empty
    # weight/style as "normal".
    assert _EMBEDDED_FACE_RE.findall(embedded) == [
        ("Testy", "400", "normal"),
        ("Testy", "normal", "normal"),
        ("Testy", "normal", "normal"),
        ("Testy", "regular", "normal"),
    ]
    assert [(f.family, f.weight, f.style) for f in skipped] == [
        ("Testy", "700", "normal"),
        ("Testy", "400", "italic"),
    ]


def test_family_matching_robust_to_quotes_and_case(tmp_path, monkeypatch):
    # The theme may name the family double-quoted ("Inter"),
    # single-quoted ('Inter'), unquoted (Inter), or in a different case
    # (inter) than the export's FontFace.family -- all must match.
    # brandFonts is empty so the family reaches the theme through the
    # inference path (the token's own stack value), which is where the
    # unquoted and case-varied spellings actually occur.
    monkeypatch.setenv("HOME", str(tmp_path))
    spellings = [
        '"Inter", sans-serif',
        "'Inter', sans-serif",
        "Inter, sans-serif",
        '"inter", sans-serif',
    ]
    for i, token_value in enumerate(spellings):
        export_dir = _faces_export(
            tmp_path,
            dirname="q-export-{}".format(i),
            faces=[("Inter", "400", "normal", "Inter-Regular.ttf")],
            font_token_value=token_value,
            brand_fonts=[],
        )

        result = import_design(
            export_dir, name="quotey-{}".format(i), embed_fonts=True
        )

        assert _EMBEDDED_FACE_RE.findall(result.css) == [
            ("Inter", "400", "normal")
        ], token_value
        assert result.skipped_font_faces == []


def test_family_list_generics_are_not_references(tmp_path, monkeypatch):
    # `font-family: "Inter", system-ui, sans-serif` references Inter
    # only: unquoted generics never name a shippable face -- even one
    # the export actually ships files for.
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _faces_export(
        tmp_path,
        faces=[
            ("Inter", "400", "normal", "Inter-Regular.ttf"),
            ("system-ui", "400", "normal", "SystemUI-Regular.ttf"),
        ],
        font_token_value='"Inter", system-ui, sans-serif',
        brand_family="Inter",
    )

    result = import_design(export_dir, name="listy", embed_fonts=True)

    assert result.embedded_font_families == ["Inter"]
    assert _EMBEDDED_FACE_RE.findall(result.css) == [("Inter", "400", "normal")]
    assert [(f.family, f.weight, f.style) for f in result.skipped_font_faces] == [
        ("system-ui", "400", "normal")
    ]


def test_family_list_references_every_concrete_family(tmp_path, monkeypatch):
    # Fallback is per character: `"Latin Brand", "CJK Brand", sans-serif`
    # draws its kana from the second family while the first is available,
    # so both embed. A family the stack does not name is still skipped.
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _faces_export(
        tmp_path,
        faces=[
            ("Latin Brand", "400", "normal", "Latin-Regular.ttf"),
            ("CJK Brand", "400", "normal", "CJK-Regular.otf"),
            ("Decor", "400", "normal", "Decor.ttf"),
        ],
        font_token_value='"Latin Brand", "CJK Brand", sans-serif',
        brand_fonts=[],
    )

    result = import_design(export_dir, name="stacky", embed_fonts=True)

    assert _EMBEDDED_FACE_RE.findall(result.css) == [
        ("Latin Brand", "400", "normal"),
        ("CJK Brand", "400", "normal"),
    ]
    assert [(f.family, f.weight, f.style) for f in result.skipped_font_faces] == [
        ("Decor", "400", "normal")
    ]


def test_quoted_family_is_a_name_whatever_it_spells(tmp_path, monkeypatch):
    # Families the reader accepts and CSS reads as plain names: a comma
    # or parentheses inside the quotes, and a quoted generic keyword.
    monkeypatch.setenv("HOME", str(tmp_path))
    for i, family in enumerate(["ACME, Inc", "Brand (Display)", "serif"]):
        export_dir = _faces_export(
            tmp_path,
            dirname="named-export-{}".format(i),
            faces=[(family, "400", "normal", "Face-{}.ttf".format(i))],
            font_token_value='"{}", sans-serif'.format(family),
            brand_family=family,
        )

        result = import_design(
            export_dir, name="named-{}".format(i), embed_fonts=True
        )

        assert _EMBEDDED_FACE_RE.findall(result.css) == [
            (family, "400", "normal")
        ], family
        assert result.skipped_font_faces == []


def test_skipped_face_missing_or_traversal_file_never_touched(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))

    # a SKIPPED face whose file is missing: never resolved or opened,
    # so its absence cannot fail the import
    export_dir = _faces_export(
        tmp_path,
        dirname="miss-skipped",
        faces=[
            ("Testy", "400", "normal", "Testy-Regular.ttf"),
            ("Ghost", "400", "normal", "Ghost-Regular.ttf"),
        ],
    )
    (export_dir / "assets" / "fonts" / "Ghost-Regular.ttf").unlink()

    result = import_design(export_dir, name="missskip", embed_fonts=True)

    assert result.embedded_font_families == ["Testy"]
    assert [(f.family, f.weight, f.style) for f in result.skipped_font_faces] == [
        ("Ghost", "400", "normal")
    ]

    # a SKIPPED face with a traversal path: same -- and a spy proves the
    # file is never read at all
    export_dir = _faces_export(
        tmp_path,
        dirname="trav-skipped",
        faces=[
            ("Testy", "400", "normal", "Testy-Regular.ttf"),
            ("Ghost", "400", "normal", "../outside/evil.ttf"),
        ],
    )
    original_read_bytes = Path.read_bytes

    def spying_read_bytes(self, *args, **kwargs):
        assert "evil" not in self.name, f"skipped face file was read: {self}"
        return original_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", spying_read_bytes)

    result = import_design(export_dir, name="travskip", embed_fonts=True)

    assert result.embedded_font_families == ["Testy"]
    assert [(f.family, f.weight, f.style) for f in result.skipped_font_faces] == [
        ("Ghost", "400", "normal")
    ]
    monkeypatch.setattr(Path, "read_bytes", original_read_bytes)

    # the same problems on a REFERENCED face still raise exactly as
    # before -- nothing about the guards changed, only who reaches them
    missing_ref = _faces_export(
        tmp_path,
        dirname="miss-ref",
        faces=[("Testy", "400", "normal", "Testy-Regular.ttf")],
    )
    (missing_ref / "assets" / "fonts" / "Testy-Regular.ttf").unlink()
    with pytest.raises(DesignImportError, match="not found"):
        import_design(missing_ref, name="missref", embed_fonts=True)

    trav_ref = _faces_export(
        tmp_path,
        dirname="trav-ref",
        faces=[("Testy", "400", "normal", "../outside/evil.ttf")],
    )
    with pytest.raises(DesignImportError, match="escapes"):
        import_design(trav_ref, name="travref", embed_fonts=True)


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


def test_embed_fonts_checked_in_fixture_embeds_two_of_eight_faces(tmp_path, monkeypatch):
    # The checked-in export lists 8 faces with files (the shape behind the
    # 12MB theme) and ships no font binaries, so stand-in bytes are written
    # for each listed file. Count and identity, never a byte size.
    #
    # This also pins one judgment call. "Northwind Gothic JP" is named only
    # by --nw-font-jp, an export token the theme re-declares and no jimemo
    # role uses; the rule counts it as referenced, so its regular face
    # embeds. Over-embedding is the safe failure, and it costs one face
    # here (2 of 8, against 1 of 8 for a rule that followed role tokens
    # only). Narrowing the rule has to change this test.
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _copy_fixture(tmp_path)
    manifest = json.loads((export_dir / "_ds_manifest.json").read_text())
    listed = [f for f in manifest["fonts"] if f.get("files")]
    assert len(listed) == 8
    for face in listed:
        for rel in face["files"]:
            path = export_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"STAND-IN-" + rel.encode("ascii"))

    result = import_design(export_dir, name="northwind", embed_fonts=True)

    assert _EMBEDDED_FACE_RE.findall(result.css) == [
        ("Northwind Sans", "400", "normal"),
        ("Northwind Gothic JP", "400", "normal"),
    ]
    assert sorted((f.family, f.weight, f.style) for f in result.skipped_font_faces) == [
        ("Northwind Gothic JP", "700", "normal"),
        ("Northwind Mono", "400", "normal"),
        ("Northwind Sans", "300", "normal"),
        ("Northwind Sans", "700", "normal"),
        ("Northwind Sans Cond", "400", "normal"),
        ("Northwind Sans Cond", "700", "normal"),
    ]


def test_cli_embed_fonts_lists_skipped_faces_after_the_embedded_summary(
    tmp_path, monkeypatch, capsys
):
    # One line per skipped face (family / weight / style), after the
    # embedded families and byte count: a user who wanted the dropped
    # weight reads that it was dropped.
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _faces_export(
        tmp_path,
        faces=[
            ("Testy", "400", "normal", "Testy-Regular.ttf"),
            ("Testy", "700", "normal", "Testy-Bold.ttf"),
            ("Testy", "400", "italic", "Testy-Italic.ttf"),
            ("Decor", "", "", "Decor.ttf"),
        ],
    )

    rc = main(["import-design", str(export_dir), "--name", "testy", "--embed-fonts"])
    assert rc == 0

    lines = capsys.readouterr().out.splitlines()
    embedded_at = next(i for i, l in enumerate(lines) if l.startswith("embedded fonts: 'Testy'"))
    assert lines[embedded_at + 1].startswith("skipped font faces: 3 ")
    assert lines[embedded_at + 2 : embedded_at + 5] == [
        "  'Testy' / 700 / normal",
        "  'Testy' / 400 / italic",
        # the reader turns an empty style into "normal"; an empty weight
        # stays empty and prints as "unspecified"
        "  'Decor' / unspecified / normal",
    ]
    assert any(l.startswith("LICENSING") for l in lines[embedded_at + 5 :])


def test_cli_embed_fonts_says_when_nothing_was_skipped(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _manual_export(tmp_path)

    rc = main(["import-design", str(export_dir), "--name", "testy", "--embed-fonts"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "skipped font faces: none" in out


def test_cli_embed_fonts_all_faces_skipped_is_not_reported_as_no_files(
    tmp_path, monkeypatch, capsys
):
    # Every listed face is unreferenced: "the export lists no font files"
    # would be false, so the summary says nothing was embedded and names
    # the faces.
    monkeypatch.setenv("HOME", str(tmp_path))
    # (italic, because a lone upright weight would embed as the nearest
    # weight; a style is never substituted)
    export_dir = _faces_export(
        tmp_path, faces=[("Testy", "700", "italic", "Testy-BoldItalic.ttf")]
    )

    rc = main(["import-design", str(export_dir), "--name", "testy", "--embed-fonts"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "lists no font files" not in out
    assert "nothing was embedded" in out
    assert "  'Testy' / 700 / italic" in out
    assert "LICENSING" not in out


def test_cli_embed_fonts_skipped_family_is_escaped_for_the_terminal(
    tmp_path, monkeypatch, capsys
):
    # The reader refuses C0 controls in a family; U+009B (CSI) and U+202E
    # (right-to-left override) pass it. The skipped line prints the
    # family with !r, so neither reaches the terminal raw.
    monkeypatch.setenv("HOME", str(tmp_path))
    hostile = "Dec\u009b31m\u202eor"
    export_dir = _faces_export(
        tmp_path,
        faces=[
            ("Testy", "400", "normal", "Testy-Regular.ttf"),
            (hostile, "400", "normal", "Decor.ttf"),
        ],
    )

    rc = main(["import-design", str(export_dir), "--name", "testy", "--embed-fonts"])
    assert rc == 0

    out = capsys.readouterr().out
    skipped_at = out.index("skipped font faces:")
    assert "\u009b" not in out[skipped_at:]
    assert "\u202e" not in out[skipped_at:]
    assert "'Dec\\x9b31m\\u202eor' / 400 / normal" in out


def test_cli_embed_fonts_embedded_family_is_escaped_for_the_terminal(
    tmp_path, monkeypatch, capsys
):
    # The same hostile family, this time the one the theme references:
    # the "embedded fonts:" line escapes it too.
    monkeypatch.setenv("HOME", str(tmp_path))
    hostile = "Tes\u009b31m\u202ety"
    export_dir = _faces_export(
        tmp_path,
        faces=[(hostile, "400", "normal", "Testy-Regular.ttf")],
        font_token_value='"{}", sans-serif'.format(hostile),
        brand_family=hostile,
    )

    rc = main(["import-design", str(export_dir), "--name", "testy", "--embed-fonts"])
    assert rc == 0

    out = capsys.readouterr().out
    embedded_line = next(l for l in out.splitlines() if l.startswith("embedded fonts:"))
    assert "\u009b" not in embedded_line
    assert "\u202e" not in embedded_line
    assert "'Tes\\x9b31m\\u202ety'" in embedded_line


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


@pytest.mark.skipif(
    not hasattr(os, "symlink"), reason="platform cannot create symlinks"
)
def test_embed_fonts_symlink_loop_raises_design_import_error(tmp_path, monkeypatch):
    # A symlink LOOP in the font path (a -> b -> a) makes Path.resolve()
    # itself raise on CPython <= 3.12 (RuntimeError; OSError on some
    # platforms) rather than just landing outside export_root -- that
    # used to bypass _resolve_font_file's DesignImportError contract and
    # surface as a raw traceback under --embed-fonts. 3.13 rewrote
    # pathlib.resolve() to give up silently on a loop instead of raising
    # (verified: it returns the unresolved path, so the existing
    # is_file() check below reports "not found" -- no traceback either
    # way, just not this code path), so this test skips itself there
    # rather than asserting a exception this runtime will never raise;
    # see the deterministic monkeypatch-based test below for coverage
    # that doesn't depend on the interpreter's resolve() behavior.
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _manual_export(
        tmp_path, font_rel_path="assets/fonts/loop-a.ttf", write_font=False
    )
    fonts_dir = export_dir / "assets" / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    loop_a = fonts_dir / "loop-a.ttf"
    loop_b = fonts_dir / "loop-b.ttf"
    try:
        os.symlink(loop_b, loop_a)
        os.symlink(loop_a, loop_b)
    except OSError:
        pytest.skip("platform refused to create a symlink loop")

    try:
        loop_a.resolve()
    except (OSError, ValueError, RuntimeError):
        pass
    else:
        pytest.skip(
            "this Python's Path.resolve() does not raise on a symlink "
            "loop (non-strict resolution gives up silently instead)"
        )

    with pytest.raises(DesignImportError, match="cannot resolve font path"):
        import_design(export_dir, name="loopy", embed_fonts=True)


def test_resolve_font_file_wraps_runtime_error_as_design_import_error(
    tmp_path, monkeypatch
):
    # Same contract as the symlink-loop test above, but deterministic
    # across Python versions: force Path.resolve() to raise RuntimeError
    # the way CPython <= 3.12's pathlib does for a symlink loop, and
    # confirm _resolve_font_file wraps it as a DesignImportError instead
    # of letting it escape as a raw RuntimeError/traceback.
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = _manual_export(tmp_path)

    original_resolve = Path.resolve

    def raising_resolve(self, *args, **kwargs):
        if self.name == "Testy-Regular.ttf":
            raise RuntimeError(f"Symlink loop from {self!r}")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", raising_resolve)

    with pytest.raises(DesignImportError, match="cannot resolve font path"):
        import_design(export_dir, name="loopy2", embed_fonts=True)


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


def test_embed_fonts_css_fallback_escape_on_an_unreferenced_face_still_rejected(
    tmp_path, monkeypatch
):
    # "A skipped face's file is never touched" is _embed_fonts' promise.
    # The manifest-less READER confines every @font-face url to the export
    # before any face is selected, so an escaping url fails the import
    # even on a family the theme never names. That stays fail-closed.
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "outside.ttf").write_bytes(b"SHOULD-NEVER-BE-READ")
    export_dir = _manifestless_font_export(tmp_path)
    fonts_css = export_dir / "tokens" / "fonts.css"
    fonts_css.write_text(
        fonts_css.read_text()
        + "@font-face {\n"
        '  font-family: "Ghost";\n'
        '  src: url("../../outside.ttf") format("truetype");\n'
        "  font-weight: 400;\n"
        "  font-style: normal;\n"
        "}\n"
    )

    with pytest.raises(DesignImportError, match="escapes"):
        import_design(export_dir, name="ghosty", embed_fonts=True)
    assert not (tmp_path / ".jimemo" / "themes" / "ghosty.css").exists()


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


def test_cli_import_design_brand_font_referencing_token_injection_rc1_writes_nothing(
    tmp_path, monkeypatch
):
    # The roborev proof-of-concept, run through the whole importer: a
    # brandFonts referencing token that would break the header comment open
    # AND inject a second :root{...} carrying a reserved --jm- override.
    # Must exit rc=1 with no theme written (blocked at the reader boundary).
    monkeypatch.setenv("HOME", str(tmp_path))
    export_dir = tmp_path / "evil-brandfont-export"
    export_dir.mkdir()
    manifest = {
        "namespace": "Evil",
        "tokens": [
            {"name": "--ev-ink", "value": "#111111", "kind": "color"},
            {"name": "--ev-font", "value": '"Helios", sans-serif', "kind": "font"},
        ],
        "fonts": [],
        "brandFonts": [
            {
                "family": "Helios",
                "status": "ok",
                "tokens": ["*/:root{--jm-font-mono:serif}/*"],
            }
        ],
        "globalCssPaths": [],
        "themes": [],
    }
    (export_dir / "_ds_manifest.json").write_text(json.dumps(manifest))

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


# -- --from NAME: resolve against ~/.jimemo/design-systems/NAME/ ---------


def _seed_design_system(tmp_path: Path, name: str) -> Path:
    """Copies the synthetic fixture into a fake
    `~/.jimemo/design-systems/<name>/` under `tmp_path` (used as HOME),
    the shape a friend gets by cloning their private design-systems
    repo there."""
    dest = design_systems_dir() / name
    shutil.copytree(FIXTURE_DIR, dest)
    return dest


def test_resolve_from_name_finds_seeded_design_system(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    seeded = _seed_design_system(tmp_path, "northwind-tech")

    resolved = resolve_from_name("northwind-tech")

    assert resolved == seeded
    assert resolved == tmp_path / ".jimemo" / "design-systems" / "northwind-tech"


def test_resolve_from_name_missing_raises_clean_error_naming_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    expected_path = tmp_path / ".jimemo" / "design-systems" / "nope"

    with pytest.raises(DesignImportError, match=re.escape(str(expected_path))):
        resolve_from_name("nope")


def test_resolve_from_name_rejects_slash():
    with pytest.raises(DesignImportError, match="not a valid slug"):
        resolve_from_name("foo/bar")


def test_resolve_from_name_rejects_dotdot_traversal():
    with pytest.raises(DesignImportError, match="not a valid slug"):
        resolve_from_name("../etc")


def test_resolve_from_name_rejects_uppercase_and_underscore():
    with pytest.raises(DesignImportError, match="not a valid slug"):
        resolve_from_name("Northwind_Tech")


def test_cli_import_design_from_resolves_and_writes_theme(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_design_system(tmp_path, "northwind-tech")

    rc = main(["import-design", "--from", "northwind-tech", "--name", "viafrom"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "wrote theme:" in out
    assert (tmp_path / ".jimemo" / "themes" / "viafrom.css").is_file()


def test_cli_import_design_from_nonexistent_returns_rc1_clean_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))

    rc = main(["import-design", "--from", "nope"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "no design system named 'nope'" in err
    assert "Traceback" not in err


def test_cli_import_design_from_and_positional_both_given_returns_rc2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed_design_system(tmp_path, "northwind-tech")

    rc = main(["import-design", str(FIXTURE_DIR), "--from", "northwind-tech"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "not both" in err
    assert not (tmp_path / ".jimemo" / "themes").exists()


def test_cli_import_design_neither_positional_nor_from_returns_rc2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))

    rc = main(["import-design"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "--from NAME" in err


def test_cli_import_design_positional_still_works_unaffected_by_from(tmp_path, monkeypatch):
    # The pre-existing positional-path form must keep working exactly as
    # before now that it's optional (nargs="?") to make room for --from.
    monkeypatch.setenv("HOME", str(tmp_path))

    rc = main(["import-design", str(FIXTURE_DIR), "--name", "positional-still-works"])

    assert rc == 0
    assert (tmp_path / ".jimemo" / "themes" / "positional-still-works.css").is_file()
