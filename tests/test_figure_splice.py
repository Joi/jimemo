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


# --- hardening: RED against the draft as ported (5b30c27) -----------------
#
# Each case below survived the draft sanitizer. Found by the trusted-side
# probe of the draft and by the independent spec/plan reviews (jimemo#4smm).

EVIL = "evil.example"


def _rect(attr: str) -> str:
    return sanitize_svg(wrap("<rect {0} width=\"1\"/>".format(attr)))


@pytest.mark.parametrize("style", [
    # CSS escapes: a browser decodes the escape BEFORE tokenizing, so
    # u\72l( is a url( token. Any backslash refuses the value.
    r"fill:u\72l(https://evil.example/x)",
    r"fill:u\72 l(https://evil.example/x)",
    r"fill:\75rl(https://evil.example/x)",
    r"fill:url\28https://evil.example/x\29",
    "fill:u&#92;72l(https://evil.example/x)",  # entity-encoded backslash
    "fill:var(--jm-accent)\\",                  # a bare trailing backslash
])
def test_style_with_backslash_dropped(style):
    out = _rect('style="{0}"'.format(style))
    assert "style" not in out and EVIL not in out and "\\" not in out
    assert '<rect width="1"' in out and '<rect id="ok"' in out


@pytest.mark.parametrize("style", [
    # Comment stripping is unsound inside an unquoted url( token, where a
    # browser reads /* as URL text: this strips to url(#g) but fetches.
    "background:url(/*);background:url(https://evil.example/p);/*x*/#g)",
    "fill:url(/**/#g)",
    "fill:var(--jm-accent)/* note */",
    "fill:var(--jm-accent);*/",
])
def test_style_with_comment_delimiter_dropped(style):
    out = _rect('style="{0}"'.format(style))
    assert "style" not in out and EVIL not in out and "/*" not in out
    assert '<rect width="1"' in out


@pytest.mark.parametrize("style", [
    # Functions that fetch without a url( token, and anything else not on
    # the allowlist. The WHOLE identifier before "(" is judged.
    "background-image:image-set('https://evil.example/a' 1x)",
    "background-image:IMAGE-SET('https://evil.example/a' 1x)",
    "background-image:-webkit-image-set('https://evil.example/a' 1x)",
    "background:src('https://evil.example/a')",
    "background:image('https://evil.example/a')",
    "background:cross-fade('https://evil.example/a', red)",
    "background:element(#x)",
    "content:attr(data-x)",
    "background:paint(x)",
    "fill:(red)",
    "fill:url (#g)",
    "fill:érgb(1,2,3)",
    "fill:foo1rgb(1,2,3)",
    "fill:_rgb(1,2,3)",
    "fill:-rgb(1,2,3)",
    "fill:var(jm-accent)",  # var( must name a custom property
])
def test_style_with_non_allowlisted_function_dropped(style):
    out = _rect('style="{0}"'.format(style))
    assert "style" not in out and EVIL not in out
    assert '<rect width="1"' in out and '<rect id="ok"' in out


@pytest.mark.parametrize("attr", [
    r'fill="u\72l(https://evil.example/x)"',
    "fill=\"image-set('https://evil.example/a' 1x)\"",
    'stroke="url(/**/#g)"',
    'clip-path="url(/*);x:url(https://evil.example/p);/*x*/#g)"',
    'marker-end="element(#x)"',
])
def test_paint_attribute_gets_the_same_css_value_rules(attr):
    out = _rect(attr)
    name = attr.split("=", 1)[0]
    assert name + "=" not in out and EVIL not in out
    assert '<rect width="1"' in out


@pytest.mark.parametrize("src, expected", [
    # title/desc are HTML integration points in a browser: a child
    # <title /> is parsed as an HTML <title> (RCDATA, slash ignored) and
    # swallows the rest of the page. They are text-only here.
    ("<desc><title /></desc>", ["<desc></desc>"]),
    ("<desc><rect/>text</desc>", ["<desc>text</desc>"]),
    ("<desc><g><rect/></g>after</desc>", ["<desc>after</desc>"]),
    ("<desc><svg><rect/></svg>z</desc>", ["<desc>z</desc>"]),
    ("<desc>a &lt;b&gt; c</desc>", ["<desc>a &lt;b&gt; c</desc>"]),
    # <title>: CPython 3.13+ html.parser reads its content as RCDATA
    # (children arrive as text and are escaped); older versions parse the
    # children, which the text-only rule drops. Either way no element.
    ("<title><title/></title>",
     ["<title></title>", "<title>&lt;title/&gt;</title>"]),
    ("<title><tspan>x</tspan>y</title>",
     ["<title>y</title>", "<title>&lt;tspan&gt;x&lt;/tspan&gt;y</title>"]),
])
def test_title_and_desc_are_text_only(src, expected):
    out = sanitize_svg(wrap(src))
    frame = '<svg viewBox="0 0 20 20">{0}<rect id="ok" width="4" height="4" /></svg>'
    assert out in [frame.format(e) for e in expected]


