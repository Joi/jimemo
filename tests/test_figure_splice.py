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

from jimemo.content import load_content
from jimemo.errors import ContentError
from jimemo.manifest import load_manifest
from jimemo.render import render_page
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
    # A well-formed child first: dropped, the text and sibling stay.
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
    except ValueError as e:
        assert "never closed" in str(e)
    else:
        assert out == "<svg><title>&lt;g&gt;never closed</title></svg>"


@pytest.mark.parametrize("attr, expected", [
    ('href=" #a"', 'href="#a"'),
    ('href="&#9;#a"', 'href="#a"'),
    ('xlink:href=" #Mark "', 'xlink:href="#Mark"'),
    ('href="#Mark"', 'href="#Mark"'),
    ('href="\n#Mixed\t"', 'href="#Mixed"'),
    ('href="#caf\u00e9"', 'href="#caf\u00e9"'),  # non-ASCII ids are fine
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


# --- the render-level splice (render_page figures=) -----------------------

BRIEFING_DIR = Path(__file__).resolve().parents[1] / "templates" / "briefing"

GOOD_SVG = (
    '<svg viewBox="0 0 760 120" role="img" aria-label="A flow." '
    'style="width:100%;height:auto;font-family:var(--jm-font-ui)">'
    '<defs><linearGradient id="flow-grad"><stop offset="0" '
    'style="stop-color:var(--jm-accent)"/></linearGradient></defs>'
    '<rect x="10" y="10" width="200" height="60" '
    'style="fill:url(#flow-grad);stroke:var(--jm-border)"/>'
    '<text x="20" y="40" style="fill:var(--jm-text)">Flow &amp; more</text>'
    "</svg>"
)

PLAIN_SVG = '<svg viewBox="0 0 10 10"><rect width="5" height="5"/></svg>'


@pytest.fixture
def _isolated_home(tmp_path, monkeypatch):
    # See tests/test_render.py: keeps a personal ~/.jimemo theme on the
    # machine running the suite from shadowing the repo's.
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))


def _briefing_content(tmp_path, body: str):
    """Content for the real briefing template, loaded through the real
    markdown path, so the placeholder paragraph is whatever
    python-markdown + sanitize_html actually produce for it."""
    src = tmp_path / "content.md"
    src.write_text(
        '---\ntitle: "T"\ndate: "18 September 2026"\n---\n' + body
    )
    return load_content(src, load_manifest(BRIEFING_DIR))


BODY = "Before the figure.\n\n[[DIAGRAM:FLOW]]\n\nAFTER-THE-FIGURE marker.\n"


def test_splice_replaces_placeholder_and_page_passes_lint(tmp_path, _isolated_home):
    content = _briefing_content(tmp_path, BODY)
    # The documented placeholder form really is what the pipeline emits.
    assert "<p>[[DIAGRAM:FLOW]]</p>" in render_page(BRIEFING_DIR, content)

    html = render_page(BRIEFING_DIR, content, figures={"FLOW": GOOD_SVG})  # lint ran: no raise

    assert "[[DIAGRAM:" not in html
    assert '<figure class="jm-figure" style="contain:paint"><svg viewBox="0 0 760 120"' in html
    assert 'style="fill:url(#flow-grad);stroke:var(--jm-border)"' in html
    assert "Flow &amp; more</text></svg></figure>" in html
    figure_end = html.index("</figure>")
    assert html.index("Before the figure.") < html.index("<figure") < figure_end
    assert html.index("AFTER-THE-FIGURE") > figure_end


def test_splice_replaces_every_occurrence_of_one_name(tmp_path, _isolated_home):
    content = _briefing_content(tmp_path, "[[DIAGRAM:FLOW]]\n\nmid\n\n[[DIAGRAM:FLOW]]\n")
    html = render_page(BRIEFING_DIR, content, figures={"FLOW": GOOD_SVG})
    assert html.count('<figure class="jm-figure" ') == 2
    assert "[[DIAGRAM:" not in html


def test_without_figures_output_is_byte_identical(tmp_path, _isolated_home):
    content = _briefing_content(tmp_path, BODY)
    baseline = render_page(BRIEFING_DIR, content)
    assert render_page(BRIEFING_DIR, content, figures=None) == baseline
    assert render_page(BRIEFING_DIR, content, figures={}) == baseline
    assert "jm-figure" not in baseline


def test_unknown_placeholder_is_a_content_error(tmp_path, _isolated_home):
    content = _briefing_content(tmp_path, BODY)
    with pytest.raises(ContentError, match=r"\[\[DIAGRAM:NOPE\]\]"):
        render_page(BRIEFING_DIR, content, figures={"FLOW": GOOD_SVG, "NOPE": PLAIN_SVG})


