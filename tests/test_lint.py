import collections
import sys
from html.entities import html5 as html5_entities
from html.parser import HTMLParser
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo import PYTHON_FLOOR, lint
from jimemo._paths import CHARTJS_BUNDLE
from jimemo.charts import chart_lib_inline_text
from jimemo.lint import MAX_OUTPUT_BYTES, lint_html, lint_standalone


def _faked_version_info(major, minor, micro):
    version_info = collections.namedtuple(
        "version_info", "major minor micro releaselevel serial"
    )
    return version_info(major, minor, micro, "final", 0)


def test_clean_html_has_no_errors_or_warnings():
    html = "<!doctype html><html><body><p>hello</p></body></html>"
    errors, warnings = lint_html(html, {"charts": []})
    assert errors == []
    assert warnings == []


def test_script_tag_errors_when_no_charts_declared():
    html = "<html><body><script>alert(1)</script></body></html>"
    errors, warnings = lint_html(html, {"charts": []})
    assert any("script" in e for e in errors)


def test_renderer_shaped_init_script_allowed_when_charts_declared():
    # An inline script whose body is a chart init in the exact shape the
    # renderer emits, for a declared chart id, passes.
    html = (
        "<html><body>"
        '<script>new Chart(document.getElementById("bar-chart"), {});</script>'
        "</body></html>"
    )
    errors, warnings = lint_html(html, {"charts": ["bar-chart"]})
    assert errors == []


def test_arbitrary_inline_script_rejected_even_when_charts_declared():
    # The Phase 4 tightening: declaring charts no longer blesses ANY
    # inline script — only the renderer-emitted library and chart inits.
    html = "<html><body><script>drawChart();</script></body></html>"
    errors, warnings = lint_html(html, {"charts": ["bar-chart"]})
    assert any("unexpected inline" in e for e in errors)


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


def test_valueless_script_src_errors_even_with_charts():
    # Python's html.parser reports a bare attribute like `src` (no
    # `=value`) as ("src", None) -- identical to "no src attribute at
    # all". A browser that sees src present ignores the element's
    # inline body and fetches src instead, so this must still be
    # rejected as a src-bearing script, not treated as (and possibly
    # allowlisted as) an inline chart init.
    html = (
        "<html><body>"
        '<script src>new Chart(document.getElementById("bar-chart"), {});</script>'
        "</body></html>"
    )
    errors, warnings = lint_html(html, {"charts": ["bar-chart"]})
    assert any("script" in e and "never allowed" in e for e in errors)


def test_empty_script_src_errors_even_with_charts():
    html = (
        "<html><body>"
        '<script src="">new Chart(document.getElementById("bar-chart"), {});</script>'
        "</body></html>"
    )
    errors, warnings = lint_html(html, {"charts": ["bar-chart"]})
    assert any("script" in e and "never allowed" in e for e in errors)


def test_uppercase_script_src_attr_errors_even_with_charts():
    html = (
        "<html><body>"
        '<script SRC>new Chart(document.getElementById("bar-chart"), {});</script>'
        "</body></html>"
    )
    errors, warnings = lint_html(html, {"charts": ["bar-chart"]})
    assert any("script" in e and "never allowed" in e for e in errors)


def test_script_src_with_value_still_errors_on_chart_page():
    # Regression guard alongside the presence-only cases above: a
    # normal valued src on a chart page must still be rejected, not
    # accidentally waved through by the presence-check refactor.
    html = '<html><body><script src="x"></script></body></html>'
    errors, warnings = lint_html(html, {"charts": ["bar-chart"]})
    assert any("script" in e and "never allowed" in e for e in errors)


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
    # <a href> is a click-time navigation attribute, not a fetch-on-load
    # one — #fragment and relative hrefs there never fetch and stay fine
    # even though the same values error on img/link/video (see below).
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


def test_fragment_on_resource_attrs_errors_but_anchor_href_is_fine():
    # roborev finding: a pure #fragment on a fetch-on-load RESOURCE
    # attribute still makes the browser attempt a same-document resource
    # load, so it must error there — unlike <a href="#section">, which
    # only navigates on click and is not a fetch-on-load attribute.
    for markup in (
        '<img src="#x">',
        '<link rel="stylesheet" href="#x">',
        '<video src="#x"></video>',
    ):
        errors, _ = _lint(markup)
        assert errors, f"{markup!r} must error"
        assert any("#fragment" in e for e in errors), errors

    errors, _ = _lint('<a href="#section">jump</a>')
    assert errors == []


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


def test_local_audio_and_track_error():
    # Allowlist semantics (previously passed): neither has a
    # self-contained local form. (object/embed, formerly tested here,
    # are now rejected as tags outright — see the banned-tags section.)
    html = (
        "<html><body>"
        '<audio src="local.mp3"></audio>'
        '<track src="local.vtt">'
        "</body></html>"
    )
    errors, warnings = lint_html(html, {"charts": []})
    for path in ("local.mp3", "local.vtt"):
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
# Every fetch-on-load attribute is validated against ONE allowlist: a
# raster data:image URI, and only on image-displaying attributes. A pure
# #fragment is NOT in the allowlist for any fetch-on-load attribute — it
# still makes the browser attempt a same-document resource load there,
# unlike <a href="#...">, which only navigates on click and is not a
# fetch-on-load attribute at all (see test_relative_and_data_and_fragment_urls_are_fine
# and test_fragment_and_data_image_fine_bare_img_path_errors). The
# matrices below pin the whole class shut so no further
# per-scheme/per-attribute denylist patches are ever needed.

# Markup shapes for attributes with NO self-contained form: data: URIs
# of any kind are disallowed here, and so is everything else, including
# #fragment. (iframe/object/embed, formerly in this matrix, are banned
# as tags now and covered by the banned-tags section — even #fragment
# errors there.)
NON_IMAGE_SHAPES = [
    ("link-href", '<link rel="stylesheet" href="{u}">'),
    ("video-src", '<video src="{u}"></video>'),
    ("audio-src", '<audio src="{u}"></audio>'),
    ("track-src", '<video><track src="{u}"></video>'),
    ("input-src", '<input type="image" src="{u}">'),
    ("body-background", '<body background="{u}"></body>'),
]

# Markup shapes for image-displaying attributes: an inlined raster
# data:image URI is the one allowed form — #fragment is rejected here
# too.
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


@pytest.mark.parametrize("url", DISALLOWED_EVERYWHERE + [DATA_PNG, "#frag", "x.bin", ""])
@pytest.mark.parametrize("name, shape", NON_IMAGE_SHAPES, ids=[s[0] for s in NON_IMAGE_SHAPES])
def test_non_image_fetch_attrs_reject_everything(name, shape, url):
    # Note data:image/png is ALSO rejected here: only image-displaying
    # attributes may carry an inlined image. #frag is rejected too: it
    # has no self-contained fetch-on-load form.
    errors, _ = _lint(shape.format(u=url))
    assert errors, f"{name} with {url!r} must error"


