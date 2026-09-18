"""Tests for `render --figure`: the SVG sanitizer (jimemo.sanitize.
sanitize_svg) and the placeholder splice (jimemo.render.render_page's
`figures`).

One test per adversarial vector, each asserting the payload is gone AND
harmless sibling content survives; then the keep side (what theming
needs); then the render-level splice; CLI coverage lives in test_cli.py.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.sanitize import sanitize_svg


# A benign sibling that must survive every payload test.
SIBLING = '<rect id="ok" width="4" height="4"/>'


def wrap(payload: str, root_attrs: str = ' viewBox="0 0 20 20"') -> str:
    """`payload` inside a root <svg>, followed by the benign sibling."""
    return "<svg{0}>{1}{2}</svg>".format(root_attrs, payload, SIBLING)


# --- dropped payloads: payload gone, sibling survives ---------------------


def test_script_element_dropped_with_content():
    out = sanitize_svg(wrap("<script>alert(1)//&lt;</script>"))
    assert "script" not in out and "alert" not in out
    assert '<rect id="ok"' in out and out.startswith("<svg")


def test_foreignobject_with_html_inside_dropped():
    out = sanitize_svg(
        wrap('<foreignObject><div onclick="x()">HTML</div>'
             '<img src=x onerror=alert(1)></foreignObject>')
    )
    assert "foreignobject" not in out.lower()
    assert "<div" not in out and "HTML" not in out and "onclick" not in out
    assert '<rect id="ok"' in out


def test_onload_on_svg_root_dropped():
    out = sanitize_svg(wrap("", root_attrs=' viewBox="0 0 20 20" onload="evil()"'))
    assert "onload" not in out and "evil" not in out
    assert out.startswith('<svg viewBox="0 0 20 20">')
    assert '<rect id="ok"' in out


def test_onclick_on_rect_dropped_element_kept():
    out = sanitize_svg(wrap('<rect onclick="evil()" width="1" height="1"/>'))
    assert "onclick" not in out and "evil" not in out
    assert '<rect width="1" height="1"' in out  # the element itself stays


def test_a_javascript_href_dropped_with_element_and_content():
    out = sanitize_svg(wrap('<a href="javascript:alert(1)">click me</a>'))
    assert "<a" not in out and "javascript" not in out and "click" not in out
    assert '<rect id="ok"' in out


def test_use_remote_href_attribute_dropped():
    out = sanitize_svg(wrap('<use href="https://e.example/x.svg#a"/>'))
    assert "e.example" not in out and "href" not in out
    assert "<use" in out  # the element itself is harmless without the ref
    assert '<rect id="ok"' in out


def test_use_xlink_href_other_file_dropped():
    out = sanitize_svg(wrap('<use xlink:href="other.svg#x"/>'))
    assert "other.svg" not in out and "xlink:href" not in out
    assert "<use" in out
    assert '<rect id="ok"' in out


def test_image_remote_href_dropped_with_element():
    out = sanitize_svg(wrap('<image href="https://e.example/x.png"/>'))
    assert "<image" not in out and "e.example" not in out
    assert '<rect id="ok"' in out


def test_image_svg_data_uri_dropped_with_element():
    out = sanitize_svg(
        wrap('<image href="data:image/svg+xml,<svg onload=alert(1)>"/>')
    )
    assert "<image" not in out and "data:" not in out and "onload" not in out
    assert '<rect id="ok"' in out


def test_style_element_with_import_dropped_with_content():
    out = sanitize_svg(wrap("<style>@import url(https://e.example/x.css);</style>"))
    assert "style" not in out and "@import" not in out and "e.example" not in out
    assert '<rect id="ok"' in out


def test_style_attribute_remote_url_dropped():
    out = sanitize_svg(wrap('<rect style="fill:url(https://e.example/x.png)" width="1"/>'))
    assert "style" not in out and "e.example" not in out
    assert '<rect width="1"' in out  # the element itself stays


def test_fill_remote_url_attribute_dropped():
    out = sanitize_svg(wrap('<rect fill="url(https://e.example/x.png)" width="1"/>'))
    assert "fill" not in out and "e.example" not in out
    assert '<rect width="1"' in out
    assert '<rect id="ok"' in out


def test_animate_to_javascript_href_dropped_with_element():
    out = sanitize_svg(
        wrap('<animate attributeName="href" to="javascript:alert(1)"></animate>')
    )
    assert "animate" not in out and "javascript" not in out and "alert" not in out
    assert '<rect id="ok"' in out


def test_mixed_case_script_and_onload_neutralized():
    out = sanitize_svg(
        wrap('<SCRIPT>alert(1)</SCRIPT><rect ONLOAD="evil()" width="1" height="1"/>')
    )
    assert "script" not in out and "SCRIPT" not in out and "alert" not in out
    assert "onload" not in out.lower() and "ONLOAD" not in out and "evil" not in out
    assert '<rect width="1" height="1"' in out
    assert '<rect id="ok"' in out


def test_entity_obfuscated_javascript_href_dropped():
    # html.parser decodes java&#115;cript: in the attribute value before
    # any check sees it; the <a> is dropped with its content regardless.
    out = sanitize_svg(wrap('<a href="java&#115;cript:alert(1)">click</a>'))
    assert "javascript" not in out.lower() and "alert" not in out and "click" not in out
    assert '<rect id="ok"' in out


def test_doctype_internal_entity_declaration_dropped():
    src = (
        '<!DOCTYPE svg [ <!ENTITY xx "javascript:alert(1)"> ]>'
        + wrap("<text>pre&amp;post</text>&xx;")
    )
    out = sanitize_svg(src)
    assert "DOCTYPE" not in out and "ENTITY" not in out
    assert "javascript" not in out and "alert" not in out  # &xx; never expands
    assert "<text>pre&amp;post</text>" in out
    assert '<rect id="ok"' in out


# --- the keep side: what page theming needs -------------------------------


def test_style_var_tokens_survive_verbatim():
    out = sanitize_svg(
        wrap('<rect style="fill: var(--jm-accent); stroke: var(--jm-ink)" width="1"/>')
    )
    assert 'style="fill: var(--jm-accent); stroke: var(--jm-ink)"' in out


def test_fill_fragment_url_survives():
    out = sanitize_svg(wrap('<rect fill="url(#grad)" width="1"/>'))
    assert 'fill="url(#grad)"' in out


def test_use_fragment_href_survives():
    out = sanitize_svg(wrap('<use href="#sym"/>'))
    assert '<use href="#sym"' in out


def test_camel_case_names_restored():
    out = sanitize_svg(
        wrap(
            "<defs>"
            '<linearGradient id="g"><stop offset="0"/></linearGradient>'
            '<radialGradient id="r"/><clipPath id="c"><rect width="1"/></clipPath>'
            '<marker id="m" markerWidth="7" markerHeight="7" refX="9" refY="5" '
            'orient="auto" markerUnits="strokeWidth" viewBox="0 0 10 10"/>'
            "</defs>"
            '<rect preserveAspectRatio="xMidYMid meet" width="1"/>',
        )
    )
    assert 'viewBox="0 0 20 20"' in out and "viewbox=" not in out
    assert "<linearGradient" in out and "</linearGradient>" in out
    assert "lineargradient" not in out
    assert "<radialGradient" in out and "<clipPath" in out and "</clipPath>" in out
    assert 'markerWidth="7"' in out and 'markerHeight="7"' in out
    assert 'refX="9"' in out and 'refY="5"' in out
    assert 'markerUnits="strokeWidth"' in out
    assert 'preserveAspectRatio="xMidYMid meet"' in out


def test_no_svg_root_raises_value_error():
    with pytest.raises(ValueError, match="svg"):
        sanitize_svg("<p>not an svg at all</p>")
    with pytest.raises(ValueError, match="svg"):
        sanitize_svg("<rect width='1' height='1'/>")  # allowed tag, no root


def test_second_svg_root_raises_value_error():
    with pytest.raises(ValueError, match="more than one"):
        sanitize_svg("<svg><rect/></svg><svg><rect/></svg>")


def test_unclosed_svg_root_raises_value_error():
    with pytest.raises(ValueError, match="never closed"):
        sanitize_svg("<svg><rect width='1' height='1'/>")