def test_placeholder_not_alone_in_its_paragraph_is_not_spliced(tmp_path, _isolated_home):
    content = _briefing_content(tmp_path, "See [[DIAGRAM:FLOW]] inline.\n")
    with pytest.raises(ContentError, match="not as a paragraph of its own"):
        render_page(BRIEFING_DIR, content, figures={"FLOW": GOOD_SVG})


def test_malicious_svg_is_sanitized_before_it_lands(tmp_path, _isolated_home):
    evil = (
        '<svg viewBox="0 0 10 10" onload="evil()"><script>evil()</script>'
        '<foreignObject><iframe src="https://evil.example/"></iframe></foreignObject>'
        '<rect style="fill:url(https://evil.example/x)" width="5" height="5"/>'
        '<rect style="background:url(/*);background:url(https://evil.example/p);/*x*/#g)" '
        'width="4" height="4"/>'
        r'<rect style="fill:u\72l(https://evil.example/e)" width="3" height="3"/>'
        '<rect id="kept" style="fill:var(--jm-accent)" width="2" height="2"/></svg>'
    )
    html = render_page(
        BRIEFING_DIR, _briefing_content(tmp_path, BODY), figures={"FLOW": evil}
    )
    figure = html[html.index("<figure"):html.index("</figure>")]
    for gone in ("evil", "onload", "script", "foreignObject", "iframe", "\\"):
        assert gone not in figure, gone
    assert '<rect id="kept" style="fill:var(--jm-accent)" width="2" height="2" />' in figure


def test_title_inside_desc_cannot_swallow_the_page(tmp_path, _isolated_home):
    svg = '<svg viewBox="0 0 10 10"><desc><title /></desc><rect width="1" height="1"/></svg>'
    html = render_page(
        BRIEFING_DIR, _briefing_content(tmp_path, BODY), figures={"FLOW": svg}
    )
    figure = html[html.index("<figure"):html.index("</figure>")]
    assert "<title" not in figure
    assert html.index("AFTER-THE-FIGURE") > html.index("</figure>")


def test_non_svg_figure_is_a_content_error_naming_the_figure(tmp_path, _isolated_home):
    content = _briefing_content(tmp_path, BODY)
    with pytest.raises(ContentError, match=r"--figure FLOW: .*<svg>"):
        render_page(BRIEFING_DIR, content, figures={"FLOW": "<p>not an svg</p>"})
    with pytest.raises(ContentError, match=r"--figure FLOW: .*never closed"):
        render_page(BRIEFING_DIR, content, figures={"FLOW": "<svg><rect/>"})


def test_same_id_in_two_figures_is_a_content_error(tmp_path, _isolated_home):
    body = "[[DIAGRAM:A]]\n\n[[DIAGRAM:B]]\n"
    a = '<svg><defs><linearGradient id="grad"/></defs><rect fill="url(#grad)"/></svg>'
    b = '<svg><defs><radialGradient id="grad"/></defs><rect fill="url(#grad)"/></svg>'
    with pytest.raises(ContentError) as exc_info:
        render_page(BRIEFING_DIR, _briefing_content(tmp_path, body), figures={"A": a, "B": b})
    assert "--figure A and --figure B both define id='grad'" in str(exc_info.value)


def test_id_lookalike_in_svg_text_is_not_a_collision(tmp_path, _isolated_home):
    body = "[[DIAGRAM:A]]\n\n[[DIAGRAM:B]]\n"
    a = '<svg><text>example id="grad"</text></svg>'
    b = '<svg><rect id="grad" width="1" height="1"/></svg>'
    html = render_page(BRIEFING_DIR, _briefing_content(tmp_path, body), figures={"A": a, "B": b})
    assert html.count('<figure class="jm-figure" ') == 2


def test_one_figure_at_two_placeholders_may_repeat_its_own_ids(tmp_path, _isolated_home):
    svg = '<svg><defs><linearGradient id="grad"/></defs><rect fill="url(#grad)"/></svg>'
    html = render_page(
        BRIEFING_DIR,
        _briefing_content(tmp_path, "[[DIAGRAM:A]]\n\nmid\n\n[[DIAGRAM:A]]\n"),
        figures={"A": svg},
    )
    assert html.count('id="grad"') == 2


def test_placeholder_text_inside_a_figure_is_never_spliced(tmp_path, _isolated_home):
    # Figure A's text names figure B's placeholder. It is escaped text,
    # not a <p> paragraph, and placeholders are checked against the page
    # as rendered BEFORE any figure lands — B has no placeholder: error.
    a = "<svg><text>&lt;p&gt;[[DIAGRAM:B]]&lt;/p&gt;</text></svg>"
    with pytest.raises(ContentError, match=r"\[\[DIAGRAM:B\]\]"):
        render_page(
            BRIEFING_DIR, _briefing_content(tmp_path, "[[DIAGRAM:A]]\n"),
            figures={"A": a, "B": GOOD_SVG},
        )


