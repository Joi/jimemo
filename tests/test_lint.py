import sys
from pathlib import Path

import pytest

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


def test_root_relative_img_src_errors_as_uninlined_local_path():
    # Allowlist semantics (previously passed): a root-relative "/x" has
    # no netloc so it isn't remote, but it IS a surviving path — the
    # page would depend on a sidecar file, so it's not self-contained.
    html = '<html><body><img src="/local/x.png"></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("/local/x.png" in e and "not inlined" in e for e in errors)
    assert not any("external" in e for e in errors)  # not misreported as remote


def test_fragment_and_data_image_fine_bare_img_path_errors():
    # Allowlist semantics (previously all three passed): the fragment
    # and the inlined data:image survive; a bare local <img src> path
    # that inline_images did not localize is a missing dependency.
    html = (
        "<html><body>"
        '<a href="#frag">x</a>'
        '<img src="data:image/png;base64,AAAA">'
        "</body></html>"
    )
    errors, warnings = lint_html(html, {"charts": []})
    assert errors == []

    html = '<html><body><img src="img/x.png"></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("img/x.png" in e and "not inlined" in e for e in errors)


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


def test_local_iframe_src_errors():
    # Allowlist semantics (previously passed): an iframe has no
    # self-contained form at all — a local src is a sidecar dependency.
    html = '<html><body><iframe src="local.html"></iframe></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("local.html" in e and "not inlined" in e for e in errors)


def test_local_video_src_and_source_srcset_error():
    # Allowlist semantics (previously passed): surviving local paths on
    # video src and srcset candidates are sidecar dependencies.
    html = (
        "<html><body><video src=\"local.mp4\">"
        '<source srcset="a.png 1x, b.png 2x">'
        "</video></body></html>"
    )
    errors, warnings = lint_html(html, {"charts": []})
    for path in ("local.mp4", "a.png", "b.png"):
        assert any(path in e and "not inlined" in e for e in errors)


def test_local_object_embed_audio_track_error():
    # Allowlist semantics (previously passed): none of these have a
    # self-contained local form.
    html = (
        "<html><body>"
        '<object data="local.pdf"></object>'
        '<embed src="local.swf">'
        '<audio src="local.mp3"></audio>'
        '<track src="local.vtt">'
        "</body></html>"
    )
    errors, warnings = lint_html(html, {"charts": []})
    for path in ("local.pdf", "local.swf", "local.mp3", "local.vtt"):
        assert any(path in e and "not inlined" in e for e in errors)


def test_bare_local_img_srcset_errors_inlined_srcset_ok():
    # Allowlist semantics (previously the bare form passed): un-inlined
    # srcset candidates error; after inline_images both candidates are
    # data: URIs and the value passes — including the commas inside each
    # data: URI payload, which the srcset parser must keep in-URL.
    html = '<html><body><img src="a.png" srcset="a.png 1x, b.png 2x"></body></html>'
    errors, warnings = lint_html(html, {"charts": []})
    assert any("a.png" in e and "not inlined" in e for e in errors)

    html = (
        "<html><body>"
        '<img src="data:image/png;base64,AAAA" '
        'srcset="data:image/png;base64,AAAA 1x, data:image/png;base64,BBBB 2x">'
        "</body></html>"
    )
    errors, warnings = lint_html(html, {"charts": []})
    assert errors == []


# --- strict allowlist: terminal-class coverage (Fix 4) -------------------
#
# Every fetch-on-load attribute is validated against ONE allowlist:
# a raster data:image URI (image-displaying attributes only) or a pure
# #fragment. The matrices below pin the whole class shut so no further
# per-scheme/per-attribute denylist patches are ever needed.

# Markup shapes for attributes with NO self-contained form: data: URIs
# of any kind are disallowed here, as is everything else but #fragment.
NON_IMAGE_SHAPES = [
    ("iframe-src", '<iframe src="{u}"></iframe>'),
    ("object-data", '<object data="{u}"></object>'),
    ("embed-src", '<embed src="{u}">'),
    ("link-href", '<link rel="stylesheet" href="{u}">'),
    ("video-src", '<video src="{u}"></video>'),
    ("audio-src", '<audio src="{u}"></audio>'),
    ("track-src", '<video><track src="{u}"></video>'),
    ("input-src", '<input type="image" src="{u}">'),
    ("body-background", '<body background="{u}"></body>'),
]

# Markup shapes for image-displaying attributes: an inlined raster
# data:image URI is the one allowed non-fragment form.
IMAGE_SHAPES = [
    ("img-src", '<img src="{u}">'),
    ("img-srcset", '<img srcset="{u} 2x">'),
    ("source-src", '<picture><source src="{u}"><img src="data:image/png;base64,AAAA"></picture>'),
    ("source-srcset", '<video><source srcset="{u} 1x"></video>'),
    ("video-poster", '<video poster="{u}"></video>'),
]

REMOTE_URL = "https://evil.example/x"
PROTO_RELATIVE_URL = "//cdn.example/x"
DATA_TEXT_HTML = "data:text/html,<script>alert(1)</script>"
DATA_SVG = "data:image/svg+xml;base64,PHN2Zz4="
DATA_PNG = "data:image/png;base64,AAAA"

DISALLOWED_EVERYWHERE = [REMOTE_URL, PROTO_RELATIVE_URL, DATA_TEXT_HTML, DATA_SVG]


def _lint(markup):
    return lint_html("<html><body>" + markup + "</body></html>", {"charts": []})


