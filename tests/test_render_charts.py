"""Chart render wiring (Phase 4): the vendored Chart.js library and the
per-chart canvas/init-script pairs enter the page ONLY when the manifest
declares charts, and content data can never become live markup or
script. The plan's mandatory adversarial integration test lives here.
"""
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo import cli
from jimemo.errors import ContentError, ManifestError
from jimemo.lint import lint_html
from jimemo.manifest import load_manifest
from jimemo.render import render_page

# The literal six-character JSON escape sequence backslash-u003c that
# serialize_chart_config substitutes for every "<". Built via chr(92)
# so this source file itself never contains a backslash-u escape that
# an editor or tool layer could decode into a real "<".
ESCAPED_LT = chr(92) + "u003c"

EVIL_LABEL = "</script><script>alert(1)</script>"
EVIL_SERIES_NAME = "<img src=x onerror=alert(1)>"

CHART_MANIFEST = """\
{
  "name": "chart-tpl",
  "version": 1,
  "title": "Chart Template",
  "slots": {
    "title": {"type": "text", "required": true},
    "sales_data": {"type": "data", "required": true}
  },
  "components": ["page-header"],
  "charts": [
    {"id": "sales", "type": "bar", "data_slot": "sales_data",
     "title": "Sales by quarter"}
  ]
}
"""

CHART_TEMPLATE = """\
{% extends "page.html.j2" %}
{% import "macros.html.j2" as ui %}
{% block title %}{{ title }}{% endblock %}
{% block content %}
{{ ui.page_header(title) }}
{% for c in charts %}{{ ui.chart(c.id, c.config_json) }}{% endfor %}
{% endblock %}
"""

SALES_DATA = [{
    "labels": ["Q1", "Q2", "Q3"],
    "series": [
        {"name": "Revenue", "values": [1200, 1350, 1480]},
        {"name": "Costs", "values": [900, 940, 1010]},
    ],
}]


def make_chart_template_dir(root, manifest_source=CHART_MANIFEST,
                            template_source=CHART_TEMPLATE,
                            name="chart-tpl"):
    template_dir = root / name
    template_dir.mkdir(parents=True)
    (template_dir / "manifest.json").write_text(manifest_source)
    (template_dir / "template.html.j2").write_text(template_source)
    return template_dir


def script_open_tags(html):
    return re.findall(r"<script[^>]*>", html)


def extract_config(html, chart_id):
    """The init script's config argument, parsed back through JSON —
    proving the escaped payload is data the browser will decode
    identically, not markup."""
    marker = 'new Chart(document.getElementById("%s"), ' % chart_id
    start = html.index(marker) + len(marker)
    end = html.index(");</script>", start)
    return json.loads(html[start:end])


def test_escaped_lt_constant_is_the_json_escape_for_lt():
    # Guards the chr(92) construction: six chars, and JSON decodes it
    # back to a real "<".
    assert len(ESCAPED_LT) == 6
    assert ESCAPED_LT[1:] == "u003c"
    assert json.loads('"' + ESCAPED_LT + '"') == "<"


# --- happy path -----------------------------------------------------------

def test_chart_page_has_canvas_and_init_script(tmp_path):
    template_dir = make_chart_template_dir(tmp_path)
    html = render_page(template_dir, {"title": "Dash", "sales_data": SALES_DATA})
    assert '<canvas id="sales"></canvas>' in html
    assert 'new Chart(document.getElementById("sales"), ' in html


def test_chart_lib_inlined_once_in_head_before_init_script(tmp_path):
    template_dir = make_chart_template_dir(tmp_path)
    html = render_page(template_dir, {"title": "Dash", "sales_data": SALES_DATA})
    # The version banner appears exactly once: one lib, inlined once.
    assert html.count("Chart.js v") == 1
    assert html.index("Chart.js v") < html.index("</head>") < html.index("new Chart(")


def test_chart_config_round_trips_data_and_title(tmp_path):
    template_dir = make_chart_template_dir(tmp_path)
    html = render_page(template_dir, {"title": "Dash", "sales_data": SALES_DATA})
    config = extract_config(html, "sales")
    assert config["type"] == "bar"
    assert config["data"]["labels"] == ["Q1", "Q2", "Q3"]
    assert [d["label"] for d in config["data"]["datasets"]] == ["Revenue", "Costs"]
    assert config["data"]["datasets"][0]["data"] == [1200, 1350, 1480]
    # The manifest title ("Sales by quarter") renders once, via the
    # toolkit block heading (render.py's `charts` context, c.title) —
    # Chart.js's own title plugin must stay unset in the config.
    assert config["options"] == {}