def test_use_fragment_href_is_refused_by_lint_today(tmp_path, _isolated_home):
    # sanitize_svg keeps <use href="#id">, but the self-containment lint
    # accepts no fragment on a fetch-on-load attribute (jimemo#ktmx owns
    # that allowlist). Pinned so the documented limitation stays true —
    # and so a future lint change updates the docs with it.
    svg = '<svg><defs><g id="sym"><rect width="1" height="1"/></g></defs><use href="#sym"/></svg>'
    with pytest.raises(ContentError, match="use href") as exc_info:
        render_page(BRIEFING_DIR, _briefing_content(tmp_path, BODY), figures={"FLOW": svg})
    # lint cannot say where an error came from; the message points at the
    # one new input.
    assert "this page includes --figure SVG" in str(exc_info.value)


def test_figure_wrapper_contains_a_fixed_position_svg(tmp_path, _isolated_home):
    # `style` survives sanitization (theming needs it), so an SVG can ask
    # for position:fixed over the whole viewport. The wrapper's paint
    # containment confines it to the figure (measured in Chromium); this
    # pins that the wrapper carries it and that lint accepts the page.
    from jimemo.render import FIGURE_OPEN

    cover = ('<svg viewBox="0 0 1 1" style="position:fixed;top:0;left:0;width:100vw;'
             'height:100vh;z-index:99999"><text>spoof</text></svg>')
    html = render_page(
        BRIEFING_DIR, _briefing_content(tmp_path, BODY), figures={"FLOW": cover}
    )
    assert "contain:paint" in FIGURE_OPEN
    assert FIGURE_OPEN + "<svg" in html
    assert "position:fixed" in html  # the premise: style is kept, so the wrapper must contain it


# --- second review round: remaining branches, exact outputs ----------------


@pytest.mark.parametrize("attr", [
    'href="#a b"',            # interior space: refused, never "repaired" to #ab
    'href="#a\tb"',
    'href="#a\x7fb"',        # DEL
])
def test_use_href_with_interior_whitespace_or_control_is_dropped(attr):
    out = sanitize_svg(wrap("<use {0}/>".format(attr)))
    assert "href" not in out and "<use />" in out


@pytest.mark.parametrize("src, expected", [
    ('<svg viewBox="0 0 1 1"/><svg/>', ValueError),                      # second root, self-closing
    ("<svg><rect/></svg></g></svg>", "<svg><rect /></svg>"),             # stray close tags
    ("<svg><foo><foo><rect/></foo><rect/></foo><circle r=\"1\"/></svg>",
     '<svg><circle r="1" /></svg>'),                                     # nested same-name discard depth
    ("<svg><rect style width=\"1\"/></svg>", '<svg><rect style="" width="1" /></svg>'),  # valueless attribute
    ('<?xml version="1.0"?><svg><?pi x?><rect/></svg>', "<svg><rect /></svg>"),          # processing instructions
    ("<svg><text>a<tspan>b</text>c</svg>", "<svg><text>a<tspan>b</tspan></text>c</svg>"),  # mis-nested close
    ('<svg><rect href="#a" xlink:href="#a"/></svg>', "<svg><rect /></svg>"),             # href off <use>
    ("<svg></svg><g><rect/></g>trailing", "<svg></svg>"),                # content after the root: dropped
    ('<svg style="font-family:var(--jm-font-ui)"><rect/></svg>',
     '<svg style="font-family:var(--jm-font-ui)"><rect /></svg>'),       # token style on the ROOT
])
def test_remaining_parser_branches_exact_output(src, expected):
    if expected is ValueError:
        with pytest.raises(ValueError, match="more than one"):
            sanitize_svg(src)
    else:
        assert sanitize_svg(src) == expected


def test_cdata_section_never_yields_markup():
    # Older CPythons hand CDATA to unknown_decl (dropped); newer ones read
    # "<![CDATA[" as a bogus comment and the rest as text (escaped).
    out = sanitize_svg("<svg><text><![CDATA[<script>evil()</script>]]></text><rect/></svg>")
    assert "<script" not in out and out.endswith("<rect /></svg>")


def test_every_presentation_attribute_gets_the_css_value_rule():
    # font-family and stop-color cannot fetch in any browser; the rule is
    # applied anyway so no url(https://…) text is ever emitted.
    out = sanitize_svg(
        '<svg><text font-family="url(https://evil.example/f)" '
        'stop-color="url(https://evil.example/s)" opacity="expression(1)" '
        'transform="rotate(45 10 10) translate(5)" font-size="calc(10px + 2px)" '
        'aria-label="Revenue (2024) — see url(https://evil.example/ok-in-a-label)">t</text></svg>'
    )
    assert out == (
        '<svg><text transform="rotate(45 10 10) translate(5)" '
        'font-size="calc(10px + 2px)" aria-label="Revenue (2024) — see '
        'url(https://evil.example/ok-in-a-label)">t</text></svg>'
    )