@pytest.mark.parametrize("name, shape", NON_IMAGE_SHAPES, ids=[s[0] for s in NON_IMAGE_SHAPES])
def test_non_image_fetch_attrs_reject_pure_fragment(name, shape):
    # A #fragment still triggers a same-document resource load on a
    # fetch-on-load attribute — it is not a legal form here even though
    # it never fetches on <a href> (which isn't a fetch-on-load attribute
    # at all).
    errors, _ = _lint(shape.format(u="#frag"))
    assert any("#fragment" in e for e in errors), f"{name} must error: {errors!r}"


@pytest.mark.parametrize("url", DISALLOWED_EVERYWHERE + ["img/x.png", ""])
@pytest.mark.parametrize("name, shape", IMAGE_SHAPES, ids=[s[0] for s in IMAGE_SHAPES])
def test_image_fetch_attrs_reject_non_inlined_urls(name, shape, url):
    if url == "" and "srcset" in name:
        pytest.skip("an empty srcset has no candidates and fetches nothing")
    errors, _ = _lint(shape.format(u=url))
    assert errors, f"{name} with {url!r} must error"


@pytest.mark.parametrize("name, shape", IMAGE_SHAPES, ids=[s[0] for s in IMAGE_SHAPES])
def test_image_fetch_attrs_allow_inlined_data_image(name, shape):
    errors, _ = _lint(shape.format(u=DATA_PNG))
    assert errors == []


@pytest.mark.parametrize("name, shape", IMAGE_SHAPES, ids=[s[0] for s in IMAGE_SHAPES])
def test_image_fetch_attrs_reject_pure_fragment(name, shape):
    # Even on an image-displaying attribute, #fragment is not the same
    # as an inlined data:image — it still triggers a same-document
    # resource load.
    errors, _ = _lint(shape.format(u="#frag"))
    assert any("#fragment" in e for e in errors), f"{name} must error: {errors!r}"


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


def test_svg_use_remote_and_fragment_sprite_both_error():
    errors, _ = _lint('<svg><use href="https://evil.example/s.svg#i"/></svg>')
    assert any("evil.example" in e for e in errors)
    # <use> is a fetch-on-load attribute (jimemo never emits it, but the
    # allowlist covers it defensively); a same-document sprite reference
    # via #fragment still errors here — <use> is not <a href>.
    errors, _ = _lint('<svg><use href="#icon"/></svg>')
    assert any("#fragment" in e for e in errors)


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


def test_base_href_errors():
    # <base href> re-roots every relative/#fragment URL on the page
    # against a remote origin, defeating the self-contained allowlist.
    errors, _ = _lint('<base href="https://evil.example/">')
    assert any("base href" in e for e in errors)


def test_base_target_without_href_is_fine():
    errors, _ = _lint('<base target="_blank">')
    assert errors == []


def test_no_base_tag_is_fine():
    errors, warnings = lint_html(
        "<!doctype html><html><body><p>hello</p></body></html>", {"charts": []}
    )
    assert errors == []
    assert warnings == []


# --- banned tags: any occurrence errors (Fix 6) ---------------------------
#
# These tags have no legitimate use in a self-contained static page and
# each is an embed/exec/fetch vector per-attribute checks can't fully
# cover — <iframe srcdoc> executes script with no src attribute at all —
# so the tag itself is rejected, attributes unexamined.

@pytest.mark.parametrize(
    "markup, tag",
    [
        ('<iframe srcdoc="<script>alert(1)</script>"></iframe>', "iframe"),
        ('<iframe src="#frag"></iframe>', "iframe"),  # even a fragment src
        ("<iframe></iframe>", "iframe"),
        ('<frame src="a.html">', "frame"),
        ("<frameset></frameset>", "frameset"),
        ('<object data="https://evil.example/x.pdf"></object>', "object"),
        ('<object data="local.pdf"></object>', "object"),
        ('<embed src="https://evil.example/x.swf">', "embed"),
        ("<embed>", "embed"),
        ('<applet code="Evil.class"></applet>', "applet"),
        ('<portal src="https://evil.example/"></portal>', "portal"),
        ('<form action="https://evil.example/collect"><input name="q"></form>', "form"),
        ("<form></form>", "form"),  # even action-less: submits to the page URL
    ],
)
def test_banned_tags_error_on_any_occurrence(markup, tag):
    errors, _ = _lint(markup)
    assert any(f"<{tag}>" in e and "never allowed" in e for e in errors), (
        f"{markup!r} must produce a banned-tag error for <{tag}>"
    )


def test_meta_content_type_and_color_scheme_are_fine():
    # Only http-equiv="refresh" is rejected on <meta>; the ordinary
    # charset/name/content-type forms are inert.
    errors, _ = _lint(
        '<meta http-equiv="content-type" content="text/html; charset=utf-8">'
        '<meta name="color-scheme" content="light dark">'
    )
    assert errors == []


def test_meta_refresh_case_insensitive():
    errors, _ = _lint('<meta HTTP-EQUIV="ReFrEsH" content="0;url=https://evil.example/">')
    assert any("refresh" in e for e in errors)


# --- CSS references: url() and @import (Fix 6) -----------------------------
#
# CSS fetches on its own; <style> text and style="..." attributes are
# scanned against the same allowlist as fetch-on-load attributes.

def test_style_import_url_form_errors():
    errors, _ = _lint("<style>@import url(https://evil.example/x.css);</style>")
    assert any("@import" in e for e in errors)


def test_style_import_string_forms_error():
    for rule in (
        '@import "https://evil.example/x.css";',
        "@import 'https://evil.example/x.css';",
        '@import "local.css";',  # even a local sheet is a sidecar fetch
    ):
        errors, _ = _lint(f"<style>{rule}</style>")
        assert any("@import" in e for e in errors), rule


def test_style_remote_url_errors():
    errors, _ = _lint("<style>.x{background:url(https://evil.example/x.png)}</style>")
    assert any("evil.example" in e and "<style>" in e for e in errors)


def test_style_url_quoting_and_whitespace_variants_error():
    for ref in (
        'url("https://evil.example/x.png")',
        "url('https://evil.example/x.png')",
        'url(  "https://evil.example/x.png"  )',
        "url(//cdn.example/x.png)",
    ):
        errors, _ = _lint("<style>.x{background:%s}</style>" % ref)
        assert any("remote" in e for e in errors), ref


def test_style_attribute_remote_url_errors():
    errors, _ = _lint('<div style="background:url(//evil.example/x.png)">x</div>')
    assert any("style attribute" in e and "<div>" in e for e in errors)


def test_style_attribute_entity_encoded_url_errors():
    # html.parser decodes charrefs in attribute values, same as the
    # browser; the scan judges the decoded value.
    errors, _ = _lint('<div style="background:url(&#104;ttps://evil.example/x)">x</div>')
    assert any("evil.example" in e for e in errors)


