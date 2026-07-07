import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.sanitize import (
    is_allowed_data_uri,
    is_allowed_image_data_uri,
    is_protocol_relative,
    parse_srcset,
    sanitize_html,
)


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


def test_fenced_code_language_class_preserved():
    src = '<pre><code class="language-python">x = 1</code></pre>'
    assert sanitize_html(src) == src


def test_non_language_code_class_dropped():
    assert sanitize_html('<code class="hljs">x</code>') == "<code>x</code>"
    assert sanitize_html('<code class="language-py onclick=alert(1)">x</code>') == "<code>x</code>"


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


def test_img_src_data_image_svg_xml_rejected_other_image_subtypes_kept():
    # SVG is the one image subtype that can itself carry markup/script;
    # <img> never executes it, but it's excluded as defense in depth.
    # Other data:image/ subtypes (png, jpeg, ...) are unaffected.
    assert sanitize_html('<img src="data:image/svg+xml,<svg onload=alert(1)>">') == "<img />"
    assert sanitize_html('<img src="data:image/svg+xml;base64,PHN2Zz4=">') == "<img />"
    assert (
        sanitize_html('<img src="data:image/jpeg;base64,AAAA">')
        == '<img src="data:image/jpeg;base64,AAAA" />'
    )


def test_data_uri_not_allowed_on_a_href():
    out = sanitize_html('<a href="data:image/svg+xml,<svg onload=alert(1)>">x</a>')
    assert out == "<a>x</a>"


def test_relative_img_src_kept():
    assert sanitize_html('<img alt="a" src="figures/plot.png">') == '<img alt="a" src="figures/plot.png" />'


# --- is_allowed_image_data_uri (shared helper, Fix 1) ---

@pytest.mark.parametrize(
    "value",
    [
        "data:image/png;base64,AAAA",
        "data:image/jpeg;base64,AAAA",
        "data:image/gif;base64,AAAA",
        "data:image/webp;base64,AAAA",
    ],
)
def test_is_allowed_image_data_uri_true_for_allowed_image_subtypes(value):
    assert is_allowed_image_data_uri(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "data:image/svg+xml,<svg onload=alert(1)>",
        "data:image/svg+xml;base64,PHN2Zz4=",
        "data:text/html,<script>alert(1)</script>",
        "data:text/html;base64,PHNjcmlwdD4=",
        "https://example.com/a.png",
        "",
        # Raster allowlist: non-raster image subtypes are excluded
        # wholesale, not enumerated (bmp/tiff/avif/x-icon/...).
        "data:image/bmp;base64,AAAA",
        "data:image/tiff;base64,AAAA",
        "data:image/avif;base64,AAAA",
        "data:image/x-icon;base64,AAAA",
    ],
)
def test_is_allowed_image_data_uri_false_for_svg_and_non_image(value):
    assert is_allowed_image_data_uri(value) is False


def test_is_allowed_image_data_uri_judges_browser_form_too():
    # \x01 survives browser URL parsing, so the browser treats the value
    # as a relative path and fetches it — the paranoid normalization
    # alone would strip the control char and wrongly bless it.
    assert is_allowed_image_data_uri("da\x01ta:image/png;base64,AAAA") is False
    # A raw entity (already decoded once upstream) is literal text to
    # the browser; the allow side must not double-decode it into data:.
    assert is_allowed_image_data_uri("&#100;ata:image/png;base64,AAAA") is False
    # Tab/newline ARE stripped by browser URL parsing, so these really
    # are data: URIs to the browser and both forms agree.
    assert is_allowed_image_data_uri("da\tta:image/png;base64,AAAA") is True
    assert is_allowed_image_data_uri(" data:image/png;base64,AAAA ") is True


# --- parse_srcset (shared srcset splitter, Fix 4) ---

def test_parse_srcset_simple_candidates():
    assert parse_srcset("a.png 1x, b.png 2x") == [("a.png", "1x"), ("b.png", "2x")]
    assert parse_srcset("a.png") == [("a.png", "")]
    assert parse_srcset("a.png 600w") == [("a.png", "600w")]


