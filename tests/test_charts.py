"""Tests for the safe chart config builder (Phase 4 security crux).

The serialize tests are the breakout defense: content-controlled
strings must never be able to terminate the inline <script> element,
open a new one, or start an HTML comment. They assert the serialized
output contains no raw '<' at all, then parse it back to prove the
data survived intact (safe, not mangled).
"""
import copy
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo._paths import REPO_ROOT
from jimemo.charts import (
    DEFAULT_PALETTE,
    build_chart_config,
    serialize_chart_config,
)
from jimemo.errors import ContentError, ManifestError

DECL = {"id": "sales", "type": "bar", "data_slot": "sales_data"}


def chart_data(labels=None, series=None):
    return {
        "labels": ["Q1", "Q2", "Q3"] if labels is None else labels,
        "series": (
            [{"name": "Revenue", "values": [1, 2, 3]}]
            if series is None
            else series
        ),
    }


def assert_script_safe(out: str):
    assert "</script>" not in out
    assert "<script" not in out
    assert "<!--" not in out
    assert "<" not in out  # every single '<' must be \u003c-escaped
    assert out.isascii()


# --- serialize: breakout defense (MANDATORY security tests) ---

INJECTIONS = [
    "</script><script>alert(1)</script>",
    "<!--",
    "<img src=x onerror=alert(1)>",
    "</ScRiPt ><svg onload=alert(1)>",
    "<!--<script>-->",
]


@pytest.mark.parametrize("evil", INJECTIONS)
def test_serialize_neutralizes_injection_in_labels(evil):
    config = build_chart_config(DECL, chart_data(labels=[evil, "b", "c"]))
    out = serialize_chart_config(config)
    assert_script_safe(out)
    # \u003c is an ordinary JSON string escape: parsing the output
    # directly must recover the original label byte-for-byte.
    assert json.loads(out)["data"]["labels"][0] == evil


@pytest.mark.parametrize("evil", INJECTIONS)
def test_serialize_neutralizes_injection_in_series_name(evil):
    config = build_chart_config(
        DECL, chart_data(series=[{"name": evil, "values": [1, 2, 3]}])
    )
    out = serialize_chart_config(config)
    assert_script_safe(out)
    assert json.loads(out)["data"]["datasets"][0]["label"] == evil


def test_serialize_neutralizes_injection_in_title():
    decl = dict(DECL, title="</script><script>alert(1)</script>")
    out = serialize_chart_config(build_chart_config(decl, chart_data()))
    assert_script_safe(out)
    parsed = json.loads(out)
    assert parsed["options"]["plugins"]["title"]["text"] == decl["title"]


def test_serialize_escapes_non_ascii_and_js_line_separators():
    label = "café\u2028\u2029end"
    out = serialize_chart_config(build_chart_config(DECL, chart_data(labels=[label, "b", "c"])))
    assert out.isascii()
    assert "\u2028" not in out and "\u2029" not in out
    assert json.loads(out)["data"]["labels"][0] == label


def test_serialize_plain_config_has_no_raw_angle_bracket():
    out = serialize_chart_config(build_chart_config(DECL, chart_data()))
    assert_script_safe(out)


def test_serialize_is_deterministic():
    payload = chart_data(
        labels=["</script>", "b", "c"],
        series=[
            {"name": "Revenue", "values": [1, 2, 3]},
            {"name": "Costs", "values": [4.5, 5, 6]},
        ],
    )
    first = serialize_chart_config(
        build_chart_config(copy.deepcopy(DECL), copy.deepcopy(payload))
    )
    second = serialize_chart_config(
        build_chart_config(copy.deepcopy(DECL), copy.deepcopy(payload))
    )
    assert first == second


# --- build: valid shapes ---

