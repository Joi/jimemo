import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.design.mapping import build_theme, theme_structure_errors
from jimemo.design.reader import BrandFont, DesignExport, Token, read_export
from jimemo.errors import DesignImportError

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "design-export"


def _theme():
    export = read_export(FIXTURE_DIR)
    return export, build_theme(export, "chiba")


# -- role mapping ----------------------------------------------------------


def test_font_maps_to_finder_with_fallback():
    _, css = _theme()
    for role in ("--jm-font-prose", "--jm-font-ui"):
        m = re.search(re.escape(role) + r":\s*([^;]+);", css)
        assert m, f"{role} not set in theme"
        value = m.group(1)
        assert '"Finder"' in value
        # a real fallback stack, not just the bare family name
        assert "sans-serif" in value
        assert "Arial" in value or "Helvetica" in value


# -- font inference when brand_fonts is empty ------------------------------
#
# brand_fonts is manifest-only metadata; a manifest that omits
# `brandFonts`, or the CSS-fallback reader path (which never populates
# it at all -- see reader._from_css_fallback), left --jm-font-prose/ui
# unmapped even when the export plainly has a primary font. These cover
# the (a) name-token / (b) FontFace / (c) nothing-to-infer paths.


def _export_with_tokens(tokens, fonts=()):
    return DesignExport(tokens=list(tokens), fonts=list(fonts), brand_fonts=[], namespace="")


def test_font_inferred_from_font_named_token_when_brand_fonts_empty():
    export = _export_with_tokens(
        [Token(name="--x-font", value='"Custom", sans-serif', kind="font")]
    )
    css = build_theme(export, "inferred")
    for role in ("--jm-font-prose", "--jm-font-ui"):
        m = re.search(re.escape(role) + r":\s*([^;]+);", css)
        assert m, f"{role} not set in theme"
        assert '"Custom"' in m.group(1)


def test_font_inferred_from_fontface_when_no_font_token():
    from jimemo.design.reader import FontFace

    export = _export_with_tokens(
        [Token(name="--x-color", value="#111111", kind="color")],
        fonts=[FontFace(family="Custom Face", weight="400", style="normal")],
    )
    css = build_theme(export, "inferred")
    m = re.search(r"--jm-font-prose:\s*([^;]+);", css)
    assert m and '"Custom Face"' in m.group(1)


def test_font_not_inferred_when_nothing_to_infer_from():
    export = _export_with_tokens([Token(name="--x-color", value="#111111", kind="color")])
    css = build_theme(export, "inferred")
    assert not re.search(r"--jm-font-prose:\s*[^;]+;", css)
    assert not re.search(r"--jm-font-ui:\s*[^;]+;", css)
    assert "no confident primary font found" in css


def test_font_inferred_when_brand_fonts_has_only_unreferenced_entries():
    # An unreferenced-only brand_fonts list (referencing_token_names=[])
    # must behave like an empty one: _pick_primary_font correctly refuses
    # to name an unreferenced brand font as primary, and _font_declaration
    # must fall through to the --*-font token inference rather than give
    # up just because export.brand_fonts happens to be non-empty.
    export = DesignExport(
        tokens=[Token(name="--x-font", value='"Custom", sans-serif', kind="font")],
        fonts=[],
        brand_fonts=[BrandFont(family="Unused", referencing_token_names=[], status="ok")],
        namespace="",
    )
    css = build_theme(export, "inferred")
    for role in ("--jm-font-prose", "--jm-font-ui"):
        m = re.search(re.escape(role) + r":\s*([^;]+);", css)
        assert m, f"{role} not set in theme"
        assert '"Custom"' in m.group(1)
        assert "Unused" not in m.group(1)


def test_chiba_font_mapping_unchanged_when_brand_fonts_present():
    # Regression guard: brand_fonts stays the primary source when
    # present -- inference must not kick in and override it.
    export, css = _theme()
    assert export.brand_fonts  # sanity: the fixture really has brand_fonts
    for role in ("--jm-font-prose", "--jm-font-ui"):
        m = re.search(re.escape(role) + r":\s*([^;]+);", css)
        assert m and '"Finder"' in m.group(1)