def test_parse_srcset_keeps_data_uri_commas_inside_url():
    # The whole reason for spec-style splitting: a data: URI's payload
    # comma must not shear the candidate into a bogus URL + a tail that
    # looks like a bare local path.
    value = "data:image/png;base64,AAAA 1x, b.png 2x"
    assert parse_srcset(value) == [("data:image/png;base64,AAAA", "1x"), ("b.png", "2x")]


def test_parse_srcset_trailing_commas_and_whitespace():
    assert parse_srcset(" a.png , b.png 2x ,") == [("a.png", ""), ("b.png", "2x")]
    assert parse_srcset("a.png,") == [("a.png", "")]
    assert parse_srcset("") == []
    assert parse_srcset(" , ,, ") == []


def test_parse_srcset_commaless_glue_is_one_url():
    # Spec parsing: a comma with no whitespace stays inside the URL run;
    # the browser fetches the glued value as one (broken) URL, so the
    # validator must judge exactly that one URL.
    assert parse_srcset("a.png,b.png") == [("a.png,b.png", "")]


# --- is_protocol_relative (shared helper, Fix 2) ---

@pytest.mark.parametrize(
    "value",
    ["//cdn.example/x.css", "//host", "//HOST/X.PNG"],
)
def test_is_protocol_relative_true(value):
    assert is_protocol_relative(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/x",
        "http://example.com/x",
        "/local/x.png",
        "img/x.png",
        "#frag",
        "",
        "data:image/png;base64,AAAA",
        "///x",
    ],
)
def test_is_protocol_relative_false(value):
    assert is_protocol_relative(value) is False


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

    from jimemo.content import markdown_extensions

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
    rendered = markdown.markdown(doc, extensions=markdown_extensions())
    assert sanitize_html(rendered) == rendered
    # sanity: the interesting constructs really are present
    assert 'style="text-align: center;"' in rendered
    assert "<pre><code>" in rendered
    assert '<img alt="alt text" src="figures/plot.png" />' in rendered


# --- is_allowed_data_uri: legacy application/font-* subtype allowlist ---
#
# Regression: `_is_font_data_uri` used to accept ANY subtype after the
# "application/font-"/"application/x-font-" prefix (a bare
# `mime.startswith(_LEGACY_FONT_MIME_PREFIXES)`), so e.g.
# "application/font-evil" passed as a "font". Tightened to require the
# subtype after the prefix be one of the real font formats.


@pytest.mark.parametrize(
    "mime",
    [
        "application/font-ttf",
        "application/font-otf",
        "application/font-woff",
        "application/font-woff2",
        "application/x-font-ttf",
        "application/x-font-otf",
        "application/x-font-woff",
        "application/x-font-woff2",
    ],
)
def test_legacy_font_mime_prefix_with_real_subtype_accepted(mime):
    assert is_allowed_data_uri("data:{};base64,AAAA".format(mime)) is True


@pytest.mark.parametrize(
    "mime",
    [
        "application/font-evil",
        "application/font-",
        "application/x-font-evil",
        "application/x-font-",
        "application/font",
        "application/x-font",
    ],
)
def test_legacy_font_mime_prefix_with_bogus_subtype_rejected(mime):
    assert is_allowed_data_uri("data:{};base64,AAAA".format(mime)) is False


@pytest.mark.parametrize("mime", ["font/ttf", "font/otf", "font/woff", "font/woff2"])
def test_modern_font_mime_still_accepted(mime):
    assert is_allowed_data_uri("data:{};base64,AAAA".format(mime)) is True


def test_image_data_uri_still_accepted_by_is_allowed_data_uri():
    assert is_allowed_data_uri("data:image/png;base64,AAAA") is True


def test_is_allowed_image_data_uri_unaffected_by_font_allowlist_tightening():
    # is_allowed_image_data_uri must stay raster-only: neither the old
    # nor the new font allowlist should leak into it.
    assert is_allowed_image_data_uri("data:application/font-woff;base64,AAAA") is False
    assert is_allowed_image_data_uri("data:font/woff;base64,AAAA") is False
    assert is_allowed_image_data_uri("data:image/png;base64,AAAA") is True


def test_non_font_non_image_mime_rejected_by_is_allowed_data_uri():
    assert is_allowed_data_uri("data:application/octet-stream;base64,AAAA") is False