def test_style_local_path_url_errors():
    errors, _ = _lint("<style>.x{background:url(img/x.png)}</style>")
    assert any("img/x.png" in e and "sidecar" in e for e in errors)


def test_style_disallowed_data_uri_errors():
    errors, _ = _lint(
        '<style>.x{background:url("data:image/svg+xml;base64,PHN2Zz4=")}</style>'
    )
    assert any("data: URI" in e for e in errors)


def test_style_data_image_and_fragment_urls_are_fine():
    errors, _ = _lint(
        "<style>"
        ".x{background:url(data:image/png;base64,AAAA)}"
        ".y{fill:url(#grad)}"
        "</style>"
    )
    assert errors == []


@pytest.mark.parametrize("mime", ["font/ttf", "font/otf", "font/woff", "font/woff2"])
def test_style_font_face_data_uri_is_fine(mime):
    # An embedded design-theme font (jimemo.design.importer's
    # --embed-fonts) appends exactly this shape: @font-face { src:
    # url(data:font/...;base64,...) }.
    errors, _ = _lint(
        "<style>@font-face{{font-family:\"X\";"
        "src:url(data:{mime};base64,AAAA)}}</style>".format(mime=mime)
    )
    assert errors == []


def test_style_non_font_non_image_data_uri_still_errors():
    errors, _ = _lint(
        '<style>@font-face{font-family:"X";'
        'src:url(data:application/octet-stream;base64,AAAA)}</style>'
    )
    assert any("data: URI" in e for e in errors)


def test_style_without_references_is_fine():
    # Shaped like the real toolkit CSS: comments, custom properties,
    # a content escape — and no url()/@import.
    errors, _ = _lint(
        "<style>\n"
        "/* tokens */\n"
        ":root { --ink: #1a1a1a; --paper: #ffffff; }\n"
        "body { font: 16px/1.6 system-ui, sans-serif; color: var(--ink); }\n"
        'nav li + li::before { content: "\\00B7"; }\n'
        "@media print { body { color: #000; } }\n"
        "</style>"
    )
    assert errors == []


def test_style_attribute_text_align_is_fine():
    # The one inline style the sanitizer lets through (table column
    # alignment) must keep passing.
    errors, _ = _lint('<td style="text-align:center;">x</td>')
    assert errors == []


def test_style_comment_obfuscated_url_errors():
    # Comments are stripped before scanning, so a /**/ split cannot
    # hide the construct.
    errors, _ = _lint("<style>.x{background:url/**/(https://evil.example/x)}</style>")
    assert any("evil.example" in e for e in errors)


def test_style_escape_obfuscated_url_and_import_error():
    # A CSS-escape-decoded copy is scanned too: \75 is "u", \69 is "i".
    errors, _ = _lint("<style>.x{background:\\75rl(https://evil.example/x)}</style>")
    assert any("evil.example" in e for e in errors)
    errors, _ = _lint('<style>@\\69mport "https://evil.example/x.css";</style>')
    assert any("@import" in e for e in errors)


# --- comment stripping must match a browser's (jimemo#y9p8) -----------------
#
# lint used to delete /*...*/ with a regex before scanning. A browser only
# treats /* as a comment opener in SOME of the places it appears, so the
# strip could delete a fetching url() from the scanned text while the
# browser still fetched it at view time. One test per bypass; each bypass
# test fails on the pre-fix code, most of them because it returned
# ([], []) (the escaped-marker test is a guard instead: that form was
# caught before and must stay caught).
# REMOTE is the fetch each vector smuggles past the allowlist; the
# comment names why the /* is or is not a comment there.

REMOTE = "https://evil.example/p"


def test_style_comment_inside_url_token_hides_remote():
    # Inside an unquoted url( token /* is URL text and the first ) ends
    # the token, so the browser reads url(/*) and then a SECOND
    # declaration that fetches; the regex strip left only url(#g).
    errors, _ = _lint(
        "<style>.x{background:url(/*);background:url(%s);/*x*/#g)}</style>" % REMOTE
    )
    assert any("evil.example" in e for e in errors)


def test_style_comment_only_url_is_an_external_path():
    # url(/**/#g) is not a fragment: the target is the path /**/#g.
    errors, _ = _lint("<style>.x{background:url(/**/#g)}</style>")
    assert any("local path" in e for e in errors)


def test_style_comment_marker_inside_string_hides_remote():
    # /* inside a string is string text, so the browser sees no comment
    # at all; the regex deleted from the first marker to the last.
    errors, _ = _lint(
        '<style>.x{content:"/*";background:url(%s);z:"*/"}</style>' % REMOTE
    )
    assert any("evil.example" in e for e in errors)


@pytest.mark.parametrize(
    "spelling",
    [r"u\72l", r"\75rl", "u\\72 l"],   # the third: the hex escape eats the space
)
def test_style_escaped_url_ident_with_comment_hides_remote(spelling):
    # Escapes are decoded BEFORE tokenizing, so this IS a url token —
    # which is why "is this /* inside a url token?" must be decided on
    # the unescaped ident, not on the literal text.
    errors, _ = _lint(
        "<style>.x{background:%s(/*);background:url(%s);/*x*/#g)}</style>"
        % (spelling, REMOTE)
    )
    assert any("evil.example" in e for e in errors)


def test_style_escaped_slash_is_not_a_comment_opener():
    # \/ is an escaped code point inside an ident; it opens nothing.
    errors, _ = _lint(
        "<style>.x{--marker:\\/*;background:url(%s);/*x*/}</style>" % REMOTE
    )
    assert any("evil.example" in e for e in errors)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_style_hex_escape_consumes_the_newline_after_it(newline):
    # A hex escape eats one following whitespace character, so the string
    # continues through the /* — and CSS folds CRLF to one newline first,
    # so both spellings behave identically to a browser.
    errors, _ = _lint(
        '<style>.x{--label:"\\22%s/*";background:url(%s);--tail:"*/"}</style>'
        % (newline, REMOTE)
    )
    assert any("evil.example" in e for e in errors)


def test_style_form_feed_ends_a_string_like_a_newline():
    # FF is folded to a newline by CSS input preprocessing, so the first
    # string is a bad-string ending there and "/*" is an ordinary string.
    errors, _ = _lint(
        '<style>.x{--x:"\f"/*";background:url(%s);--tail:"*/"}</style>' % REMOTE
    )
    assert any("evil.example" in e for e in errors)


def test_style_backslash_newline_continues_a_string():
    # The same pair means the opposite INSIDE a string: a line
    # continuation, so the string goes on through the /* and ends at
    # the next quote.
    errors, _ = _lint(
        '<style>.x{--x:"\\\n/*";background:url(%s);--tail:"*/"}</style>' % REMOTE
    )
    assert any("evil.example" in e for e in errors)