def test_accent_maps_to_brand_core_not_black_or_white():
    _, css = _theme()
    m = re.search(r"--jm-accent:\s*([^;]+);", css)
    assert m
    assert m.group(1).strip() == "var(--ct-blue-core)"
    # the underlying brand color itself, re-declared verbatim
    assert "--ct-blue-core: #4c4499;" in css


def test_accent_contrast_is_light_against_the_dark_accent():
    _, css = _theme()
    m = re.search(r"--jm-accent-contrast:\s*([^;]+);", css)
    assert m
    assert m.group(1).strip().lower() == "#ffffff"


def test_positive_and_negative_map_to_green_and_red():
    _, css = _theme()
    positive = re.search(r"--jm-positive:\s*([^;]+);", css)
    negative = re.search(r"--jm-negative:\s*([^;]+);", css)
    assert positive and positive.group(1).strip() == "var(--ct-green)"
    assert negative and negative.group(1).strip() == "var(--ct-red)"


def test_text_bg_surface_map_to_semantic_aliases_not_pink():
    _, css = _theme()
    text = re.search(r"--jm-text:\s*([^;]+);", css)
    bg = re.search(r"--jm-bg:\s*([^;]+);", css)
    surface = re.search(r"--jm-surface:\s*([^;]+);", css)
    assert text and text.group(1).strip() == "var(--ct-ink)"
    assert bg and bg.group(1).strip() == "var(--ct-paper)"
    assert surface and surface.group(1).strip() == "var(--ct-surface)"


def test_border_and_muted_pick_distinct_greys():
    _, css = _theme()
    border = re.search(r"--jm-border:\s*([^;]+);", css)
    muted = re.search(r"--jm-muted:\s*([^;]+);", css)
    assert border and muted
    assert border.group(1).strip() != muted.group(1).strip()
    # border is the lightest grey, muted a darker one
    assert border.group(1).strip() == "var(--ct-grey-b9)"
    assert muted.group(1).strip() == "var(--ct-grey-64)"


# -- raw token preservation --------------------------------------------------


def test_all_raw_tokens_present_verbatim():
    export, css = _theme()
    for t in export.tokens:
        assert "{}: {};".format(t.name, t.value) in css


# -- output shape / self-contained lint -------------------------------------


def test_output_is_valid_css_with_balanced_braces():
    _, css = _theme()
    assert css.count("{") == css.count("}")
    assert css.count("{") >= 1


def test_output_has_no_remote_url_or_import_or_markup():
    _, css = _theme()
    assert "url(http" not in css
    assert "@import" not in css
    assert "<" not in css


# -- determinism --------------------------------------------------------


def test_deterministic_across_runs():
    export = read_export(FIXTURE_DIR)
    first = build_theme(export, "chiba")
    second = build_theme(export, "chiba")
    assert first == second


# -- defense in depth: hand-built exports that bypassed the reader ----------


def test_build_theme_rejects_injection_shaped_token_name():
    export = DesignExport(
        tokens=[
            Token(
                name="x: red } body { display:none } :root{ --y",
                value="red",
                kind="color",
            )
        ],
        fonts=[],
        brand_fonts=[],
        namespace="Evil",
    )
    with pytest.raises(DesignImportError, match="token name"):
        build_theme(export, "evil")


def test_build_theme_rejects_reserved_jm_prefix_token_name():
    # Regression: a source token literally named a jm role (e.g.
    # --jm-bg) would make mapping emit a self-referential
    # `--jm-bg: var(--jm-bg)` if picked as that role's source, or
    # silently override the role via the raw re-declaration either way.
    # The reserved-prefix check in validate_token_name (called from this
    # same loop) closes that off before either can happen.
    export = DesignExport(
        tokens=[Token(name="--jm-bg", value="#111111", kind="color")],
        fonts=[],
        brand_fonts=[],
        namespace="Evil",
    )
    with pytest.raises(DesignImportError, match="reserved"):
        build_theme(export, "evil")