def test_build_valid_columnar_mapping():
    config = build_chart_config(
        DECL,
        {
            "labels": ["Q1", "Q2", "Q3"],
            "series": [
                {"name": "Revenue", "values": [1200, 1350, 1480]},
                {"name": "Costs", "values": [900, 940, 1010.5]},
            ],
        },
    )
    c0, c1 = DEFAULT_PALETTE[0], DEFAULT_PALETTE[1]
    assert config == {
        "type": "bar",
        "data": {
            "labels": ["Q1", "Q2", "Q3"],
            "datasets": [
                {
                    "label": "Revenue",
                    "data": [1200, 1350, 1480],
                    "backgroundColor": c0,
                    "borderColor": c0,
                },
                {
                    "label": "Costs",
                    "data": [900, 940, 1010.5],
                    "backgroundColor": c1,
                    "borderColor": c1,
                },
            ],
        },
        "options": {},
    }


def test_build_accepts_single_element_list_wrapper():
    # manifest 'data' slots are list-valued; the mapping arrives as the
    # single element of the slot list.
    bare = build_chart_config(DECL, chart_data())
    wrapped = build_chart_config(DECL, [chart_data()])
    assert bare == wrapped


def test_build_title_lands_in_options():
    decl = dict(DECL, title="Sales by quarter")
    config = build_chart_config(decl, chart_data())
    assert config["options"] == {
        "plugins": {"title": {"display": True, "text": "Sales by quarter"}}
    }


def test_build_numeric_labels_coerced_to_strings():
    config = build_chart_config(DECL, chart_data(labels=[2023, 2024, 2025]))
    assert config["data"]["labels"] == ["2023", "2024", "2025"]


def test_build_pie_colors_per_slice():
    decl = dict(DECL, type="pie")
    config = build_chart_config(decl, chart_data())
    dataset = config["data"]["datasets"][0]
    assert dataset["backgroundColor"] == [
        DEFAULT_PALETTE[0],
        DEFAULT_PALETTE[1],
        DEFAULT_PALETTE[2],
    ]
    assert "borderColor" not in dataset


def test_build_custom_palette_cycles():
    series = [
        {"name": "a", "values": [1, 1, 1]},
        {"name": "b", "values": [2, 2, 2]},
        {"name": "c", "values": [3, 3, 3]},
    ]
    config = build_chart_config(
        DECL, chart_data(series=series), palette=["#111111", "#222222"]
    )
    colors = [d["backgroundColor"] for d in config["data"]["datasets"]]
    assert colors == ["#111111", "#222222", "#111111"]


# --- default palette (dataviz toolkit) ---


def test_default_palette_applied_deterministically():
    # dataset i always gets DEFAULT_PALETTE[i] when palette=None (no
    # hashing/randomness) — pin every slot, not just the first two.
    series = [{"name": f"s{i}", "values": [i, i, i]} for i in range(len(DEFAULT_PALETTE))]
    config = build_chart_config(DECL, chart_data(series=series))
    colors = [d["backgroundColor"] for d in config["data"]["datasets"]]
    assert colors == list(DEFAULT_PALETTE)
    assert [d["borderColor"] for d in config["data"]["datasets"]] == list(DEFAULT_PALETTE)


def test_default_palette_cycles_past_eight():
    # a 9th+ series wraps back to slot 1 rather than inventing a hue.
    n = len(DEFAULT_PALETTE) + 3
    series = [{"name": f"s{i}", "values": [i]} for i in range(n)]
    config = build_chart_config(DECL, {"labels": ["only"], "series": series})
    colors = [d["backgroundColor"] for d in config["data"]["datasets"]]
    expected = [DEFAULT_PALETTE[i % len(DEFAULT_PALETTE)] for i in range(n)]
    assert colors == expected


def test_default_palette_matches_light_tokens_css():
    # tokens.css is the CSS-facing documentation of this same palette;
    # keep the two from drifting apart. Parses only the first (light,
    # default :root) block, before the first dark-mode override.
    tokens_css = (REPO_ROOT / "toolkit" / "tokens.css").read_text(encoding="utf-8")
    # Split on the block's opening brace, not just the media-query text,
    # since the file's header comment also mentions "@media (...) dark"
    # in prose above the real block.
    light_section = tokens_css.split("@media (prefers-color-scheme: dark) {", 1)[0]
    found = dict(
        re.findall(r"--jm-chart-(\d+):\s*(#[0-9a-fA-F]{6});", light_section)
    )
    assert found, "no --jm-chart-* tokens found in tokens.css light block"
    token_palette = [found[str(i)] for i in range(1, len(found) + 1)]
    assert [c.lower() for c in token_palette] == [c.lower() for c in DEFAULT_PALETTE]