def test_style_backslash_newline_is_not_an_ident_escape():
    # Backslash + newline is not a valid escape outside a string, so the
    # ident ends at the backslash and a real url token follows it.
    errors, _ = _lint(
        "<style>.x{--x:foo\\\nurl(/*);background:url(%s);/*x*/#g)}</style>" % REMOTE
    )
    assert any("evil.example" in e for e in errors)


def test_style_cdo_token_does_not_extend_the_next_ident():
    # <!-- is a single CDO token, so the url after it starts a url token
    # rather than continuing an ident named --url.
    errors, _ = _lint(
        "<style>.x{--x:<!--url(/*);background:url(%s);/*x*/#g)}</style>" % REMOTE
    )
    assert any("evil.example" in e for e in errors)


@pytest.mark.parametrize("name", ["#url", "@url", r"#\75rl"])
def test_style_hash_or_at_keyword_url_is_not_a_url_token(name):
    # #url is a hash token and @url an at-keyword, so the ( after either
    # opens an ordinary block in which /* IS a comment. Reading a url
    # token there instead puts every later string and comment boundary
    # out of step with the browser's, until a live declaration is
    # deleted as a "comment". (Found by the code review of this fix: the
    # pre-fix regex happened to reject this input as an unparseable url(
    # construct; the scanner's first version let it through.)
    css = '.x{--x:%s(#g/*)"*/"/*");background:url(%s);/*x*/}' % (name, REMOTE)
    errors, _ = _lint("<style>%s</style>" % css)
    assert any("evil.example" in e for e in errors)
    attr = css[3:-1].replace('"', "&quot;")
    errors, _ = _lint('<p style="%s">x</p>' % attr)
    assert any("evil.example" in e and "style attribute" in e for e in errors)


@pytest.mark.parametrize("name", ["×url", "5url", "foourl", "u+1url"])
def test_style_name_ending_in_url_stops_comment_stripping(name):
    # Whether url( after these starts a url token depends on the engine
    # or the spec revision (the current CSS Syntax draft narrows which
    # non-ASCII code points are ident code points; unicode-range contexts
    # tokenize u+1 separately). The scan does not guess: it stops
    # removing comments there, so an engine that DOES read a url token
    # cannot have the declaration after it deleted.
    errors, _ = _lint(
        "<style>.x{a:%s(/*);background:url(%s);/*x*/#g)}</style>" % (name, REMOTE)
    )
    assert any("evil.example" in e for e in errors)


def test_style_escaped_comment_markers_are_not_comments():
    # The mirror image: \2f\2a decodes to /* but a browser never reads an
    # escape as a comment delimiter, so the declaration between these
    # stays live and must keep being scanned.
    errors, _ = _lint(
        "<style>.x{a:\\2f\\2a;background:url(%s);b:\\2a\\2f}</style>" % REMOTE
    )
    assert any("evil.example" in e for e in errors)


def test_style_escaped_paren_inside_url_token_errors():
    # A backslash-escaped ) does not end the url token, so the target is
    # the path /*\)*/#g rather than the fragment the strip left behind.
    errors, _ = _lint("<style>.x{background:url(/*\\)*/#g)}</style>")
    assert any("local path" in e for e in errors)


def test_style_comment_joined_import_errors():
    # Deleting the comment joins the tokens either side of it, so this
    # arrives as @importurl(#g) — which @import\b did not match.
    errors, _ = _lint("<style>@import/**/url(#g);</style>")
    assert any("@import" in e for e in errors)


def test_style_attribute_carries_the_same_scan():
    # The style ATTRIBUTE path is the one the issue reproduced on, and
    # html.parser decodes entity-encoded CR/LF in an attribute value
    # before lint sees it — a browser's newline folding then keeps the
    # string open exactly as it does for a literal CRLF.
    errors, _ = _lint(
        '<p style="background:url(/*);background:url(%s);/*x*/#g)">x</p>' % REMOTE
    )
    assert any("evil.example" in e and "style attribute" in e for e in errors)
    errors, _ = _lint(
        '<div style="--label:&quot;\\22&#13;&#10;/*&quot;;'
        'background:url(%s);--tail:&quot;*/&quot;">x</div>' % REMOTE
    )
    assert any("evil.example" in e and "style attribute" in e for e in errors)


# --- the Python floor is what retired jimemo#y9p8's old-parser guard ------
#
# In an attribute, a browser keeps a legacy named reference literal when no
# ';' follows and the next character is a letter, digit or '='. html.parser
# below CPython 3.13.4 decodes it anyway. In a style attribute that desyncs
# the CSS comment scan: &quot adds a quote the browser never sees, and every
# one of these removes the reference NAME, which the browser reads as ident
# code points glued to what follows (&gturl( is the function gturl( to a
# browser, a url token to the scan). Each payload below returned ([], [])
# on 3.9 and 3.10, which is why y9p8 added a fail-closed guard.
#
# jimemo#gaga raised the floor to 3.13.6 and deleted that guard. So these
# tests no longer force an answer with monkeypatch -- they measure the real
# parser, and assert the payloads are read the way a browser reads them:
# the reference stays literal, so the live url() the payload was built to
# hide is plainly visible to the scan and IS reported.
QUOT_PAYLOAD = "<p style='a:%s \"/*\";background:url(%s);z:\"*/\"'>x</p>"
NAME_PAYLOAD = (
    "<p style='a:%surl(#a/*)\"*/ ); x:\"/*\"; background:url(%s); z:\"*/\"'>x</p>"
)
LEGACY_REF_CASES = [
    (QUOT_PAYLOAD, "&quotx"),
    (QUOT_PAYLOAD, "&QUOT1"),
    (QUOT_PAYLOAD, "&quot="),
] + [(NAME_PAYLOAD, ref) for ref in ("&gt", "&GT", "&lt", "&LT", "&amp", "&AMP")]


@pytest.mark.parametrize("payload, ref", LEGACY_REF_CASES)
def test_legacy_reference_payloads_are_read_as_the_browser_reads_them(payload, ref):
    markup = payload % (ref, REMOTE)
    # 1. the parser keeps the reference exactly as written ...
    assert ref in _first_style_value(markup)
    # 2. ... so the remote url() is reported, not hidden behind a phantom
    #    comment, and no old-parser guard error is needed to catch it.
    errors, _ = _lint(markup)
    assert any("evil.example" in e and "style attribute" in e for e in errors), errors
    assert not any("without ';'" in e for e in errors), errors


def _first_style_value(markup):
    """The style attribute value html.parser hands the linter for the
    first start tag in `markup`."""

    class Capture(HTMLParser):
        value = None

        def handle_starttag(self, tag, attrs):
            if self.value is None:
                for name, value in attrs:
                    if name.lower() == "style":
                        self.value = value or ""
                        return

    capture = Capture(convert_charrefs=True)
    capture.feed(markup)
    capture.close()
    assert capture.value is not None, markup
    return capture.value