@pytest.mark.parametrize("line", ["[[DIAGRAM:FLOW]] ", "[[DIAGRAM:FLOW]]   ", "- [[DIAGRAM:FLOW]]", "## [[DIAGRAM:FLOW]]"])
def test_placeholder_present_but_not_a_bare_paragraph_says_so(tmp_path, _isolated_home, line):
    content = _briefing_content(tmp_path, "Before.\n\n" + line + "\n\nAfter.\n")
    rendered = render_page(BRIEFING_DIR, content)
    if "<p>[[DIAGRAM:FLOW]]</p>" in rendered:
        pytest.skip("this markdown form renders as a bare paragraph")
    with pytest.raises(ContentError, match="is in the rendered page, but not as a paragraph of its own"):
        render_page(BRIEFING_DIR, content, figures={"FLOW": GOOD_SVG})


def test_placeholder_in_a_blockquote_is_spliced(tmp_path, _isolated_home):
    content = _briefing_content(tmp_path, "> [[DIAGRAM:FLOW]]\n")
    html = render_page(BRIEFING_DIR, content, figures={"FLOW": GOOD_SVG})
    assert "<blockquote>" in html and FIGURE_OPEN_TEXT in html


FIGURE_OPEN_TEXT = '<figure class="jm-figure" style="contain:paint"><svg'


def test_nul_is_normalized_like_a_browser_so_ids_still_collide(tmp_path, _isolated_home):
    # HTML parsing turns U+0000 into U+FFFD, so these two ids are the same
    # id in the page although they differ as Python strings.
    from jimemo.sanitize import sanitize_svg_with_ids

    svg, ids = sanitize_svg_with_ids('<svg><linearGradient id="g\x00"/><text>a\x00b</text></svg>')
    assert "\x00" not in svg and ids == ["g\ufffd"]
    with pytest.raises(ContentError, match="both define id"):
        render_page(
            BRIEFING_DIR, _briefing_content(tmp_path, "[[DIAGRAM:A]]\n\n[[DIAGRAM:B]]\n"),
            figures={"A": '<svg><linearGradient id="g\x00"/></svg>',
                     "B": '<svg><linearGradient id="g\ufffd"/></svg>'},
        )


# --- pre-handoff review: ids as the browser sees them ----------------------


@pytest.mark.parametrize("raw_id", ["g&#13;x", "g&#10;x", "g x", "g&#9;x", "g\r\nx", "\x7fg", ""])
def test_id_with_whitespace_or_control_is_dropped_not_recorded(raw_id):
    # Not a valid id, and exactly where Python's and a browser's view of
    # the value part: a browser turns a literal CR into LF while reading
    # the page, so id="g\rx" and id="g\nx" would collide unnoticed.
    from jimemo.sanitize import sanitize_svg_with_ids

    svg, ids = sanitize_svg_with_ids('<svg><linearGradient id="{0}" x1="0"/></svg>'.format(raw_id))
    assert svg == '<svg><linearGradient x1="0" /></svg>' and ids == []


def test_carriage_return_is_serialized_as_a_character_reference():
    # A literal CR in the page is normalized to LF by the browser; &#13;
    # is not. Emitting the reference keeps both views of the value equal.
    out = sanitize_svg('<svg><text aria-label="a&#13;b">x&#13;&#10;y\r\nz</text></svg>')
    assert "\r" not in out
    assert out == '<svg><text aria-label="a&#13;b">x&#13;\ny&#13;\nz</text></svg>'


def test_figure_id_already_used_by_the_page_is_a_content_error(tmp_path, _isolated_home):
    # The page's own id comes from the content here (an <a id> anchor, which
    # sanitize_html allows); a chart canvas id is the case that breaks a
    # page outright — see the chart test below.
    body = '<a id="methods"></a>Methods.\n\n[[DIAGRAM:FLOW]]\n'
    svg = '<svg><rect id="methods" width="1" height="1"/></svg>'
    with pytest.raises(ContentError, match=r"--figure FLOW defines id='methods', which the page already uses"):
        render_page(BRIEFING_DIR, _briefing_content(tmp_path, body), figures={"FLOW": svg})
    # text that merely looks like an id attribute is not one
    body = 'Write `id="methods"` in your SVG.\n\n[[DIAGRAM:FLOW]]\n'
    html = render_page(BRIEFING_DIR, _briefing_content(tmp_path, body), figures={"FLOW": svg})
    assert FIGURE_OPEN_TEXT in html