def test_missing_chart_title_falls_back_to_chart_id(tmp_path):
    # Jinja's |default filter only fires on Undefined, not None, so a
    # template's {{ c.title|default(c.id) }} would render the literal
    # string "None" for a title-less declaration unless render.py's
    # `charts` context itself substitutes the chart id.
    manifest = CHART_MANIFEST.replace(
        '    {"id": "sales", "type": "bar", "data_slot": "sales_data",\n'
        '     "title": "Sales by quarter"}',
        '    {"id": "sales", "type": "bar", "data_slot": "sales_data"}',
    )
    template = CHART_TEMPLATE.replace(
        "{% for c in charts %}{{ ui.chart(c.id, c.config_json) }}{% endfor %}",
        "{% for c in charts %}<h2>{{ c.title }}</h2>"
        "{{ ui.chart(c.id, c.config_json) }}{% endfor %}",
    )
    template_dir = make_chart_template_dir(
        tmp_path, manifest_source=manifest, template_source=template
    )
    html = render_page(template_dir, {"title": "Dash", "sales_data": SALES_DATA})
    assert "<h2>sales</h2>" in html
    assert "None" not in html


def test_two_charts_share_one_inlined_lib(tmp_path):
    manifest = CHART_MANIFEST.replace(
        '"sales_data": {"type": "data", "required": true}',
        '"sales_data": {"type": "data", "required": true},\n'
        '    "trend_data": {"type": "data", "required": true}',
    ).replace(
        '     "title": "Sales by quarter"}',
        '     "title": "Sales by quarter"},\n'
        '    {"id": "trend", "type": "line", "data_slot": "trend_data"}',
    )
    template_dir = make_chart_template_dir(tmp_path, manifest_source=manifest)
    html = render_page(
        template_dir,
        {"title": "Dash", "sales_data": SALES_DATA, "trend_data": SALES_DATA},
    )
    assert html.count("Chart.js v") == 1
    assert script_open_tags(html) == ["<script>"] * 3  # lib + 2 inits
    assert '<canvas id="sales"></canvas>' in html
    assert '<canvas id="trend"></canvas>' in html
    assert extract_config(html, "sales")["type"] == "bar"
    assert extract_config(html, "trend")["type"] == "line"


def test_chart_page_is_self_contained(tmp_path):
    template_dir = make_chart_template_dir(tmp_path)
    html = render_page(template_dir, {"title": "Dash", "sales_data": SALES_DATA})
    # Outside script text (Chart.js's banner comment cites its homepage,
    # inert inside a JS comment), no remote reference survives anywhere.
    outside_scripts = re.sub(r"<script>.*?</script>", "", html, flags=re.DOTALL)
    assert "http://" not in outside_scripts
    assert "https://" not in outside_scripts
    # Structurally: every script is inline and attribute-less (no src).
    assert script_open_tags(html) == ["<script>", "<script>"]
    errors, _ = lint_html(html, load_manifest(template_dir))
    assert errors == []


# --- chartless pages: Phase 3 behavior byte-for-byte ------------------------

NO_CHART_MANIFEST = CHART_MANIFEST.replace(
    '"charts": [\n'
    '    {"id": "sales", "type": "bar", "data_slot": "sales_data",\n'
    '     "title": "Sales by quarter"}\n'
    "  ]",
    '"charts": []',
)

NO_CHART_TEMPLATE = CHART_TEMPLATE.replace(
    "{% for c in charts %}{{ ui.chart(c.id, c.config_json) }}{% endfor %}\n", ""
)


def test_chartless_page_emits_no_script_at_all(tmp_path):
    template_dir = make_chart_template_dir(
        tmp_path, manifest_source=NO_CHART_MANIFEST,
        template_source=NO_CHART_TEMPLATE,
    )
    html = render_page(template_dir, {"title": "Dash", "sales_data": SALES_DATA})
    assert "<script" not in html
    assert "Chart.js" not in html
    assert "canvas" not in html