# Every legacy (semicolonless) name in the HTML5 entity table, not just the
# four that decode to ASCII: this is the whole input class y9p8's guard
# covered, measured against the real parser rather than assumed from a
# version number. Measured 2026-09-19: 0 of 318 kept literal on 3.9.6,
# 3.10.21, 3.12.11, 3.13.0 and 3.13.3; all 318 kept on 3.13.4, 3.13.6,
# 3.13.15, 3.14.0 and 3.14.7. If this test ever fails, the floor has been
# undercut and the y9p8 guard has to come back -- it is the canary for the
# deletion, so do not weaken it to a sample.
LEGACY_ENTITY_NAMES = sorted(
    name for name in html5_entities if not name.endswith(";")
)


def test_floor_parser_keeps_every_legacy_reference_literal_in_attribute():
    assert len(LEGACY_ENTITY_NAMES) > 100, LEGACY_ENTITY_NAMES
    mismatches = []
    for name in LEGACY_ENTITY_NAMES:
        for following in ("x", "1", "="):
            raw = "&" + name + following
            got = _first_style_value("<p style='%s'>x</p>" % raw)
            if got != raw:
                mismatches.append((raw, got))
    assert mismatches == [], mismatches[:10]


def test_floor_parser_still_decodes_terminated_references():
    # The flip side: a reference WITH its ';' decodes on every version, so
    # legitimate escaping is untouched by the floor.
    assert _first_style_value("<p style='&quot;'>x</p>") == '"'
    assert _first_style_value("<p style='&amp;'>x</p>") == "&"


# --- the import-time floor assertion -------------------------------------


def test_parser_faithfulness_assertion_passes_on_this_interpreter():
    lint._browser_faithfulness_problem.cache_clear()
    lint._assert_parser_is_browser_faithful()


@pytest.mark.parametrize("probe_name", [name for name, _ in lint._PARSER_PROBES])
def test_lint_refuses_a_parser_that_fails_any_probe(probe_name, monkeypatch):
    # Each probe guards one browser disagreement the floor exists to rule
    # out; failing any of them means lint would judge different markup
    # than the browser renders, so the module refuses to provide lint at
    # all rather than answer a weaker question (jimemo#gaga).
    patched = tuple(
        (name, (lambda: False) if name == probe_name else probe)
        for name, probe in lint._PARSER_PROBES
    )
    monkeypatch.setattr(lint, "_PARSER_PROBES", patched)
    lint._browser_faithfulness_problem.cache_clear()
    try:
        with pytest.raises(RuntimeError) as excinfo:
            lint._assert_parser_is_browser_faithful()
    finally:
        lint._browser_faithfulness_problem.cache_clear()
    message = str(excinfo.value)
    assert repr(probe_name) in message, message
    assert ".".join(str(part) for part in PYTHON_FLOOR) in message, message


def test_lint_refuses_an_interpreter_below_the_floor(monkeypatch):
    # The numeric floor is checked too, not only the probes: 3.13.4 and
    # 3.13.5 pass the 'refs' and 'style' probes but split attributes the
    # way a browser does not, and a backport could produce any other
    # combination. Below the floor, lint does not run.
    faked = _faked_version_info(3, 13, 5)
    monkeypatch.setattr(lint.sys, "version_info", faked)
    lint._browser_faithfulness_problem.cache_clear()
    try:
        with pytest.raises(RuntimeError) as excinfo:
            lint._assert_parser_is_browser_faithful()
    finally:
        lint._browser_faithfulness_problem.cache_clear()
    message = str(excinfo.value)
    assert "3.13.5" in message, message
    assert ".".join(str(part) for part in PYTHON_FLOOR) in message, message


# --- the rest of the y9p8 vectors, with the guard gone -------------------


def test_style_attribute_reference_in_another_attribute_is_fine():
    # A query string in an href never could move a CSS boundary, and on
    # the floor it is not decoded either.
    errors, _ = _lint(
        '<a href="https://example.com/?base=EUR&quote=USD&gte=5&amplitude=3" '
        'style="color:red">rate</a>'
    )
    assert errors == []


def test_style_attribute_reference_next_to_another_attribute_is_still_caught():
    # The same reference INSIDE the style value: on the floor it stays
    # literal, so the live background:url() is reported by the ordinary
    # scan, whatever attributes sit around it.
    errors, _ = _lint(
        '<a href="https://example.com/?a=1&amp;b=2" title="x" '
        "style='a:&gturl(#a/*)\"*/ ); x:\"/*\"; background:url(%s); z:\"*/\"'>"
        "x</a>" % REMOTE
    )
    assert any("evil.example" in e for e in errors), errors


def test_repeated_style_attributes_stay_linear():
    # y9p8's guard judged the raw tag once per style attribute, which made
    # 40 000 duplicates take ~4 s (quadratic). The guard is gone, but the
    # per-tag cost is still worth pinning: a 100 000-attribute tag must
    # stay well under a second.
    import time
    markup = "<p" + " style=x" * 100_000 + ">x</p>"
    started = time.monotonic()
    _lint(markup)
    assert time.monotonic() - started < 5.0


def test_style_attribute_terminated_references_are_fine():
    # With the ';' present every parser decodes the same way.
    errors, _ = _lint(
        '<p style="font-family:&quot;Iowan&quot;,serif;content:&quot;a&amp;b&quot;">x</p>'
    )
    assert errors == []
    # Longer complete references that merely START with a legacy name
    # (&ltri; is a triangle, not &lt + "ri;") decode identically in every
    # parser; y9p8's first attempt at the guard rejected them on 3.9.
    for ref in ("&ltri;", "&gtrsim;", "&ltimes;", "&amp;"):
        errors, _ = _lint("<p style=\"--marker:'%s';color:red\">x</p>" % ref)
        assert errors == [], (ref, errors)


def test_style_attribute_legacy_prefix_of_unknown_run_is_literal_on_the_floor():
    # A run that is NOT a complete reference fell back to its legacy
    # prefix in the old decoder, which a browser does not do; y9p8 had to
    # fail closed on it. On the floor the parser keeps it literal, like a
    # browser, and there is nothing to report.
    assert _first_style_value("<p style=\"--marker:'&ltrix;';color:red\">x</p>") == (
        "--marker:'&ltrix;';color:red"
    )
    errors, _ = _lint("<p style=\"--marker:'&ltrix;';color:red\">x</p>")
    assert errors == []