def test_figure_cannot_take_a_chart_canvas_id(tmp_path, _isolated_home):
    # A figure element with the canvas's id would come first in the
    # document, so the chart's getElementById would find the SVG element
    # and the chart would never draw.
    import re

    chart_dir = BRIEFING_DIR.parent / "chart-dashboard"
    sample = (chart_dir / "sample" / "content.yaml").read_text()
    start, end = sample.index("intro: |"), sample.index("chart_data_line:")
    src = tmp_path / "content.yaml"
    src.write_text(sample[:start] + "intro: |\n  [[DIAGRAM:A]]\n\n" + sample[end:])
    content = load_content(src, load_manifest(chart_dir))

    page = render_page(chart_dir, content)
    canvas_id = re.search(r'<canvas[^>]* id="([^"]+)"', page).group(1)
    clash = '<svg><rect id="{0}" width="10" height="10"/></svg>'.format(canvas_id)
    with pytest.raises(ContentError, match="which the page already uses"):
        render_page(chart_dir, content, figures={"A": clash})
    # a prefixed id is fine, and the chart page still passes its script lint
    ok = '<svg><rect id="fig-a-box" width="10" height="10"/></svg>'
    assert FIGURE_OPEN_TEXT in render_page(chart_dir, content, figures={"A": ok})


# --- jimemo#86jn: the sanitizer reports what it dropped --------------------

# Output frozen from the implementation at dd62d75, BEFORE the drop report
# existed: (source, markup, ids). sanitize_svg, sanitize_svg_with_ids and
# sanitize_svg_with_report all call one implementation, so comparing them
# with each other proves only that the wrappers agree; and most vector tests
# above assert on substrings, which an escaping, ordering or serialization
# regression would survive. This battery is what pins the VALUES.
FROZEN_VECTORS = [
    (
        '<svg viewBox="0 0 10 10"><rect style="fill: var(--jm-accent); stroke: var(--jm-ink)" width="4" height="4"/></svg>',
        '<svg viewBox="0 0 10 10"><rect style="fill: var(--jm-accent); stroke: var(--jm-ink)" width="4" height="4" /></svg>',
        [],
    ),
    (
        '<svg><rect style="fill:u\\72l(https://e.example/x)" width="4" height="4"/></svg>',
        '<svg><rect width="4" height="4" /></svg>',
        [],
    ),
    (
        '<svg><rect style="fill:url(/*);background:url(https://e.example/p);/*x*/#g)" width="1" height="1"/></svg>',
        '<svg><rect width="1" height="1" /></svg>',
        [],
    ),
    (
        '<svg><rect fill="url(#grad)" width="1" height="1"/><circle fill="url(https://e.example/x)" r="1"/></svg>',
        '<svg><rect fill="url(#grad)" width="1" height="1" /><circle r="1" /></svg>',
        [],
    ),
    (
        '<svg><use href="#sym"/><use href="https://e.example/x.svg#a"/><use xlink:href="other.svg#x"/></svg>',
        '<svg><use href="#sym" /><use /><use /></svg>',
        [],
    ),
    (
        '<svg><rect href="#a" width="1" height="1"/></svg>',
        '<svg><rect width="1" height="1" /></svg>',
        [],
    ),
    (
        '<svg viewBox="0 0 8 8"><defs><linearGradient id="g" gradientUnits="userSpaceOnUse"><stop offset="0"/></linearGradient><clipPath id="c"><rect width="1" height="1"/></clipPath></defs><marker markerWidth="4" markerHeight="4" refX="2" refY="2" markerUnits="strokeWidth"/></svg>',
        '<svg viewBox="0 0 8 8"><defs><linearGradient id="g" gradientUnits="userSpaceOnUse"><stop offset="0" /></linearGradient><clipPath id="c"><rect width="1" height="1" /></clipPath></defs><marker markerWidth="4" markerHeight="4" refX="2" refY="2" markerUnits="strokeWidth" /></svg>',
        ['g', 'c'],
    ),
    (
        '<svg><text>Flow &amp; more &lt;x&gt;</text></svg>',
        '<svg><text>Flow &amp; more &lt;x&gt;</text></svg>',
        [],
    ),
    (
        '<svg><text aria-label="a&#13;b">x&#13;&#10;y\r\nz</text></svg>',
        '<svg><text aria-label="a&#13;b">x&#13;\ny&#13;\nz</text></svg>',
        [],
    ),
    (
        '<svg><linearGradient id="g\x00"/><text>a\x00b</text></svg>',
        '<svg><linearGradient id="g�" /><text>a�b</text></svg>',
        ['g�'],
    ),
    (
        '<svg><rect id="keep-me" width="1" height="1"/><circle id="a b" r="1"/><ellipse id="" rx="1"/></svg>',
        '<svg><rect id="keep-me" width="1" height="1" /><circle r="1" /><ellipse rx="1" /></svg>',
        ['keep-me'],
    ),
    (
        '<svg><rect id="a\xa0b" width="1" height="1"/><circle id="a\x85b" r="1"/></svg>',
        '<svg><rect id="a\xa0b" width="1" height="1" /><circle id="a\x85b" r="1" /></svg>',
        ['a\xa0b', 'a\x85b'],
    ),
    (
        '<svg><desc><g><rect width="1" height="1"/></g>kept text</desc><title><g/>t</title></svg>',
        '<svg><desc>kept text</desc><title>&lt;g/&gt;t</title></svg>',
        [],
    ),
    (
        '<svg><script>alert(1)</script><blink><rect width="9" height="9"/></blink><rect id="ok" width="4" height="4"/></svg>',
        '<svg><rect id="ok" width="4" height="4" /></svg>',
        ['ok'],
    ),
    (
        '<svg onload="x()"><rect onclick="y()" data-x="1" width="1" height="1"/></svg>',
        '<svg><rect width="1" height="1" /></svg>',
        [],
    ),
    (
        '<svg viewBox="0 0 2 2"/>',
        '<svg viewBox="0 0 2 2" />',
        [],
    ),
    (
        '<svg><rect style="fill:var(--jm-a)" style="fill:red" id="a" id="b" width="1" height="1"/></svg>',
        '<svg><rect style="fill:var(--jm-a)" id="a" width="1" height="1" /></svg>',
        ['a'],
    ),
    (
        '<p>before</p><svg><rect width="1" height="1"/></svg><g/>after',
        '<svg><rect width="1" height="1" /></svg>',
        [],
    ),
    (
        '<svg><path d="M0 0 L10 10" aria-label="Revenue (2024)"/></svg>',
        '<svg><path d="M0 0 L10 10" aria-label="Revenue (2024)" /></svg>',
        [],
    ),
]


