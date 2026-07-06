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
    return export, build_theme(export, "northwind")


# -- role mapping ----------------------------------------------------------


def test_font_maps_to_primary_family_with_fallback():
    _, css = _theme()
    for role in ("--jm-font-prose", "--jm-font-ui"):
        m = re.search(re.escape(role) + r":\s*([^;]+);", css)
        assert m, f"{role} not set in theme"
        value = m.group(1)
        assert '"Northwind Sans"' in value
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


def test_font_inference_rejects_unsafe_family_extracted_from_token_value():
    # `"Bad"Name, sans-serif` passes validate_token_value (a bare '"' is
    # legal in a token value), but the family carved out of it --
    # literally `"Bad"Name` -- fails the stricter font-family check. Left
    # unvalidated, it would re-quote into a malformed
    # `--jm-font-prose: ""Bad"Name", ...` declaration; it must instead be
    # rejected and the role left unmapped (no other font source here).
    export = _export_with_tokens(
        [Token(name="--x-font", value='"Bad"Name, sans-serif', kind="font")]
    )
    css = build_theme(export, "inferred")
    # jimemo's font roles are left unmapped -- not corrupted -- while the
    # raw token is still re-declared verbatim below (module contract:
    # "All imported tokens are re-declared verbatim ... mapped or not").
    assert not re.search(r"--jm-font-prose:\s*[^;]+;", css)
    assert not re.search(r"--jm-font-ui:\s*[^;]+;", css)
    assert '--x-font: "Bad"Name, sans-serif;' in css
    assert not theme_structure_errors(css)


def test_font_inference_falls_through_to_fontface_when_token_family_unsafe():
    # Same unsafe token value, but with a valid (reader-validated)
    # FontFace also present -- the rejected token-derived family must
    # fall through to source (b) rather than give up entirely.
    from jimemo.design.reader import FontFace

    export = _export_with_tokens(
        [Token(name="--x-font", value='"Bad"Name, sans-serif', kind="font")],
        fonts=[FontFace(family="Safe Face", weight="400", style="normal")],
    )
    css = build_theme(export, "inferred")
    m = re.search(r"--jm-font-prose:\s*([^;]+);", css)
    assert m and '"Safe Face"' in m.group(1)
    assert "Bad" not in m.group(1)
    assert not theme_structure_errors(css)


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


def test_duplicate_brand_fonts_same_family_no_indexerror():
    # Two brandFonts entries for one family: the FIRST has no referencing
    # tokens, the SECOND is referenced. _pick_primary_font picks the
    # referenced one; the old code then re-found "the first brand font with
    # this family" (the token-less duplicate) and indexed its empty
    # referencing list -> raw IndexError. Returning the selected BrandFont
    # object means the referenced entry is used directly -- no re-find, no
    # crash.
    export = DesignExport(
        tokens=[Token(name="--brand-font", value='"Helios", sans-serif', kind="font")],
        fonts=[],
        brand_fonts=[
            BrandFont(family="Helios", referencing_token_names=[], status="ok"),
            BrandFont(family="Helios", referencing_token_names=["--brand-font"], status="ok"),
        ],
        namespace="",
    )
    css = build_theme(export, "dup")  # must not raise IndexError
    for role in ("--jm-font-prose", "--jm-font-ui"):
        m = re.search(re.escape(role) + r":\s*([^;]+);", css)
        assert m and '"Helios"' in m.group(1)
    # header records the referenced token as the source, not a crash
    assert "--brand-font -> --jm-font-prose" in css
    assert not theme_structure_errors(css)


def test_empty_quoted_family_in_token_value_falls_through():
    # A `--*-font` token whose stack's first entry is an EMPTY quoted family
    # (`"", sans-serif`) yields no usable family: the hardened extractor
    # returns None rather than the junk `""`, so inference falls through
    # (here: to nothing) and the font roles are simply left unmapped --
    # never emitted as a malformed `--jm-font-prose: "", ...`.
    export = _export_with_tokens(
        [Token(name="--x-font", value='"", sans-serif', kind="font")]
    )
    css = build_theme(export, "inferred")
    assert not re.search(r"--jm-font-prose:\s*[^;]+;", css)
    assert not re.search(r"--jm-font-ui:\s*[^;]+;", css)
    assert not theme_structure_errors(css)