# Real theme CSS is full of legitimate comments (the seed templates carry
# 39-43 per rendered page), so the stricter scan must not report any of
# these. The count is asserted: a corpus that silently became empty
# would make this test pass while proving nothing.
THEME_CORPUS_CASES = (
    "/* tokens */\n:root{--ink:#1a1a1a;--paper:#fff}",
    "/* background: url(https://ok.example/x.png); */\np{color:var(--ink)}",
    '/* @import "https://ok.example/x.css"; */\np{color:red}',
    r'.icon-\" {color:red} /* background:url(https://ok.example/y.png) */',
    r'.a\/b{color:red} /* url(https://ok.example/z.png) */',
    'nav li + li::before{content:"\\00B7"} /* middot */',
    "p{content:\"it's\"} /* url(https://ok.example/q.png) */",
    "p{background:url(" + DATA_PNG + ")} /* inlined by inline_images */",
    '@font-face{font-family:"X";src:url(data:font/woff2;base64,AAAA)}',
    "svg .grad{fill:url(#grad)} /* same-document paint server */",
    "p{color:red}\r\n/* crlf and form feed are ordinary whitespace */\fq{color:blue}",
    # Unterminated: the comment runs to the end, as it does in a browser,
    # so the reference inside it is inert (the regex used to report it).
    "p{color:red} /* unterminated: url(https://ok.example/w.png)",
)


def test_style_theme_corpus_with_comments_is_fine():
    assert len(THEME_CORPUS_CASES) == 12
    for css in THEME_CORPUS_CASES:
        errors, _ = _lint("<style>%s</style>" % css)
        assert errors == [], (css, errors)


def test_style_inert_constructs_are_over_rejected_not_parsed():
    # Two forms a browser fetches nothing for, which the scan reports
    # anyway. Recorded deliberately: over-rejection is the direction this
    # module chooses, and a future reader should see it was a decision.
    # A string carrying BOTH comment markers and url() text:
    errors, _ = _lint(
        '<style>.x{content:"/* url(https://ok.example/icon.png) */"}</style>'
    )
    assert any("ok.example" in e for e in errors)
    # url( whose unquoted target contains a quote is a bad-url token:
    errors, _ = _lint(
        '<style>@font-face{src:url(/*c*/"data:font/woff2;base64,AAAA")}</style>'
    )
    assert any("unparseable" in e for e in errors)


@pytest.mark.parametrize(
    "css",
    [
        "\\",                       # a lone trailing backslash
        "color:red;x:\\",           # an ident ending in one
        'content:"\\',              # inside an unterminated string
        "background:url(\\",        # inside an unterminated url token
        "/* background:url(https://e.x/p)",   # an unterminated comment
        "--x:<",
        "--x:<!",
        "--x:<!--",                 # a CDO at EOF
    ],
)
def test_style_truncated_css_terminates(css):
    # Every scan branch must advance: a lone trailing backslash used to
    # spin the loop forever, allocating as it went — a hang reachable
    # from <style>\</style>.
    errors, _ = _lint("<style>%s</style>" % css)
    assert isinstance(errors, list)


def test_style_unparseable_url_construct_errors():
    # An unterminated quote defeats extraction; that is itself an error.
    errors, _ = _lint('<style>.x{background:url("https://evil.example/x}</style>')
    assert any("unparseable" in e for e in errors)


def test_unclosed_style_element_still_scanned():
    errors, _ = _lint("<style>.x{background:url(https://evil.example/x)}")
    assert any("evil.example" in e for e in errors)


# --- charts declared: the one controlled opening (Phase 4) -----------------
#
# When the manifest declares charts, an inline src-less <script> (and the
# inert <canvas> it draws on) is allowed; EVERY other Phase 3 rule still
# applies. Script content is deliberately not validated — the trust
# boundary is upstream in charts.serialize_chart_config (see lint.py's
# script-check comment).

CHARTS_DECLARED = {
    "charts": [{"id": "sales", "type": "bar", "data_slot": "sales_data"}]
}


def _lint_with_charts(markup):
    return lint_html("<html><body>" + markup + "</body></html>", CHARTS_DECLARED)


def test_canvas_and_inline_script_allowed_when_charts_declared():
    errors, warnings = _lint_with_charts(
        '<canvas id="sales"></canvas>'
        '<script>new Chart(document.getElementById("sales"), {});</script>'
    )
    assert errors == []
    assert warnings == []


def test_event_handler_still_errors_when_charts_declared():
    errors, _ = _lint_with_charts('<canvas id="x" onclick="alert(1)"></canvas>')
    assert any("event handler" in e for e in errors)


def test_javascript_uri_still_errors_when_charts_declared():
    errors, _ = _lint_with_charts('<a href="javascript:alert(1)">x</a>')
    assert any("javascript" in e for e in errors)


def test_remote_resources_still_error_when_charts_declared():
    errors, _ = _lint_with_charts('<img src="https://evil.example/x.png">')
    assert any("https://evil.example/x.png" in e for e in errors)
    errors, _ = _lint_with_charts(
        '<link rel="stylesheet" href="https://fonts.example/f.css">'
    )
    assert any("https://fonts.example/f.css" in e for e in errors)


def test_banned_tags_still_error_when_charts_declared():
    errors, _ = _lint_with_charts('<iframe srcdoc="<script>x</script>"></iframe>')
    assert any("<iframe>" in e and "never allowed" in e for e in errors)


def test_meta_refresh_and_base_href_still_error_when_charts_declared():
    errors, _ = _lint_with_charts(
        '<meta http-equiv="refresh" content="0;url=https://evil.example/">'
    )
    assert any("refresh" in e for e in errors)
    errors, _ = _lint_with_charts('<base href="https://evil.example/">')
    assert any("base href" in e for e in errors)


def test_css_fetches_still_error_when_charts_declared():
    errors, _ = _lint_with_charts(
        "<style>@import url(https://evil.example/x.css);</style>"
    )
    assert any("@import" in e for e in errors)
    errors, _ = _lint_with_charts(
        "<style>.x{background:url(https://evil.example/x.png)}</style>"
    )
    assert any("evil.example" in e for e in errors)


# --- inline-script allowlist on a chart page (Phase 4 tightening) -----------
#
# Declaring charts no longer blesses ANY inline <script>. The only inline
# script bodies accepted are the ones the RENDERER emits: the vendored
# Chart.js library and, per declared chart, an init in exactly
# charts.chart_init_js's byte shape whose id is declared and whose config
# is the safe-serialized JSON. Everything else is an error — so a
# third-party template cannot ride a chart declaration to embed its own
# JavaScript.

VALID_INIT = 'new Chart(document.getElementById("sales"), {"type":"bar"});'


def test_extra_hand_added_inline_script_rejected_on_chart_page():
    # The core adversarial case: a legit chart init AND a bolted-on
    # arbitrary script. The init passes; the extra script is named and
    # errors, so the page fails closed.
    errors, _ = _lint_with_charts(
        '<canvas id="sales"></canvas>'
        f"<script>{VALID_INIT}</script>"
        "<script>alert(1)</script>"
    )
    assert any("unexpected inline" in e and "alert(1)" in e for e in errors)


def test_init_for_undeclared_chart_id_rejected():
    errors, _ = _lint_with_charts(
        '<script>new Chart(document.getElementById("ghost"), {});</script>'
    )
    assert any("ghost" in e and "does not declare" in e for e in errors)