def test_sanitizer_output_is_frozen_at_dd62d75():
    from jimemo.sanitize import sanitize_svg_with_ids

    for src, markup, ids in FROZEN_VECTORS:
        assert sanitize_svg(src) == markup, src
        assert sanitize_svg_with_ids(src) == (markup, ids), src


def _drops(src):
    from jimemo.sanitize import sanitize_svg_with_report

    return sanitize_svg_with_report(src).drops


def _drop(kind, name, reason):
    from jimemo.sanitize import SvgDrop

    return SvgDrop(kind, name, reason)


def test_report_siblings_agree_and_keep_their_signatures():
    # Wrapper consistency only; the frozen battery above pins the values.
    from jimemo.sanitize import sanitize_svg_with_ids, sanitize_svg_with_report

    for src, _markup, _ids in FROZEN_VECTORS:
        result = sanitize_svg_with_report(src)
        pair = sanitize_svg_with_ids(src)
        assert type(pair) is tuple and len(pair) == 2
        assert isinstance(pair[0], str) and isinstance(pair[1], list)
        assert result.markup == sanitize_svg(src) == pair[0]
        assert result.ids == pair[1]


def test_a_clean_figure_reports_no_drops():
    assert _drops(GOOD_SVG) == []


def test_dropped_elements_are_reported_with_a_reason():
    drops = _drops(
        "<svg><script>alert(1)<g/></script><blink><rect/><foo/></blink>"
        + SIBLING + "</svg>"
    )
    # Only the outermost element of each discarded subtree is reported.
    assert drops == [
        _drop("element", "script", "not allowlisted"),
        _drop("element", "blink", "not allowlisted"),
    ]


def test_dropped_self_closing_element_is_reported():
    assert _drops("<svg><image/><rect/></svg>") == [
        _drop("element", "image", "not allowlisted"),
    ]


def test_dropped_attributes_are_reported_with_a_reason():
    drops = _drops('<svg onload="x()"><rect data-x="1" ONCLICK="y()" width="1"/></svg>')
    assert drops == [
        _drop("attribute", "onload", "not allowlisted"),
        _drop("attribute", "data-x", "not allowlisted"),
        _drop("attribute", "onclick", "not allowlisted"),
    ]


def test_refused_css_value_is_reported_without_the_value():
    from jimemo.render import _figure_drop_warnings

    secret = "SECRET-VALUE-https://e.example/x"
    drops = _drops(
        '<svg><rect style="fill:u\\72l({0})" fill="url({0})" width="1"/></svg>'.format(secret)
    )
    assert drops == [
        _drop("attribute", "style", "css value refused"),
        _drop("attribute", "fill", "css value refused"),
    ]
    lines = _figure_drop_warnings("FLOW", drops)
    assert lines == [
        "figure FLOW: dropped attribute style (css value refused)",
        "figure FLOW: dropped attribute fill (css value refused)",
    ]
    assert not any("SECRET" in line or "e.example" in line for line in lines)