def test_build_theme_rejects_comment_breakout_namespace():
    export = DesignExport(
        tokens=[Token(name="--ok", value="#111111", kind="color")],
        fonts=[],
        brand_fonts=[],
        namespace="Evil*/}body{display:none}/*",
    )
    with pytest.raises(DesignImportError, match="namespace"):
        build_theme(export, "evil")


# -- structural output validation --------------------------------------------
#
# theme_structure_errors is the OUTPUT-side gate: even if every input
# validator were bypassed (or a future untrusted field forgotten), a theme
# whose shape isn't pure :root/@font-face blocks must not be written.


def test_theme_structure_passes_on_real_chiba_theme():
    _, css = _theme()
    assert theme_structure_errors(css) == []


def test_theme_structure_passes_on_font_face_blocks():
    css = (
        ":root {\n  --x: 1;\n}\n\n"
        '@font-face {\n  font-family: "X";\n'
        '  src: url(data:font/ttf;base64,AAAA) format("truetype");\n}\n'
    )
    assert theme_structure_errors(css) == []


def test_theme_structure_flags_top_level_rule():
    css = "/* header */\n:root {\n  --x: 1;\n}\nbody { display:none }\n"
    errors = theme_structure_errors(css)
    assert any("top-level" in e for e in errors)


def test_theme_structure_flags_unbalanced_brace():
    errors = theme_structure_errors(":root {\n  --x: 1;\n")
    assert any("unbalanced" in e for e in errors)


def test_theme_structure_flags_stray_comment_delimiter():
    errors = theme_structure_errors(":root { --x: 1; } */ :root { --y: 2; }")
    assert any("comment delimiter" in e for e in errors)


def test_build_theme_structural_gate_catches_injection_past_input_validation(monkeypatch):
    # Hypothetical: input validation has a hole (here: forced off), so a
    # hostile value smuggles `} body { display:none } :root {` into the
    # :root block. Brace counts BALANCE in this payload, and the lint
    # sees no url()/@import -- only the structural check catches the
    # stray top-level `body { ... }` rule. Fail closed.
    from jimemo.design import mapping

    monkeypatch.setattr(mapping, "validate_token_name", lambda name: None)
    monkeypatch.setattr(mapping, "validate_token_value", lambda name, value: None)

    export = DesignExport(
        tokens=[
            Token(
                name="--x",
                value="red } body { display:none } :root { --y: 1",
                kind="color",
            )
        ],
        fonts=[],
        brand_fonts=[],
        namespace="Evil",
    )
    with pytest.raises(DesignImportError, match="structural safety check"):
        build_theme(export, "evil")


def _bypassed_build_theme(monkeypatch, value: str):
    """build_theme on a single-token export with the input validators
    forced off -- the 'hypothetical hole' harness for the output gate."""
    from jimemo.design import mapping

    monkeypatch.setattr(mapping, "validate_token_name", lambda name: None)
    monkeypatch.setattr(mapping, "validate_token_value", lambda name, value: None)
    export = DesignExport(
        tokens=[Token(name="--x", value=value, kind="color")],
        fonts=[],
        brand_fonts=[],
        namespace="Evil",
    )
    return build_theme(export, "evil")


def test_build_theme_structural_gate_catches_unbalanced_brace(monkeypatch):
    with pytest.raises(DesignImportError, match="unbalanced braces"):
        _bypassed_build_theme(monkeypatch, "red } }")


def test_build_theme_structural_gate_catches_stray_comment_close(monkeypatch):
    with pytest.raises(DesignImportError, match="comment delimiter"):
        _bypassed_build_theme(monkeypatch, "red */ oops")


# -- header comment -----------------------------------------------------


def test_header_documents_mappings():
    _, css = _theme()
    header_end = css.index(":root")
    header = css[:header_end]
    assert "Auto-mapped roles" in header
    assert "--ct-blue-core -> --jm-accent" in header
    assert "Review / refine" in header