def test_init_with_raw_lt_in_config_rejected():
    # A hand-forged config carrying an unescaped "<" is not
    # serialize_chart_config output (which u003c-escapes every "<"), even
    # though the init wrapper is well-formed and the id is declared.
    errors, _ = _lint_with_charts(
        '<script>new Chart(document.getElementById("sales"), '
        '{"x":"<script>"});</script>'
    )
    assert any("raw" in e and "sales" in e for e in errors)


def test_init_with_non_json_config_rejected():
    # The init wrapper matches and the id is declared, but the config
    # argument is code, not data — renderer output always parses as JSON.
    errors, _ = _lint_with_charts(
        '<script>new Chart(document.getElementById("sales"), '
        "fetch('/x'));</script>"
    )
    assert any("not valid JSON" in e and "sales" in e for e in errors)


def test_self_closing_inline_script_rejected_on_chart_page():
    # A body-less <script/> is nothing the renderer emits.
    errors, _ = _lint_with_charts("<script></script>")
    assert any("unexpected inline" in e for e in errors)


def test_whitespace_around_valid_init_is_fine():
    errors, _ = _lint_with_charts(f"<script>\n  {VALID_INIT}\n</script>")
    assert errors == []


def test_vendored_chartjs_library_body_is_accepted():
    # The other allowed body: the vendored bundle's INLINED text (same
    # chart_lib_inline_text() render.py calls), as the page's single
    # library <script>. Use the real file via the shared function so the
    # byte-equality path is exercised end to end.
    lib = chart_lib_inline_text(CHARTJS_BUNDLE)
    errors, _ = _lint_with_charts(f"<script>{lib}</script>")
    assert errors == []


def test_raw_vendored_chartjs_bundle_with_sourcemap_comment_is_rejected():
    # lint's allowlist matches the INLINED form only (sourceMappingURL
    # stripped) -- the raw on-disk bundle, sourceMappingURL comment and
    # all, is not a body render.py ever emits and must not be accepted
    # as if it were.
    lib = CHARTJS_BUNDLE.read_text(encoding="utf-8")
    errors, _ = _lint_with_charts(f"<script>{lib}</script>")
    assert any("unexpected inline" in e for e in errors)


def test_two_declared_inits_both_accepted():
    manifest = {
        "charts": [
            {"id": "sales", "type": "bar", "data_slot": "s"},
            {"id": "trend", "type": "line", "data_slot": "t"},
        ]
    }
    html = (
        "<html><body>"
        '<script>new Chart(document.getElementById("sales"), {});</script>'
        '<script>new Chart(document.getElementById("trend"), {});</script>'
        "</body></html>"
    )
    errors, _ = lint_html(html, manifest)
    assert errors == []


# --- exact-match mode (allowed_scripts) --------------------------------------
# The render path passes lint_html the exact inline-script bodies it
# emitted; the page's scripts must equal that multiset. The structural
# fallback above still governs direct calls without a render context.

INIT_SALES = 'new Chart(document.getElementById("sales"), {"type":"bar"});'
INIT_TREND = 'new Chart(document.getElementById("trend"), {"type":"line"});'
# Structurally valid for the declared id "sales" — right shape, valid
# JSON, no raw "<" — but not the body the renderer emitted.
INIT_FORGED = 'new Chart(document.getElementById("sales"), {"type":"pie"});'

EXACT_MANIFEST = {"charts": ["sales"]}


def _scripts_page(*bodies, canvas_ids=()):
    # canvas_ids: <canvas id="..."> markup for completeness check 3
    # (every declared chart id needs a matching canvas) — supplied by
    # tests that mean to pass a FULL exact-mode page and left empty by
    # tests that are deliberately exercising only the script multiset in
    # isolation (see test_allowed_scripts_missing_canvas_for_declared_chart_errors
    # below, which relies on the empty default).
    canvases = "".join(f'<canvas id="{cid}"></canvas>' for cid in canvas_ids)
    scripts = "".join(f"<script>{body}</script>" for body in bodies)
    return f"<html><body>{canvases}{scripts}</body></html>"


def test_allowed_scripts_exact_set_passes():
    errors, _ = lint_html(
        _scripts_page(INIT_SALES, canvas_ids=("sales",)),
        EXACT_MANIFEST,
        allowed_scripts=[INIT_SALES],
    )
    assert errors == []


def test_allowed_scripts_rejects_forged_body_the_fallback_accepts():
    # The exact-match win: a hand-forged init for the DECLARED id with
    # different config bytes passes the structural fallback...
    errors, _ = lint_html(_scripts_page(INIT_FORGED), EXACT_MANIFEST)
    assert errors == []
    # ...but exact mode knows the renderer never emitted that body.
    errors, _ = lint_html(
        _scripts_page(INIT_FORGED, canvas_ids=("sales",)),
        EXACT_MANIFEST,
        allowed_scripts=[INIT_SALES],
    )
    assert any("unexpected inline" in e for e in errors)
    # And the body it replaced is reported missing.
    assert any("missing" in e for e in errors)


def test_allowed_scripts_rejects_extra_inline_script():
    errors, _ = lint_html(
        _scripts_page(INIT_SALES, "alert(1)", canvas_ids=("sales",)),
        EXACT_MANIFEST,
        allowed_scripts=[INIT_SALES],
    )
    assert any("unexpected inline" in e and "alert(1)" in e for e in errors)


def test_allowed_scripts_rejects_duplicate():
    errors, _ = lint_html(
        _scripts_page(INIT_SALES, INIT_SALES, canvas_ids=("sales",)),
        EXACT_MANIFEST,
        allowed_scripts=[INIT_SALES],
    )
    assert any("duplicate" in e for e in errors)


def test_allowed_scripts_reports_missing_expected_script():
    errors, _ = lint_html(
        _scripts_page(INIT_SALES, canvas_ids=("sales", "trend")),
        {"charts": ["sales", "trend"]},
        allowed_scripts=[INIT_SALES, INIT_TREND],
    )
    assert any("missing" in e and "trend" in e for e in errors)


def test_allowed_scripts_normalizes_surrounding_whitespace_only():
    # Whitespace around a body is template indentation, stripped
    # identically on both sides; whitespace INSIDE a body still breaks
    # equality (it is a different script).
    errors, _ = lint_html(
        _scripts_page("\n  " + INIT_SALES + "\n", canvas_ids=("sales",)),
        EXACT_MANIFEST,
        allowed_scripts=[INIT_SALES],
    )
    assert errors == []
    inner_ws = INIT_SALES.replace('"), ', '"),  ')
    errors, _ = lint_html(
        _scripts_page(inner_ws, canvas_ids=("sales",)),
        EXACT_MANIFEST,
        allowed_scripts=[INIT_SALES],
    )
    assert any("unexpected inline" in e for e in errors)