def test_font_mapping_unchanged_when_brand_fonts_present():
    # Regression guard: brand_fonts stays the primary source when
    # present -- inference must not kick in and override it.
    export, css = _theme()
    assert export.brand_fonts  # sanity: the fixture really has brand_fonts
    for role in ("--jm-font-prose", "--jm-font-ui"):
        m = re.search(re.escape(role) + r":\s*([^;]+);", css)
        assert m and '"Northwind Sans"' in m.group(1)


def test_accent_maps_to_brand_core_not_black_or_white():
    _, css = _theme()
    m = re.search(r"--jm-accent:\s*([^;]+);", css)
    assert m
    assert m.group(1).strip() == "var(--nw-blue-core)"
    # the underlying brand color itself, re-declared verbatim
    assert "--nw-blue-core: #33418f;" in css


def test_accent_contrast_is_light_against_the_dark_accent():
    _, css = _theme()
    m = re.search(r"--jm-accent-contrast:\s*([^;]+);", css)
    assert m
    assert m.group(1).strip().lower() == "#ffffff"


def test_positive_and_negative_map_to_green_and_red():
    _, css = _theme()
    positive = re.search(r"--jm-positive:\s*([^;]+);", css)
    negative = re.search(r"--jm-negative:\s*([^;]+);", css)
    assert positive and positive.group(1).strip() == "var(--nw-green)"
    assert negative and negative.group(1).strip() == "var(--nw-red)"


def test_text_bg_surface_map_to_semantic_aliases_not_pink():
    _, css = _theme()
    text = re.search(r"--jm-text:\s*([^;]+);", css)
    bg = re.search(r"--jm-bg:\s*([^;]+);", css)
    surface = re.search(r"--jm-surface:\s*([^;]+);", css)
    assert text and text.group(1).strip() == "var(--nw-ink)"
    assert bg and bg.group(1).strip() == "var(--nw-paper)"
    assert surface and surface.group(1).strip() == "var(--nw-surface)"


def test_border_and_muted_pick_distinct_greys():
    _, css = _theme()
    border = re.search(r"--jm-border:\s*([^;]+);", css)
    muted = re.search(r"--jm-muted:\s*([^;]+);", css)
    assert border and muted
    assert border.group(1).strip() != muted.group(1).strip()
    # border is the lightest grey, muted a darker one
    assert border.group(1).strip() == "var(--nw-grey-cf)"
    assert muted.group(1).strip() == "var(--nw-grey-58)"


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
    first = build_theme(export, "northwind")
    second = build_theme(export, "northwind")
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


def test_theme_structure_passes_on_real_fixture_theme():
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


def test_theme_structure_flags_multiple_root_blocks():
    # A second :root block (e.g. injected via a header-comment breakout)
    # rides in alongside the legitimate one. _THEME_BLOCK_RE strips both, so
    # only the dedicated :root count catches it. Fail closed.
    css = (
        "/* header */\n"
        ":root {\n  --x: 1;\n}\n"
        ":root {\n  --jm-font-mono: serif;\n}\n"
    )
    errors = theme_structure_errors(css)
    assert any("multiple :root" in e for e in errors)


def test_theme_structure_passes_on_multiple_font_face_blocks():
    # One :root + N @font-face is exactly the --embed-fonts shape.
    css = (
        ":root {\n  --x: 1;\n}\n\n"
        '@font-face {\n  font-family: "A";\n'
        '  src: url(data:font/ttf;base64,AAAA) format("truetype");\n}\n'
        '@font-face {\n  font-family: "B";\n'
        '  src: url(data:font/ttf;base64,BBBB) format("truetype");\n}\n'
    )
    assert theme_structure_errors(css) == []


def test_build_theme_rejects_comment_breakout_source_token():
    # A hand-built export (bypassing the reader's referencing-name
    # validation) whose brand-font source token would break the header
    # comment open AND inject a second :root. build_theme's
    # _reject_comment_close is the input-side belt-and-braces; the single-
    # :root / comment-delimiter structural gate is the output-side net --
    # this must fail closed either way.
    export = DesignExport(
        tokens=[Token(name="--brand-font", value='"Helios", sans-serif', kind="font")],
        fonts=[],
        brand_fonts=[
            BrandFont(
                family="Helios",
                referencing_token_names=["*/:root{--jm-font-mono:serif}/*"],
                status="ok",
            )
        ],
        namespace="Evil",
    )
    with pytest.raises(DesignImportError):
        build_theme(export, "evil")


