import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.sanitize import sanitize_html


# --- the three reviewer payloads ---

def test_img_onerror_neutralized():
    out = sanitize_html("<p><img src=x onerror=alert(1)></p>")
    assert out == '<p><img src="x" /></p>'
    assert "onerror" not in out


def test_svg_onload_removed_with_entire_content():
    out = sanitize_html("<svg onload=alert(1)><circle cx=1 /></svg>after")
    assert out == "after"


def test_a_javascript_href_dropped_text_kept():
    out = sanitize_html('<a href="javascript:alert(1)">click</a>')
    assert out == "<a>click</a>"


# --- discard-tags: content fully removed ---

@pytest.mark.parametrize(
    "payload, marker",
    [
        ("<script>document.write('pwn')</script>", "document.write"),
        ("<style>body { background: url(evil) }</style>", "background"),
        ("<iframe src=https://evil.example>fallback</iframe>", "fallback"),
        ("<object data=x>inner</object>", "inner"),
        ("<embed src=x>inner</embed>", "inner"),
        ("<template><img src=x onerror=alert(1)></template>", "img"),
    ],
)
def test_dangerous_tag_content_discarded(payload, marker):
    out = sanitize_html("<p>before</p>" + payload + "<p>after</p>")
    assert marker not in out
    assert "<p>before</p>" in out
    assert "<p>after</p>" in out


def test_nested_svg_discarded_to_matching_close():
    out = sanitize_html("<svg><svg></svg><strong>hidden</strong></svg>visible")
    assert out == "visible"


def test_unterminated_script_discards_rest_of_document():
    # Failing closed: no close tag means nothing after it survives.
    out = sanitize_html("<p>ok</p><script>alert(1)")
    assert out == "<p>ok</p>"


# --- unwrap: unknown tags dropped, children kept ---

def test_unknown_tags_unwrapped_keep_text():
    out = sanitize_html('<div class="x"><span data-y="1">keep me</span></div>')
    assert out == "keep me"


# --- attribute allowlist ---

def test_non_allowlisted_attributes_stripped():
    out = sanitize_html('<p class="a" id="b">t</p>')
    assert out == "<p>t</p>"
    out = sanitize_html('<a href="https://e.com/x" target="_blank" rel="noopener" title="T">x</a>')
    assert out == '<a href="https://e.com/x" title="T">x</a>'


def test_on_attributes_stripped_even_on_allowlisted_tags():
    out = sanitize_html('<td align="left" ONCLICK="alert(1)" onmouseover=x>c</td>')
    assert out == '<td align="left">c</td>'


def test_table_alignment_style_preserved():
    for align in ("left", "center", "right"):
        cell = '<th style="text-align: {0};">H</th>'.format(align)
        assert sanitize_html(cell) == cell
        cell = '<td style="text-align: {0};">d</td>'.format(align)
        assert sanitize_html(cell) == cell


def test_non_alignment_style_dropped():
    assert sanitize_html('<td style="color:red">x</td>') == "<td>x</td>"
    assert (
        sanitize_html('<td style="text-align: left; background:url(evil)">x</td>')
        == "<td>x</td>"
    )


# --- URL scheme checks ---

@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        " javascript:alert(1)",
        "java&#09;script:alert(1)",
        "jav&#x0A;ascript:alert(1)",
        "&#106;avascript:alert(1)",
        "\tjavascript:alert(1)",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "data:text/html;base64,PHNjcmlwdD4=",
    ],
)
def test_bad_href_schemes_rejected(href):
    out = sanitize_html('<a href="{0}">x</a>'.format(href))
    assert out == "<a>x</a>"


@pytest.mark.parametrize(
    "href",
    [
        "https://example.com/page",
        "http://example.com/page",
        "relative/path.html",
        "./also/relative",
        "#section-2",
        "mailto:joi@ito.com",
        "?query=only",
    ],
)
def test_good_href_schemes_kept(href):
    out = sanitize_html('<a href="{0}">x</a>'.format(href))
    assert 'href="' in out


def test_img_src_data_image_allowed_but_not_other_data():
    keep = '<img src="data:image/png;base64,AAAA" />'
    assert sanitize_html('<img src="data:image/png;base64,AAAA">') == keep
    assert sanitize_html('<img src="data:text/html;base64,PHNjcmlwdD4=">') == "<img />"


def test_data_uri_not_allowed_on_a_href():
    out = sanitize_html('<a href="data:image/svg+xml,<svg onload=alert(1)>">x</a>')
    assert out == "<a>x</a>"


def test_relative_img_src_kept():
    assert sanitize_html('<img alt="a" src="figures/plot.png">') == '<img alt="a" src="figures/plot.png" />'


# --- structure / escaping ---

def test_void_elements_no_bogus_close_tag():
    out = sanitize_html("x<br>y<hr>")
    assert out == "x<br />y<hr />"
    assert "</br>" not in out and "</hr>" not in out


def test_text_entities_round_trip_without_double_escaping():
    src = "<p>a &amp; b &lt;c&gt;</p>"
    assert sanitize_html(src) == src


def test_comments_and_decls_dropped():
    out = sanitize_html("<!doctype html><!-- secret --><p>t</p><![CDATA[x]]>")
    assert out == "<p>t</p>"


def test_legit_markdown_document_passes_through_unchanged():
    """Golden-ish: for a document using every construct the pipeline
    supports (headings, emphasis, links, images, aligned tables, fenced
    code, lists, blockquote), sanitize(markdown(...)) must be byte-equal
    to markdown(...) — the sanitizer only removes what markdown never
    legitimately emits."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from jimemo._vendor import add_vendor_to_path

    add_vendor_to_path()
    import markdown

    from jimemo.content import MARKDOWN_EXTENSIONS

    doc = (
        "# Title\n"
        "\n"
        "## Section\n"
        "\n"
        "Some **bold**, *italic*, `inline code`, and a "
        '[link](https://example.com "Site").\n'
        "\n"
        "![alt text](figures/plot.png)\n"
        "\n"
        "> a blockquote\n"
        "\n"
        "- one\n"
        "- two\n"
        "\n"
        "1. first\n"
        "2. second\n"
        "\n"
        "| L | C | R |\n"
        "|:--|:-:|--:|\n"
        "| a | b | c |\n"
        "\n"
        "```\n"
        "code & <stuff>\n"
        "```\n"
    )
    rendered = markdown.markdown(doc, extensions=MARKDOWN_EXTENSIONS)
    assert sanitize_html(rendered) == rendered
    # sanity: the interesting constructs really are present
    assert 'style="text-align: center;"' in rendered
    assert "<pre><code>" in rendered
    assert '<img alt="alt text" src="figures/plot.png" />' in rendered
