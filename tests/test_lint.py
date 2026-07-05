import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.lint import MAX_OUTPUT_BYTES, lint_html


def test_clean_html_has_no_errors_or_warnings():
    html = "<!doctype html><html><body><p>hello</p></body></html>"
    errors, warnings = lint_html(html, {"charts": []})
    assert errors == []
    assert warnings == []


def test_script_tag_errors_when_no_charts_declared():
    html = "<html><body><script>alert(1)</script></body></html>"
    errors, warnings = lint_html(html, {"charts": []})
    assert any("script" in e for e in errors)


def test_script_tag_allowed_when_charts_declared():
    html = "<html><body><script>drawChart();</script></body></html>"
    errors, warnings = lint_html(html, {"charts": ["bar-chart"]})
    assert errors == []


def test_external_script_src_always_errors_even_with_charts():
    html = '<html><body><script src="https://evil.example/x.js"></script></body></html>'
    errors, warnings = lint_html(html, {"charts": ["bar-chart"]})
    assert any("script" in e and "https://evil.example/x.js" in e for e in errors)


def test_external_image_errors():
    # Promoted from warning: a remote <img src> fetches at view time,
    # violating the self-contained-output spec (legit local images were
    # already converted to data: URIs by inline_images).
    html = '<html><body><img src="https://example.com/a.png"></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("https://example.com/a.png" in e for e in errors)


def test_external_link_href_errors():
    html = '<html><head><link rel="stylesheet" href="https://fonts.example/f.css"></head></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("https://fonts.example/f.css" in e for e in errors)


def test_local_script_src_errors_even_with_charts():
    html = '<html><body><script src="chart.js"></script></body></html>'
    errors, warnings = lint_html(html, {"charts": ["bar-chart"]})
    assert any("script" in e and "chart.js" in e for e in errors)


def test_plain_anchor_links_do_not_warn():
    html = '<html><body><a href="https://example.com/page">link</a></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert errors == []
    assert warnings == []


def test_oversized_output_warns():
    html = "<html><body>" + ("x" * (MAX_OUTPUT_BYTES + 1)) + "</body></html>"
    errors, warnings = lint_html(html, {"charts": []})
    assert errors == []
    assert any("bytes" in w for w in warnings)


def test_missing_charts_key_treated_as_empty():
    html = "<html><body><script>alert(1)</script></body></html>"
    errors, warnings = lint_html(html, {})
    assert any("script" in e for e in errors)


def test_inline_event_handler_errors():
    html = '<html><body><div onclick="alert(1)">x</div></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("event handler" in e for e in errors)


def test_javascript_uri_errors():
    html = '<html><body><a href="javascript:alert(1)">x</a></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("javascript" in e for e in errors)


def test_vbscript_uri_errors():
    html = '<html><body><a href="vbscript:msgbox(1)">x</a></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("vbscript" in e for e in errors)


def test_mixed_case_event_handler_errors():
    html = '<html><body><img src="x" OnErRoR="alert(1)"></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("event handler" in e for e in errors)


def test_entity_obfuscated_javascript_uri_errors():
    # &#106; = "j": the scheme check must judge the decoded value, same
    # normalization as the sanitizer's.
    html = '<html><body><a href="&#106;avascript:alert(1)">x</a></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("javascript" in e for e in errors)


def test_javascript_uri_on_formaction_errors():
    html = '<html><body><button formaction="javascript:alert(1)">x</button></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("javascript" in e for e in errors)


def test_escaped_prose_does_not_trigger_lint():
    # The whole point of structure-aware lint: these strings only appear
    # as escaped TEXT, so none of them may fire. The old regex approach
    # false-failed on every one of them.
    html = (
        "<html><body>"
        "<p>phase one = done</p>"
        "<p>never write onclick = x in your markup</p>"
        "<p>javascript: is bad, vbscript: worse</p>"
        "<p>fetch it from https://example.com/a.png later</p>"
        "<pre><code>&lt;script&gt;alert(1)&lt;/script&gt;</code></pre>"
        "</body></html>"
    )
    errors, warnings = lint_html(html, {"charts": []})
    assert errors == []
    assert warnings == []


def test_relative_and_data_and_fragment_urls_are_fine():
    html = (
        "<html><body>"
        '<img src="data:image/png;base64,AAAA">'
        '<a href="#section">jump</a>'
        '<a href="other/page.html">rel</a>'
        "</body></html>"
    )
    errors, warnings = lint_html(html, {"charts": []})
    assert errors == []
    assert warnings == []


# --- data: image URIs on <img src> (Fix 1) ---