def test_title_desc_malformed_nesting_never_emits_a_child_element():
    # <g> opened inside <desc> and "closed" by </desc>: the discard ends
    # at </g> or end of input, never leaking an element into <desc>.
    out = sanitize_svg(wrap("<desc>d<g></g></desc>"))
    assert out.startswith('<svg viewBox="0 0 20 20"><desc>d</desc><rect id="ok"')
    # An element inside <desc> that never closes discards to EOF, so the
    # root never closes: fail closed.
    with pytest.raises(ValueError, match="never closed"):
        sanitize_svg("<svg><desc><g>never closed</desc></svg>")
    # The same inside <title> is either that (older CPython) or RCDATA
    # text (3.13+); in neither case does an element land inside <title>.
    try:
        out = sanitize_svg("<svg><title><g>never closed</title></svg>")
    except ValueError:
        pass
    else:
        assert out == "<svg><title>&lt;g&gt;never closed</title></svg>"


@pytest.mark.parametrize("attr, expected", [
    ('href=" #a"', 'href="#a"'),
    ('href="&#9;#a"', 'href="#a"'),
    ('xlink:href=" #Mark "', 'xlink:href="#Mark"'),
    ('href="#Mark"', 'href="#Mark"'),
    ('href="#\n Mi xed"', 'href="#Mixed"'),
])
def test_use_fragment_href_emitted_stripped_with_case_preserved(attr, expected):
    out = sanitize_svg(wrap('<g id="Mark"/><use {0}/>'.format(attr)))
    assert "<use " + expected + " />" in out


def test_malformed_markup_is_a_value_error_not_a_parser_exception(monkeypatch):
    # Older CPythons raise AssertionError / NotImplementedError from
    # html.parser on input such as <![bogus]>; whatever the parser
    # raises must surface as ValueError (render turns it into a
    # figure-named ContentError).
    from html.parser import HTMLParser

    # Real input: CPython 3.9 raises NotImplementedError here, 3.13+
    # parses it as a bogus comment. Either a clean result or ValueError
    # is acceptable; any other exception type escapes this test.
    try:
        assert sanitize_svg("<svg><![bogus]><rect/></svg>") == "<svg><rect /></svg>"
    except ValueError as e:
        assert "malformed" in str(e)

    def boom(self, data):
        raise AssertionError("unknown status keyword")

    monkeypatch.setattr(HTMLParser, "feed", boom)
    with pytest.raises(ValueError, match="malformed"):
        sanitize_svg("<svg><rect/></svg>")


def test_sanitize_svg_with_ids_reads_parsed_attributes_only():
    from jimemo.sanitize import sanitize_svg_with_ids

    svg, ids = sanitize_svg_with_ids(
        '<svg><defs><linearGradient id="grad"/></defs>'
        '<text>example id="fake"</text><rect id="r1"/>'
        '<script id="gone"/><desc><g id="also-gone"/></desc></svg>'
    )
    assert ids == ["grad", "r1"]  # text content and dropped elements: no
    assert svg == sanitize_svg(
        '<svg><defs><linearGradient id="grad"/></defs>'
        '<text>example id="fake"</text><rect id="r1"/>'
        '<script id="gone"/><desc><g id="also-gone"/></desc></svg>'
    )


# --- hardening: PRESERVATION (already true of the draft; must stay) -------


@pytest.mark.parametrize("style", [
    "fill:expression(alert(1))",
    "fill:ur/**/l(https://evil.example/x)",
    "behavior:url(#default#time2)",
    "fill:url( https://evil.example/x)",
    'fill:url("https://evil.example/x")',
    "fill:url('#g')",  # quoted fragment: refused, docs teach the bare form
])
def test_style_vectors_the_draft_already_refused(style):
    out = _rect("style='{0}'".format(style) if '"' in style
                else 'style="{0}"'.format(style))
    assert "style" not in out and EVIL not in out


@pytest.mark.parametrize("attr", [
    'marker-end="url( https://evil.example/x)"',
    "stroke='url(\"https://evil.example/x\")'",
    'fill="URL(https://evil.example/x)"',
])
def test_paint_vectors_the_draft_already_refused(attr):
    out = _rect(attr)
    assert EVIL not in out and attr.split("=", 1)[0] + "=" not in out


@pytest.mark.parametrize("style", [
    "fill:rgb(1,2,3);stroke:hsl(10 20% 30%);width:calc(100% - 2px);transform:rotate(45deg)",
    "fill:color-mix(in srgb, var(--jm-accent) 40%, transparent)",
    "fill:url(#strand);stroke:var(--jm-negative);stroke-width:1.5",
    "width:100%;height:auto;font-family:var(--jm-font-ui)",
    "fill:var(--jm-accent, rgba(0,0,0,.5));stroke-width:min(2px, 1vw)",
    "transform:translate(10px, 5px) scale(1.5) matrix(1,0,0,1,0,0)",
    "fill: VAR(--jm-accent); stroke: RGB(1 2 3)",
])
def test_theming_styles_survive_verbatim(style):
    out = _rect('style="{0}"'.format(style))
    assert 'style="{0}"'.format(style) in out