def test_build_theme_rejects_hand_built_brand_font_with_unsafe_family():
    # read_export always runs validate_font_family on brand_fonts/fonts
    # entries, but a caller building a DesignExport directly (bypassing
    # the reader entirely) skips that. build_theme's _font_declaration
    # would otherwise interpolate the family straight into the
    # --jm-font-prose/ui role value unescaped -- the same injection
    # class validate_token_value/validate_token_name/validate_namespace
    # are already re-validated against above. This is that same
    # defense-in-depth for BrandFont.family.
    export = DesignExport(
        tokens=[Token(name="--brand-font", value='"Helios", sans-serif', kind="font")],
        fonts=[],
        brand_fonts=[
            BrandFont(
                family='"} body{display:none} .x{',
                referencing_token_names=["--brand-font"],
                status="ok",
            )
        ],
        namespace="",
    )
    with pytest.raises(DesignImportError):
        build_theme(export, "evil")


def test_build_theme_rejects_hand_built_fontface_with_unsafe_family():
    # Same bypass, but via the FontFace fallback `_infer_font_declaration`
    # reads (export.fonts[0].family) when there is no confident brand
    # font -- also never validated for a hand-built DesignExport.
    from jimemo.design.reader import FontFace

    export = DesignExport(
        tokens=[],
        fonts=[FontFace(family='"} body{display:none} .x{', weight="normal", style="normal")],
        brand_fonts=[],
        namespace="",
    )
    with pytest.raises(DesignImportError):
        build_theme(export, "evil")


def test_build_theme_normal_export_unaffected_by_font_family_revalidation():
    # The re-validation added above must be a no-op for input that already
    # passed the reader -- read_export's own output is unaffected.
    _, css = _theme()
    assert '"Northwind Sans"' in css
    assert not theme_structure_errors(css)


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


def test_structural_gate_blocks_hostile_export_with_all_validators_off(monkeypatch):
    # The definitive backstop: even with EVERY reader input validator forced
    # to a no-op (validate_token_name/value/namespace/font_family), a hostile
    # export whose token value breaks out of the :root block and injects a
    # sibling `body { ... }` rule must still be blocked -- by the output-side
    # structural gate alone. Brace counts balance in this payload and the
    # lint sees no url()/@import, so nothing but theme_structure_errors
    # catches the stray top-level rule. Fail closed.
    from jimemo.design import mapping

    monkeypatch.setattr(mapping, "validate_token_name", lambda name: None)
    monkeypatch.setattr(mapping, "validate_token_value", lambda name, value: None)
    monkeypatch.setattr(mapping, "validate_namespace", lambda ns: None)
    monkeypatch.setattr(mapping, "validate_font_family", lambda fam: None)

    # The injection rides in on the token VALUE -- the main channel, and one
    # `_reject_comment_close` (build_theme's header-comment backstop) does NOT
    # inspect, so the structural gate is genuinely the only thing left.
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
        namespace="",
    )
    with pytest.raises(DesignImportError, match="structural safety check"):
        build_theme(export, "evil")


# -- audit: export fields that never reach generated output ------------------
#
# token.kind, token.defined_in, and BrandFont.status are read from the
# untrusted manifest but are used ONLY in comparisons (kind == "color",
# status == "ok") or as metadata -- none is interpolated into the theme CSS,
# header comment, or @font-face blocks. So even hostile values for them
# produce a clean, inert theme; this guards that they stay non-emitted.


def test_hostile_kind_defined_in_status_never_reach_output():
    hostile = "*/ } body { display:none } :root { --z: 1 /*"
    export = DesignExport(
        tokens=[
            Token(name="--brand-black", value="#000000", kind=hostile, defined_in=hostile)
        ],
        fonts=[],
        brand_fonts=[
            BrandFont(family="Legit", referencing_token_names=["--brand-black"], status=hostile)
        ],
        namespace="",
    )
    css = build_theme(export, "audit")
    assert hostile not in css
    assert "body { display:none }" not in css
    assert theme_structure_errors(css) == []
    # the token itself is still re-declared verbatim (name+value validated)
    assert "--brand-black: #000000;" in css


# -- header comment -----------------------------------------------------


def test_header_documents_mappings():
    _, css = _theme()
    header_end = css.index(":root")
    header = css[:header_end]
    assert "Auto-mapped roles" in header
    assert "--nw-blue-core -> --jm-accent" in header
    assert "Review / refine" in header