def test_chartless_template_may_still_use_a_slot_named_charts(tmp_path):
    # Phase 3 unchanged: the charts/chart_lib context names are only
    # injected (and only reserved) when the manifest declares charts.
    manifest = """\
{
  "name": "charts-slot-tpl",
  "version": 1,
  "title": "T",
  "slots": {
    "title": {"type": "text", "required": true},
    "charts": {"type": "text"}
  },
  "components": ["page-header"],
  "charts": []
}
"""
    template = NO_CHART_TEMPLATE.replace(
        "{{ ui.page_header(title) }}",
        "{{ ui.page_header(title) }}\n<p>{{ charts }}</p>",
    )
    template_dir = make_chart_template_dir(
        tmp_path, manifest_source=manifest, template_source=template,
        name="charts-slot-tpl",
    )
    html = render_page(
        template_dir, {"title": "T", "charts": "a text slot named charts"}
    )
    assert "a text slot named charts" in html


def test_slot_named_charts_with_charts_declared_is_manifest_error(tmp_path):
    manifest = CHART_MANIFEST.replace(
        '"title": {"type": "text", "required": true},',
        '"title": {"type": "text", "required": true},\n'
        '    "charts": {"type": "text"},',
    )
    template_dir = make_chart_template_dir(tmp_path, manifest_source=manifest)
    with pytest.raises(ManifestError, match="'charts'.*collides"):
        render_page(template_dir, {"title": "T", "sales_data": SALES_DATA})


def test_slot_named_chart_lib_with_charts_declared_is_manifest_error(tmp_path):
    manifest = CHART_MANIFEST.replace(
        '"title": {"type": "text", "required": true},',
        '"title": {"type": "text", "required": true},\n'
        '    "chart_lib": {"type": "text"},',
    )
    template_dir = make_chart_template_dir(tmp_path, manifest_source=manifest)
    with pytest.raises(ManifestError, match="'chart_lib'.*collides"):
        render_page(template_dir, {"title": "T", "sales_data": SALES_DATA})


# --- chart data errors surface as clean domain errors ----------------------

def test_missing_chart_data_value_raises_content_error(tmp_path):
    template_dir = make_chart_template_dir(tmp_path)
    with pytest.raises(ContentError, match="chart 'sales'.*'sales_data'"):
        render_page(template_dir, {"title": "T"})


def test_malformed_chart_data_raises_content_error_naming_chart(tmp_path):
    template_dir = make_chart_template_dir(tmp_path)
    bad = [{"labels": [], "series": []}]
    with pytest.raises(ContentError, match="chart 'sales'.*labels"):
        render_page(template_dir, {"title": "T", "sales_data": bad})


def test_missing_chartjs_bundle_raises_clean_content_error(tmp_path, monkeypatch):
    monkeypatch.setattr("jimemo.render.CHARTJS_BUNDLE", tmp_path / "nope.js")
    template_dir = make_chart_template_dir(tmp_path)
    with pytest.raises(ContentError, match="vendored Chart.js"):
        render_page(template_dir, {"title": "T", "sales_data": SALES_DATA})


def test_bundle_with_script_close_sequence_is_refused(tmp_path, monkeypatch):
    # Defense in depth past the checksum: a swapped-in lib that could
    # terminate its own <script> element must never be inlined.
    bad = tmp_path / "bad.js"
    bad.write_text("var x = 1; // sneaky </scrIPT amid the code")
    monkeypatch.setattr("jimemo.render.CHARTJS_BUNDLE", bad)
    template_dir = make_chart_template_dir(tmp_path)
    with pytest.raises(ContentError, match="cannot be inlined safely"):
        render_page(template_dir, {"title": "T", "sales_data": SALES_DATA})


# --- THE security integration test (plan: "The security crux") -------------