# --- build: bad data → ContentError naming the problem ---

def test_ragged_series_rejected():
    series = [{"name": "Revenue", "values": [1, 2]}]  # 2 values, 3 labels
    with pytest.raises(ContentError, match="2 values but there are 3 labels"):
        build_chart_config(DECL, chart_data(series=series))


def test_non_numeric_value_rejected():
    series = [{"name": "Revenue", "values": [1, "abc", 3]}]
    with pytest.raises(ContentError, match="must be a number"):
        build_chart_config(DECL, chart_data(series=series))


def test_boolean_value_rejected():
    series = [{"name": "Revenue", "values": [1, True, 3]}]
    with pytest.raises(ContentError, match="must be a number"):
        build_chart_config(DECL, chart_data(series=series))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_value_rejected(bad):
    series = [{"name": "Revenue", "values": [1, bad, 3]}]
    with pytest.raises(ContentError, match="finite"):
        build_chart_config(DECL, chart_data(series=series))


def test_missing_labels_rejected():
    with pytest.raises(ContentError, match="labels"):
        build_chart_config(DECL, {"series": [{"name": "a", "values": [1]}]})


def test_empty_labels_rejected():
    with pytest.raises(ContentError, match="labels"):
        build_chart_config(
            DECL, {"labels": [], "series": [{"name": "a", "values": []}]}
        )


def test_missing_series_rejected():
    with pytest.raises(ContentError, match="series"):
        build_chart_config(DECL, {"labels": ["a"]})


def test_empty_series_rejected():
    with pytest.raises(ContentError, match="series"):
        build_chart_config(DECL, {"labels": ["a"], "series": []})


def test_structured_label_rejected():
    with pytest.raises(ContentError, match="labels"):
        build_chart_config(DECL, chart_data(labels=[{"nested": 1}, "b", "c"]))


def test_unknown_key_in_mapping_rejected():
    payload = chart_data()
    payload["onClick"] = "alert(1)"
    with pytest.raises(ContentError, match="onClick"):
        build_chart_config(DECL, payload)


def test_unknown_key_in_series_entry_rejected():
    series = [{"name": "a", "values": [1, 2, 3], "borderColor": "url(x)"}]
    with pytest.raises(ContentError, match="borderColor"):
        build_chart_config(DECL, chart_data(series=series))


def test_series_missing_values_rejected():
    with pytest.raises(ContentError, match="values"):
        build_chart_config(DECL, chart_data(series=[{"name": "a"}]))


def test_multi_item_slot_list_rejected():
    with pytest.raises(ContentError, match="exactly one"):
        build_chart_config(DECL, [chart_data(), chart_data()])


def test_none_slot_value_rejected():
    with pytest.raises(ContentError, match="mapping"):
        build_chart_config(DECL, None)


def test_string_slot_value_rejected():
    with pytest.raises(ContentError, match="mapping"):
        build_chart_config(DECL, "labels,series")


# --- build: bad declaration → ManifestError ---

def test_bad_chart_type_in_decl_rejected():
    decl = dict(DECL, type="bubble")
    with pytest.raises(ManifestError, match="bubble"):
        build_chart_config(decl, chart_data())


@pytest.mark.parametrize("field", ["id", "type", "data_slot"])
def test_decl_missing_field_rejected(field):
    decl = dict(DECL)
    del decl[field]
    with pytest.raises(ManifestError, match=field):
        build_chart_config(decl, chart_data())


def test_non_dict_decl_rejected():
    with pytest.raises(ManifestError, match="declaration"):
        build_chart_config("sales", chart_data())


def test_bad_palette_rejected():
    with pytest.raises(ValueError, match="palette"):
        build_chart_config(DECL, chart_data(), palette=[])
