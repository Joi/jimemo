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


def test_external_image_warns_not_errors():
    html = '<html><body><img src="https://example.com/a.png"></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert errors == []
    assert any("https://example.com/a.png" in w for w in warnings)


def test_external_link_href_warns():
    html = '<html><head><link rel="stylesheet" href="https://fonts.example/f.css"></head></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert errors == []
    assert any("https://fonts.example/f.css" in w for w in warnings)


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