def test_paint_attribute_fragment_with_fallback_survives():
    out = _rect('fill="url(#grad) none" stroke="rgb(1,2,3)" marker-end="url(#arrOK)"')
    assert 'fill="url(#grad) none"' in out and 'stroke="rgb(1,2,3)"' in out
    assert 'marker-end="url(#arrOK)"' in out


def _doc_svg_inputs():
    """Complete SVG inputs PREPARED from every ```html block in
    docs/diagrams.md: the <figure> wrapper and literal "..." lines are
    dropped, and a block that is a fragment is wrapped in a root <svg>."""
    text = (Path(__file__).resolve().parents[1] / "docs" / "diagrams.md").read_text()
    inputs = []
    for block in text.split("```html")[1:]:
        lines = [
            line for line in block.split("```", 1)[0].splitlines()
            if line.strip() not in ("...", "</figure>")
            and not line.strip().startswith("<figure")
        ]
        body = "\n".join(lines).strip()
        if not body.startswith("<svg"):
            body = '<svg viewBox="0 0 760 340">' + body + "</svg>"
        inputs.append(body)
    return inputs


def test_documented_snippets_lose_nothing():
    inputs = _doc_svg_inputs()
    assert len(inputs) >= 6  # the docs teach at least six snippets
    for src in inputs:
        out = sanitize_svg(src)
        for token in ('style="', "var(--jm-", "url(#"):
            assert out.count(token) == src.count(token), (token, src)


# --- code review round (Stage 1 + independent Stage 2), chunk 2 -----------


@pytest.mark.parametrize("attr", [
    # html.parser decodes once -> "&#35;a". A second decode would read
    # "#a" and accept, while the browser reads the relative URL "&#35;a"
    # and fetches it: the judgement is made on the emitted string only.
    'href="&amp;#35;a"',
    'xlink:href="&amp;#x23;a"',
    'href="a#b"',
    'href="//evil.example/x.svg#a"',
    'href="data:image/svg+xml,&lt;svg id=&quot;a&quot;/&gt;#a"',
])
def test_use_href_is_judged_on_the_emitted_value(attr):
    out = sanitize_svg(wrap("<use {0}/>".format(attr)))
    assert "href" not in out and "<use />" in out


def test_attributes_outside_the_allowlist_are_dropped():
    # filter, mask and cursor are the fetch-capable presentation
    # attributes the allowlist depends on excluding.
    out = sanitize_svg(wrap(
        '<rect filter="url(https://evil.example/f)" mask="url(https://evil.example/m)" '
        'cursor="url(https://evil.example/c),auto" xml:base="https://evil.example/" '
        'data-x="1" formaction="javascript:1" src="https://evil.example" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" width="1"/>'
    ))
    assert '<rect width="1" />' in out and EVIL not in out


def test_repeated_attribute_keeps_the_first_like_a_browser():
    from jimemo.sanitize import sanitize_svg_with_ids

    svg, ids = sanitize_svg_with_ids(
        '<svg><rect id="a" id="b" style="fill:var(--jm-accent)" '
        'style="fill:red" width="1"/></svg>'
    )
    assert svg == '<svg><rect id="a" style="fill:var(--jm-accent)" width="1" /></svg>'
    assert ids == ["a"]
    # A refused first value does not let a later duplicate through either.
    out = sanitize_svg('<svg><rect style="fill:url(https://evil.example/x)" style="fill:red"/></svg>')
    assert out == "<svg><rect /></svg>"


def test_nested_svg_and_self_closing_root():
    assert sanitize_svg('<svg viewBox="0 0 1 1"/>') == '<svg viewBox="0 0 1 1" />'
    out = sanitize_svg(wrap('<svg x="1" y="1" onload="evil()"><rect width="1"/></svg>'))
    assert '<svg x="1" y="1"><rect width="1" /></svg>' in out and "evil" not in out


@pytest.mark.parametrize("style, kept", [
    ("--x:url(https://evil.example/x);fill:var(--x)", False),   # custom property definition
    ("--x:image-set('https://evil.example/x' 1x)", False),
    ("fill:url(https://evil.example/x) !important", False),
    ("fill:var(--jm-accent) !important;--pad:calc(2px + 1px)", True),
])
def test_custom_property_definitions_and_important(style, kept):
    out = _rect('style="{0}"'.format(style))
    assert ('style="' in out) is kept and EVIL not in out


def test_large_malformed_input_is_processed_in_linear_time():
    # Both shapes were quadratic (seconds at this size) before the open-tag
    # counter and the indexed prefix checks; linear is a small fraction of
    # a second. The bound is generous on purpose: it separates the two
    # growth rates, it is not a benchmark.
    import time

    n = 60000
    start = time.perf_counter()
    out = sanitize_svg("<svg>" + "<g>" * n + "</circle>" * n + "</svg>")
    assert out == "<svg>" + "<g>" * n + "</g>" * n + "</svg>"
    style = "fill:rgb(0,0,0);" * (n * 3)
    assert 'style="' in sanitize_svg('<svg style="' + style + '"></svg>')
    assert time.perf_counter() - start < 10