def test_refused_href_is_reported():
    drops = _drops(
        '<svg><use href="https://e.example/x#a"/><use xlink:href="o.svg#x"/>'
        '<rect href="#a" width="1"/></svg>'
    )
    assert drops == [
        _drop("attribute", "href", "href not a same-document #fragment"),
        _drop("attribute", "xlink:href", "href not a same-document #fragment"),
    ]


def test_element_inside_desc_is_reported_as_such():
    # <desc>, not <title>: html.parser on 3.13+ reads <title> content as
    # RCDATA, so an element written there never reaches a start-tag
    # callback (see test_title_desc_malformed_nesting_never_emits_a_child_
    # element) and a <title> test would pass without testing anything.
    assert _drops("<svg><desc><g><rect/></g>text</desc></svg>") == [
        _drop("element", "g", "child of <title>/<desc>"),
    ]
    assert _drops("<svg><desc>a<rect/>b</desc></svg>") == [
        _drop("element", "rect", "child of <title>/<desc>"),
    ]


def test_reason_precedence_allowlist_before_text_only():
    assert _drops("<svg><desc><script>x</script></desc></svg>") == [
        _drop("element", "script", "not allowlisted"),
    ]


def test_element_outside_the_root_is_reported_as_such():
    assert _drops("<p>before</p><svg></svg><g/>") == [
        _drop("element", "p", "outside the <svg> root"),
        _drop("element", "g", "outside the <svg> root"),
    ]


def test_dropped_id_is_reported():
    assert _drops('<svg><rect id="a b"/><circle id=""/></svg>') == [
        _drop("attribute", "id", "id is not a plain token"),
    ]


def test_an_id_above_u007f_is_still_kept_and_recorded():
    # The id predicate is ord <= 0x20 or == 0x7F, NOT Unicode whitespace:
    # U+00A0 and U+0085 survive today and must keep surviving.
    from jimemo.sanitize import sanitize_svg_with_report

    result = sanitize_svg_with_report('<svg><rect id="a b"/><circle id="a\u0085b"/></svg>')
    assert result.ids == ["a b", "a\u0085b"]
    assert result.drops == []


def test_a_repeated_attribute_is_not_reported():
    # A browser keeps the first too: nothing the page shows differs from
    # what the author wrote, so there is nothing to warn about.
    src = '<svg><rect style="fill:var(--jm-a)" style="fill:red" id="a" id="b"/></svg>'
    assert _drops(src) == []


def test_drops_are_deduplicated_in_first_seen_order():
    drops = _drops(
        '<svg><script/><rect style="a:b(1)"/><script>x</script>'
        '<circle style="c:d(2)"/><script/></svg>'
    )
    assert drops == [
        _drop("element", "script", "not allowlisted"),
        _drop("attribute", "style", "css value refused"),
    ]


def test_drop_label_filters_every_unsafe_code_point():
    # Direct: html.parser never delivers a newline inside a tag or
    # attribute name, so a markup-driven newline test would pass with the
    # filter removed.
    from jimemo.sanitize import _svg_drop_label

    for name in ["a\x1b[31mb", "a\x07b", "a\nb", "a\rb", "a‮b", "a\x7fb",
                 "héllo", "a b", "a\x00b"]:
        label = _svg_drop_label(name)
        assert all(0x21 <= ord(ch) <= 0x7E for ch in label), (name, label)
        assert label.startswith("a") or label.startswith("h")
    assert _svg_drop_label("a\x1b[31mb") == "a?[31mb"
    assert _svg_drop_label("") == "?"
    long = _svg_drop_label("x" * 200)
    assert len(long) == 40 and long == "x" * 37 + "..."
    assert _svg_drop_label("x" * 40) == "x" * 40


def test_drop_labels_from_real_markup_are_filtered():
    drops = _drops('<svg><a\x1b[31mb><rect/></a\x1b[31mb><rect da\x07ta="1"/></svg>')
    assert [d.kind for d in drops] == ["element", "attribute"]
    for d in drops:
        assert all(0x21 <= ord(ch) <= 0x7E for ch in d.name), d