def test_allowed_scripts_does_not_bless_scripts_on_chartless_page():
    # Exact mode never engages without declared charts: the chartless
    # no-script rule stays absolute regardless of what a caller passes.
    errors, _ = lint_html(
        _scripts_page(INIT_SALES), {"charts": []}, allowed_scripts=[INIT_SALES]
    )
    assert any("declares no charts" in e for e in errors)


def test_allowed_scripts_none_keeps_structural_fallback():
    # No render context: the structural judgment of the sections above
    # is unchanged (renderer-shaped init for a declared id passes).
    errors, _ = lint_html(_scripts_page(INIT_SALES), EXACT_MANIFEST)
    assert errors == []


# --- exact-match completeness checks: type, order, canvas -------------------
# The multiset match alone only proves the exact bodies are present; these
# three checks close the remaining gap to "the page actually draws the
# charts" (see the module docstring's closing paragraph). All three engage
# only in exact mode (allowed_scripts given, charts declared).

@pytest.mark.parametrize(
    "bad_type", ["application/json", "text/template", "text/plain"]
)
def test_allowed_scripts_rejects_non_executable_script_type(bad_type):
    html = (
        '<html><body><canvas id="sales"></canvas>'
        f'<script type="{bad_type}">{INIT_SALES}</script>'
        "</body></html>"
    )
    errors, _ = lint_html(html, EXACT_MANIFEST, allowed_scripts=[INIT_SALES])
    assert any(
        "bare executable" in e and bad_type in e for e in errors
    ), errors


@pytest.mark.parametrize("ok_type", ["text/javascript", "module", ""])
def test_allowed_scripts_permits_executable_script_types(ok_type):
    html = (
        '<html><body><canvas id="sales"></canvas>'
        f'<script type="{ok_type}">{INIT_SALES}</script>'
        "</body></html>"
    )
    errors, _ = lint_html(html, EXACT_MANIFEST, allowed_scripts=[INIT_SALES])
    assert errors == []


def test_init_before_library_in_document_order_errors():
    lib = chart_lib_inline_text(CHARTJS_BUNDLE)
    html = (
        '<html><body><canvas id="sales"></canvas>'
        f"<script>{INIT_SALES}</script>"
        f"<script>{lib}</script>"
        "</body></html>"
    )
    errors, _ = lint_html(
        html, EXACT_MANIFEST, allowed_scripts=[lib, INIT_SALES]
    )
    assert any("library must load before" in e for e in errors)


def test_library_before_init_in_document_order_is_fine():
    lib = chart_lib_inline_text(CHARTJS_BUNDLE)
    html = (
        "<html><head>"
        f"<script>{lib}</script>"
        f'</head><body><canvas id="sales"></canvas>'
        f"<script>{INIT_SALES}</script>"
        "</body></html>"
    )
    errors, _ = lint_html(
        html, EXACT_MANIFEST, allowed_scripts=[lib, INIT_SALES]
    )
    assert errors == []


def test_allowed_scripts_missing_canvas_for_declared_chart_errors():
    # No canvas markup at all (the default of _scripts_page) for a
    # manifest that declares chart id "sales".
    errors, _ = lint_html(
        _scripts_page(INIT_SALES), EXACT_MANIFEST, allowed_scripts=[INIT_SALES]
    )
    assert any("no <canvas" in e and "sales" in e for e in errors)


def test_chart_id_on_div_not_canvas_still_errors():
    # The id is present on the page, just not on a <canvas> — the init
    # script's getElementById call would resolve to a <div>, not
    # something Chart.js can draw on.
    html = (
        '<html><body><div id="sales"></div>'
        f"<script>{INIT_SALES}</script>"
        "</body></html>"
    )
    errors, _ = lint_html(html, EXACT_MANIFEST, allowed_scripts=[INIT_SALES])
    assert any("no <canvas" in e and "sales" in e for e in errors)


def test_realistic_chart_page_lib_first_bare_scripts_canvas_per_chart_passes():
    # Shaped like the real render pipeline's output: library in <head>
    # (so it loads before any init), one <canvas id> + bare init
    # <script> per declared chart in <body>.
    lib = chart_lib_inline_text(CHARTJS_BUNDLE)
    manifest = {
        "charts": [
            {"id": "sales", "type": "bar", "data_slot": "s"},
            {"id": "trend", "type": "line", "data_slot": "t"},
        ]
    }
    html = (
        "<html><head>"
        f"<script>{lib}</script>"
        "</head><body>"
        f'<canvas id="sales"></canvas><script>{INIT_SALES}</script>'
        f'<canvas id="trend"></canvas><script>{INIT_TREND}</script>'
        "</body></html>"
    )
    errors, _ = lint_html(
        html, manifest, allowed_scripts=[lib, INIT_SALES, INIT_TREND]
    )
    assert errors == []


def test_standalone_clean_chartless_page_passes():
    html = "<!doctype html><html><body><p>hello</p></body></html>"
    errors, warnings = lint_standalone(html)
    assert errors == []
    assert warnings == []


def test_standalone_remote_image_fails():
    html = '<html><body><img src="https://cdn.example/x.png"></body></html>'
    errors, warnings = lint_standalone(html)
    assert any("https://cdn.example/x.png" in e for e in errors)


def test_standalone_script_src_fails():
    html = '<html><body><script src="https://evil.example/x.js"></script></body></html>'
    errors, warnings = lint_standalone(html)
    assert any("script" in e for e in errors)


def test_standalone_arbitrary_inline_script_fails():
    html = "<html><body><script>alert(1)</script></body></html>"
    errors, warnings = lint_standalone(html)
    assert any("unexpected inline" in e for e in errors)


def test_standalone_genuine_chart_page_passes():
    # The exact two script shapes the renderer emits: the vendored
    # library and a chart_init_js-shaped init. Standalone mode has no
    # manifest, so ANY well-formed init id must be accepted.
    lib = chart_lib_inline_text(CHARTJS_BUNDLE)
    html = (
        "<html><body>"
        f"<script>{lib}</script>"
        '<canvas id="tweaked-chart"></canvas>'
        '<script>new Chart(document.getElementById("tweaked-chart"), '
        '{"type": "bar"});</script>'
        "</body></html>"
    )
    errors, warnings = lint_standalone(html)
    assert errors == []


def test_standalone_init_with_non_json_config_fails():
    html = (
        "<html><body>"
        '<script>new Chart(document.getElementById("x"), fetch("/steal"));'
        "</script></body></html>"
    )
    errors, warnings = lint_standalone(html)
    assert errors != []


def test_lint_html_still_rejects_undeclared_chart_id():
    # Regression pin: the manifest-backed path keeps its declared-id
    # check even though standalone mode relaxes it.
    html = (
        "<html><body>"
        '<script>new Chart(document.getElementById("rogue"), {});</script>'
        "</body></html>"
    )
    errors, warnings = lint_html(html, {"charts": ["bar-chart"]})
    assert any("does not declare" in e for e in errors)