def test_svg_data_uri_img_src_errors():
    html = '<html><body><img src="data:image/svg+xml,<svg onload=alert(1)>"></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("data URI" in e for e in errors)


def test_svg_data_uri_base64_img_src_errors():
    html = '<html><body><img src="data:image/svg+xml;base64,PHN2Zz4="></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("data URI" in e for e in errors)


def test_non_image_data_uri_img_src_errors():
    html = '<html><body><img src="data:text/html,<script>alert(1)</script>"></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("data URI" in e for e in errors)


def test_png_data_uri_img_src_ok():
    html = '<html><body><img src="data:image/png;base64,AAAA"></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert errors == []


# --- protocol-relative resource URLs (Fix 2) ---

def test_protocol_relative_img_src_errors():
    html = '<html><body><img src="//host/x.png"></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("//host/x.png" in e for e in errors)


def test_protocol_relative_link_href_errors():
    html = '<html><head><link rel="stylesheet" href="//cdn.example/x.css"></head></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("//cdn.example/x.css" in e for e in errors)


def test_root_relative_img_src_not_flagged_as_protocol_relative():
    # A bare "/x" has no netloc; it's a different (non-fetch) concern that
    # this lint doesn't police. Must not newly error here.
    html = '<html><body><img src="/local/x.png"></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert errors == []


def test_relative_img_src_and_fragment_and_data_still_fine_with_protocol_relative_check():
    html = (
        "<html><body>"
        '<img src="img/x.png">'
        '<a href="#frag">x</a>'
        '<img src="data:image/png;base64,AAAA">'
        "</body></html>"
    )
    errors, warnings = lint_html(html, {"charts": []})
    assert errors == []


# --- fetch-on-load tags beyond img/link (Fix 2) ---

def test_remote_iframe_src_errors():
    html = '<html><body><iframe src="https://evil.example/x"></iframe></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("https://evil.example/x" in e for e in errors)


def test_remote_embed_src_errors():
    html = '<html><body><embed src="https://evil.example/x.swf"></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("https://evil.example/x.swf" in e for e in errors)


def test_remote_object_data_errors():
    html = '<html><body><object data="https://evil.example/x.pdf"></object></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("https://evil.example/x.pdf" in e for e in errors)


def test_remote_video_src_errors():
    html = '<html><body><video src="https://evil.example/x.mp4"></video></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("https://evil.example/x.mp4" in e for e in errors)


def test_remote_audio_src_errors():
    html = '<html><body><audio src="https://evil.example/x.mp3"></audio></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("https://evil.example/x.mp3" in e for e in errors)


def test_remote_track_src_errors():
    html = (
        "<html><body><video>"
        '<track src="https://evil.example/x.vtt">'
        "</video></body></html>"
    )
    errors, warnings = lint_html(html, {"charts": []})
    assert any("https://evil.example/x.vtt" in e for e in errors)


def test_remote_source_src_errors():
    html = (
        "<html><body><video>"
        '<source src="https://evil.example/x.mp4">'
        "</video></body></html>"
    )
    errors, warnings = lint_html(html, {"charts": []})
    assert any("https://evil.example/x.mp4" in e for e in errors)


def test_remote_srcset_candidate_on_img_errors():
    html = (
        "<html><body>"
        '<img src="local.png" srcset="local.png 1x, https://evil.example/x2.png 2x">'
        "</body></html>"
    )
    errors, warnings = lint_html(html, {"charts": []})
    assert any("https://evil.example/x2.png" in e for e in errors)


def test_remote_srcset_candidate_on_source_errors():
    html = (
        "<html><body><video>"
        '<source srcset="local.png 1x, //cdn.example/x2.png 2x">'
        "</video></body></html>"
    )
    errors, warnings = lint_html(html, {"charts": []})
    assert any("//cdn.example/x2.png" in e for e in errors)


def test_local_iframe_src_ok():
    html = '<html><body><iframe src="local.html"></iframe></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert errors == []


def test_local_video_and_source_srcset_ok():
    html = (
        "<html><body><video src=\"local.mp4\">"
        '<source srcset="a.png 1x, b.png 2x">'
        "</video></body></html>"
    )
    errors, warnings = lint_html(html, {"charts": []})
    assert errors == []


def test_local_object_embed_audio_track_ok():
    html = (
        "<html><body>"
        '<object data="local.pdf"></object>'
        '<embed src="local.swf">'
        '<audio src="local.mp3"></audio>'
        '<track src="local.vtt">'
        "</body></html>"
    )
    errors, warnings = lint_html(html, {"charts": []})
    assert errors == []


def test_local_img_srcset_ok():
    html = '<html><body><img src="a.png" srcset="a.png 1x, b.png 2x"></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert errors == []