def test_render_warns_once_per_distinct_drop(tmp_path, _isolated_home, capsys):
    svg = (
        '<svg><rect style="fill:u\\72l(https://e.example/leak)" width="1"/>'
        "<script>alert(1)</script>"
        '<circle style="fill:image-set(\'https://e.example/leak\' 1x)" r="1"/>'
        '<ellipse style="x:y(1)" rx="1"/></svg>'
    )
    html = render_page(BRIEFING_DIR, _briefing_content(tmp_path, BODY), figures={"FLOW": svg})
    assert FIGURE_OPEN_TEXT in html
    err = capsys.readouterr().err
    figure_lines = [line for line in err.splitlines() if line.startswith("warning: figure")]
    assert figure_lines == [
        "warning: figure FLOW: dropped attribute style (css value refused)",
        "warning: figure FLOW: dropped element script (not allowlisted)",
    ]
    assert "leak" not in err and "e.example" not in err


def test_a_clean_figure_warns_nothing(tmp_path, _isolated_home, capsys):
    content = _briefing_content(tmp_path, BODY)
    render_page(BRIEFING_DIR, content)
    baseline_err = capsys.readouterr().err
    render_page(BRIEFING_DIR, content, figures={"FLOW": GOOD_SVG})
    assert capsys.readouterr().err == baseline_err
    assert "warning: figure" not in baseline_err


def test_many_distinct_drops_are_capped(tmp_path, _isolated_home, capsys):
    def figure(n):
        return "<svg>" + "".join("<x{0}/>".format(i) for i in range(n)) + "</svg>"

    content = _briefing_content(tmp_path, BODY)
    render_page(BRIEFING_DIR, content, figures={"FLOW": figure(30)})
    lines = [l for l in capsys.readouterr().err.splitlines() if l.startswith("warning: figure")]
    assert len(lines) == 21
    assert lines[0] == "warning: figure FLOW: dropped element x0 (not allowlisted)"
    assert lines[19] == "warning: figure FLOW: dropped element x19 (not allowlisted)"
    assert lines[20] == "warning: figure FLOW: 10 more distinct drops not shown"

    render_page(BRIEFING_DIR, content, figures={"FLOW": figure(20)})
    lines = [l for l in capsys.readouterr().err.splitlines() if l.startswith("warning: figure")]
    assert len(lines) == 20 and "not shown" not in lines[-1]


def test_warning_figure_name_is_filtered():
    from jimemo.render import _figure_drop_warnings

    lines = _figure_drop_warnings("F\x1b[2Jx", [_drop("element", "script", "not allowlisted")])
    assert lines == ["figure F?[2Jx: dropped element script (not allowlisted)"]


# --- jimemo#1gs5: page ids are collected as a browser reads them -----------


def test_page_id_with_nul_collides_with_a_figure_id_like_a_browser(tmp_path, _isolated_home):
    # Page-versus-figure (the NUL test above is figure-versus-figure). A
    # browser reads U+0000 as U+FFFD while parsing the page, so the anchor's
    # id and the figure's are one id there, and the figure's url(#…) would
    # resolve to the anchor.
    body = '<a id="g\x00"></a>Anchor.\n\n[[DIAGRAM:FLOW]]\n'
    content = _briefing_content(tmp_path, body)
    assert 'id="g\x00"' in render_page(BRIEFING_DIR, content)  # the page keeps NUL
    clash = '<svg><linearGradient id="g�"/></svg>'
    with pytest.raises(ContentError, match="which the page already uses"):
        render_page(BRIEFING_DIR, content, figures={"FLOW": clash})
    # control: without the NUL there is nothing to collide with
    content = _briefing_content(tmp_path, '<a id="g"></a>Anchor.\n\n[[DIAGRAM:FLOW]]\n')
    assert FIGURE_OPEN_TEXT in render_page(BRIEFING_DIR, content, figures={"FLOW": clash})


def test_only_the_first_id_attribute_of_a_page_element_is_collected(tmp_path, _isolated_home):
    # sanitize_html keeps both attributes; a browser keeps only the first.
    body = '<a id="first" id="second"></a>Anchor.\n\n[[DIAGRAM:FLOW]]\n'
    content = _briefing_content(tmp_path, body)
    assert 'id="first" id="second"' in render_page(BRIEFING_DIR, content)
    # no false refusal: "second" names nothing in the browser's page
    second = '<svg><rect id="second" width="1" height="1"/></svg>'
    assert FIGURE_OPEN_TEXT in render_page(BRIEFING_DIR, content, figures={"FLOW": second})
    # the real collision is still refused
    first = '<svg><rect id="first" width="1" height="1"/></svg>'
    with pytest.raises(ContentError, match="id='first', which the page already uses"):
        render_page(BRIEFING_DIR, content, figures={"FLOW": first})
    # an empty first id is still the element's id: the later one is ignored
    content = _briefing_content(tmp_path, '<a id="" id="second"></a>A.\n\n[[DIAGRAM:FLOW]]\n')
    assert FIGURE_OPEN_TEXT in render_page(BRIEFING_DIR, content, figures={"FLOW": second})