@pytest.mark.parametrize("url", DISALLOWED_EVERYWHERE + [DATA_PNG, "x.bin", ""])
@pytest.mark.parametrize("name, shape", NON_IMAGE_SHAPES, ids=[s[0] for s in NON_IMAGE_SHAPES])
def test_non_image_fetch_attrs_reject_everything_but_fragment(name, shape, url):
    # Note data:image/png is ALSO rejected here: only image-displaying
    # attributes may carry an inlined image.
    errors, _ = _lint(shape.format(u=url))
    assert errors, f"{name} with {url!r} must error"


@pytest.mark.parametrize("name, shape", NON_IMAGE_SHAPES, ids=[s[0] for s in NON_IMAGE_SHAPES])
def test_non_image_fetch_attrs_allow_pure_fragment(name, shape):
    errors, _ = _lint(shape.format(u="#frag"))
    assert errors == []


@pytest.mark.parametrize("url", DISALLOWED_EVERYWHERE + ["img/x.png", ""])
@pytest.mark.parametrize("name, shape", IMAGE_SHAPES, ids=[s[0] for s in IMAGE_SHAPES])
def test_image_fetch_attrs_reject_non_inlined_urls(name, shape, url):
    if url == "" and "srcset" in name:
        pytest.skip("an empty srcset has no candidates and fetches nothing")
    errors, _ = _lint(shape.format(u=url))
    assert errors, f"{name} with {url!r} must error"


@pytest.mark.parametrize("url", [DATA_PNG, "#frag"])
@pytest.mark.parametrize("name, shape", IMAGE_SHAPES, ids=[s[0] for s in IMAGE_SHAPES])
def test_image_fetch_attrs_allow_inlined_data_image_and_fragment(name, shape, url):
    errors, _ = _lint(shape.format(u=url))
    assert errors == []


@pytest.mark.parametrize(
    "url, marker",
    [
        ("file:///etc/passwd", "file"),
        ("ftp://host/x.png", "ftp"),
        ("blob:https://example.com/uuid", "blob"),
    ],
)
def test_other_schemes_rejected_on_fetch_attrs(url, marker):
    errors, _ = _lint(f'<img src="{url}">')
    assert any(marker in e for e in errors)


def test_mixed_srcset_flags_only_the_bad_candidate_kinds():
    errors, _ = _lint(
        '<img src="data:image/png;base64,AAAA" '
        'srcset="data:image/png;base64,AAAA 1x, https://evil.example/x2.png 2x">'
    )
    assert any("https://evil.example/x2.png" in e for e in errors)
    assert not any("base64" in e for e in errors)  # the inlined candidate is fine


def test_link_imagesrcset_candidates_validated():
    # <link rel=preload imagesrcset=...> preloads at parse time; link is
    # not an image-displaying attribute, so even data:image errors.
    errors, _ = _lint(
        '<link rel="preload" as="image" imagesrcset="https://evil.example/x.png 1x">'
    )
    assert any("https://evil.example/x.png" in e for e in errors)
    errors, _ = _lint(
        f'<link rel="preload" as="image" imagesrcset="{DATA_PNG} 1x">'
    )
    assert errors


def test_svg_use_remote_errors_fragment_sprite_ok():
    errors, _ = _lint('<svg><use href="https://evil.example/s.svg#i"/></svg>')
    assert any("evil.example" in e for e in errors)
    # The common same-document sprite reference is a pure fragment.
    errors, _ = _lint('<svg><use href="#icon"/></svg>')
    assert errors == []


def test_image_tag_alias_of_img_is_checked():
    # The HTML parser rewrites <image> to <img>; lint must not let the
    # alias spelling through.
    errors, _ = _lint('<image src="https://evil.example/x.png">')
    assert any("evil.example" in e for e in errors)


def test_data_image_with_embedded_c0_control_rejected():
    # A literal \x01 survives browser URL parsing and demotes the value
    # to a relative-path fetch — the allow side must judge the
    # browser-faithful form, not just the over-normalized one.
    errors, _ = _lint('<img src="da\x01ta:image/png;base64,AAAA">')
    assert errors


def test_charref_to_control_codepoint_rejected_outright():
    # Python's html.unescape silently DROPS &#1; (lint would see a clean
    # data: URI) while a browser keeps the \x01 and fetches a relative
    # path. Parser disagreement itself is the error — fail closed.
    errors, _ = _lint('<img src="da&#1;ta:image/png;base64,AAAA">')
    assert any("character reference" in e for e in errors)
    # Legitimate escaping decodes to a real character and never trips it.
    errors, _ = _lint('<img src="data:image/png;base64,AAAA" alt="it&#39;s">')
    assert errors == []


def test_fragment_lookalike_via_double_entity_rejected():
    # The parser decodes &amp;#35; once, to the literal text "&#35;x" —
    # which a browser fetches as a relative path, not a fragment.
    errors, _ = _lint('<img src="&amp;#35;x">')
    assert errors


def test_empty_img_src_errors():
    errors, _ = _lint('<img src="">')
    assert any("empty" in e for e in errors)


def test_meta_refresh_errors_plain_meta_fine():
    errors, _ = _lint('<meta http-equiv="refresh" content="0;url=https://evil.example/">')
    assert any("refresh" in e for e in errors)
    errors, _ = _lint('<meta charset="utf-8"><meta name="viewport" content="width=device-width">')
    assert errors == []