def test_chart_injection_payloads_render_inert(tmp_path):
    template_dir = make_chart_template_dir(tmp_path)
    content = {
        "title": "Evil",
        "sales_data": [{
            "labels": [EVIL_LABEL, "Q2"],
            "series": [{"name": EVIL_SERIES_NAME, "values": [1, 2]}],
        }],
    }
    html = render_page(template_dir, content)

    # (a) the chart is present: canvas + Chart init script.
    assert '<canvas id="sales"></canvas>' in html
    assert 'new Chart(document.getElementById("sales"), ' in html

    # (b) the ONLY script elements are the inlined lib and the init,
    # both attribute-less — in particular src-less.
    assert script_open_tags(html) == ["<script>", "<script>"]

    # (c) the payloads never appear as live markup anywhere in the
    # output...
    assert EVIL_LABEL not in html
    assert EVIL_SERIES_NAME not in html
    assert "</script><script>alert" not in html
    assert re.search(r"<img[^>]*onerror", html) is None
    # ...only in "<"-escaped form inside the config JSON (a ">" alone
    # can close nothing, so only "<" needs escaping):
    assert (
        ESCAPED_LT + "/script>" + ESCAPED_LT + "script>alert(1)"
        + ESCAPED_LT + "/script>"
    ) in html
    assert (ESCAPED_LT + "img src=x onerror=alert(1)>") in html

    # The escaped JSON decodes back to the exact payload strings: the
    # data survived, the code did not.
    config = extract_config(html, "sales")
    assert config["data"]["labels"][0] == EVIL_LABEL
    assert config["data"]["datasets"][0]["label"] == EVIL_SERIES_NAME

    # (d) lint re-parses the full page and agrees it is clean (a live
    # on* attribute or stray tag would error)...
    manifest = load_manifest(template_dir)
    errors, _ = lint_html(html, manifest)
    assert errors == []
    # ...while the same page with a <script src> bolted on still fails
    # even though charts are declared.
    tampered = html.replace("</body>", '<script src="x.js"></script></body>')
    errors, _ = lint_html(tampered, manifest)
    assert any("script" in e and "x.js" in e for e in errors)


def test_macro_misuse_with_plain_string_fails_closed(tmp_path):
    # The chart macro takes config_json VERBATIM only via the renderer's
    # Markup-wrapped value. A template author who passes raw content
    # instead gets autoescaped entities — a broken chart, never markup.
    manifest = CHART_MANIFEST.replace(
        '"title": {"type": "text", "required": true},',
        '"title": {"type": "text", "required": true},\n'
        '    "payload": {"type": "text"},',
    )
    template = CHART_TEMPLATE.replace(
        "{% for c in charts %}{{ ui.chart(c.id, c.config_json) }}{% endfor %}",
        "{% for c in charts %}{{ ui.chart(c.id, payload) }}{% endfor %}",
    )
    template_dir = make_chart_template_dir(
        tmp_path, manifest_source=manifest, template_source=template
    )
    html = render_page(
        template_dir,
        {"title": "T", "payload": EVIL_LABEL, "sales_data": SALES_DATA},
    )
    assert EVIL_LABEL not in html
    assert "&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert script_open_tags(html) == ["<script>", "<script>"]


# --- CLI end to end ---------------------------------------------------------

CHART_CONTENT_YAML = (
    "title: Sales\n"
    "sales_data:\n"
    "  - labels: [Q1, Q2]\n"
    "    series:\n"
    "      - {name: Revenue, values: [1, 2]}\n"
)


def test_cli_render_chart_template_writes_chart_page(tmp_path, monkeypatch):
    make_chart_template_dir(tmp_path / "templates")
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [tmp_path / "templates"])
    content_file = tmp_path / "content.yaml"
    content_file.write_text(CHART_CONTENT_YAML)
    out_path = tmp_path / "out.html"

    rc = cli.main(["render", "chart-tpl", str(content_file), "-o", str(out_path)])
    assert rc == 0
    html = out_path.read_text(encoding="utf-8")
    assert '<canvas id="sales"></canvas>' in html
    assert html.count("Chart.js v") == 1


def test_cli_render_malformed_chart_data_exits_1_cleanly(
    tmp_path, monkeypatch, capsys
):
    make_chart_template_dir(tmp_path / "templates")
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [tmp_path / "templates"])
    content_file = tmp_path / "content.yaml"
    content_file.write_text(
        "title: Sales\n"
        "sales_data:\n"
        "  - labels: []\n"
        "    series: []\n"
    )
    out_path = tmp_path / "out.html"

    rc = cli.main(["render", "chart-tpl", str(content_file), "-o", str(out_path)])
    assert rc == 1
    assert not out_path.exists()
    err = capsys.readouterr().err
    assert "chart 'sales'" in err
    assert "Traceback" not in err
