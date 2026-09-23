"""The chart init script's theme runtime (jimemo#7n1f), executed.

chart_init_js wraps each chart's construction in a small fixed runtime
that maps the baked DEFAULT_PALETTE onto the page's --jm-chart-N tokens
and repaints when the theme changes. These tests run the REAL init body
under node with stub browser globals (a fake Chart that records what it
was built with, a switchable token map for getComputedStyle, and
captured matchMedia / MutationObserver / print listeners). The live
browser check is done at review time; this pins the logic in the suite.
Skipped when node is not installed.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.charts import (
    DEFAULT_PALETTE,
    build_chart_config,
    chart_init_js,
    serialize_chart_config,
)

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

DARK = ["#3987e5", "#199e70", "#c98500", "#008300",
        "#9085e9", "#e66767", "#d55181", "#d95926"]
DARK_TOKENS = {f"--jm-chart-{i + 1}": c for i, c in enumerate(DARK)}
LIGHT_TOKENS = {f"--jm-chart-{i + 1}": c for i, c in enumerate(DEFAULT_PALETTE)}

# Reads {body, tokens, steps} on stdin. Steps: ["tokens", {...}],
# ["mutate"] (data-theme changed), ["media"] (prefers-color-scheme
# changed), ["beforeprint"], ["afterprint"], ["snap"]. Prints
# {first: <datasets at construction>, snaps: [{colors, updates, mode,
# animationDuringUpdate, animationAfter}]}.
DRIVER = r"""
let input = "";
process.stdin.on("data", d => { input += d; });
process.stdin.on("end", () => {
  const {body, tokens, steps} = JSON.parse(input);
  let current = tokens;
  const listeners = {};
  let observer = null;
  global.document = {
    documentElement: {},
    getElementById: id => ({id}),
  };
  global.getComputedStyle = el => {
    if (el !== global.document.documentElement) throw new Error("wrong element");
    return {getPropertyValue: name => current[name] || ""};
  };
  global.matchMedia = query => ({
    addEventListener: (type, fn) => { listeners["media:" + query + ":" + type] = fn; },
  });
  global.MutationObserver = function (fn) {
    this.observe = (target, opts) => { observer = {fn, target, opts}; };
  };
  global.addEventListener = (type, fn) => { listeners[type] = fn; };
  const charts = [];
  global.Chart = function (el, cfg) {
    this.el = el;
    this.data = cfg.data;
    this.config = {options: cfg.options};
    this.updates = 0;
    this.mode = null;
    this.animationDuringUpdate = null;
    this.first = JSON.parse(JSON.stringify(cfg.data.datasets));
    this.update = mode => {
      this.updates += 1;
      this.mode = mode === undefined ? "default" : mode;
      this.animationDuringUpdate = this.config.options.animation;
    };
    charts.push(this);
  };
  eval(body);
  const chart = charts[0];
  const colors = ds => ds.map(d => ({bg: d.backgroundColor, border: d.borderColor}));
  const snaps = [];
  for (const [op, arg] of steps) {
    if (op === "tokens") current = arg;
    else if (op === "mutate") observer.fn([]);
    else if (op === "media") listeners["media:(prefers-color-scheme: dark):change"]();
    else if (op === "beforeprint" || op === "afterprint") listeners[op]();
    else if (op === "snap") snaps.push({
      colors: colors(chart.data.datasets), updates: chart.updates, mode: chart.mode,
      animationDuringUpdate: chart.animationDuringUpdate,
      animationAfter: "animation" in chart.config.options ? chart.config.options.animation : "absent",
    });
  }
  console.log(JSON.stringify({
    charts: charts.length,
    el: chart.el,
    first: colors(chart.first),
    observer: {opts: observer.opts, rootIsHtml: observer.target === global.document.documentElement},
    snaps,
  }));
});
"""


def run(config, tokens, steps=()):
    body = chart_init_js("c1", serialize_chart_config(config))
    proc = subprocess.run(
        [NODE, "-e", DRIVER],
        input=json.dumps({"body": body, "tokens": tokens, "steps": list(steps)}),
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def bar_config(n=2, palette=None):
    decl = {"id": "c1", "type": "bar", "data_slot": "d"}
    data = {"labels": ["a", "b"],
            "series": [{"name": f"s{i}", "values": [1, 2]} for i in range(n)]}
    return build_chart_config(decl, data, palette=palette)


def pie_config():
    decl = {"id": "c1", "type": "doughnut", "data_slot": "d"}
    data = {"labels": ["a", "b", "c"], "series": [{"name": "s", "values": [1, 2, 3]}]}
    return build_chart_config(decl, data)


def test_first_draw_uses_the_dark_tokens():
    out = run(bar_config(), DARK_TOKENS)
    assert out["charts"] == 1
    assert out["el"] == {"id": "c1"}
    assert out["first"] == [
        {"bg": DARK[0], "border": DARK[0]},
        {"bg": DARK[1], "border": DARK[1]},
    ]


def test_first_draw_uses_the_light_tokens():
    out = run(bar_config(), LIGHT_TOKENS)
    assert out["first"][0] == {"bg": DEFAULT_PALETTE[0], "border": DEFAULT_PALETTE[0]}


def test_pie_slices_each_mapped_and_no_border_invented():
    out = run(pie_config(), DARK_TOKENS)
    assert out["first"] == [{"bg": DARK[:3]}]


def test_token_values_are_trimmed():
    out = run(bar_config(1), {"--jm-chart-1": "  #3987e5 "})
    assert out["first"][0]["bg"] == "#3987e5"


def test_empty_token_keeps_the_baked_color():
    out = run(bar_config(2), {"--jm-chart-2": "#199e70"})
    assert out["first"] == [
        {"bg": DEFAULT_PALETTE[0], "border": DEFAULT_PALETTE[0]},
        {"bg": "#199e70", "border": "#199e70"},
    ]


def test_custom_palette_left_alone_but_default_literals_adapt():
    out = run(bar_config(2, palette=["#123456", DEFAULT_PALETTE[0]]), DARK_TOKENS)
    assert out["first"] == [
        {"bg": "#123456", "border": "#123456"},
        # A custom entry equal to a baked value names token slot 1.
        {"bg": DARK[0], "border": DARK[0]},
    ]


def test_data_theme_change_repaints():
    out = run(bar_config(1), DARK_TOKENS,
              [["tokens", LIGHT_TOKENS], ["mutate"], ["snap"]])
    assert out["observer"] == {
        "opts": {"attributes": True, "attributeFilter": ["data-theme"]},
        "rootIsHtml": True,
    }
    # A plain update() with animation off for that call only: a
    # synchronous redraw that also refreshes bars' shared options
    # (update("none") does not), then the option is put back as it was.
    assert out["snaps"] == [{
        "colors": [{"bg": DEFAULT_PALETTE[0], "border": DEFAULT_PALETTE[0]}],
        "updates": 1, "mode": "default",
        "animationDuringUpdate": False, "animationAfter": "absent",
    }]


def test_repaint_restores_an_explicit_animation_option():
    config = bar_config(1)
    config["options"]["animation"] = {"duration": 5}
    out = run(config, DARK_TOKENS, [["media"], ["snap"]])
    assert out["snaps"][0]["animationDuringUpdate"] is False
    assert out["snaps"][0]["animationAfter"] == {"duration": 5}


def test_color_scheme_change_repaints():
    out = run(bar_config(1), LIGHT_TOKENS,
              [["tokens", DARK_TOKENS], ["media"], ["snap"]])
    assert out["snaps"][0]["colors"] == [{"bg": DARK[0], "border": DARK[0]}]
    assert out["snaps"][0]["updates"] == 1


def test_print_uses_the_baked_light_palette_then_restores():
    out = run(pie_config(), DARK_TOKENS,
              [["beforeprint"], ["snap"], ["afterprint"], ["snap"]])
    printed, restored = out["snaps"]
    assert printed["colors"] == [{"bg": list(DEFAULT_PALETTE[:3])}]
    assert restored["colors"] == [{"bg": DARK[:3]}]
    assert restored["updates"] == 2
