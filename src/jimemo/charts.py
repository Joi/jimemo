"""Build and serialize Chart.js configs from content data — safely.

This module is the only path by which content data may reach an inline
``<script>`` in a rendered page, so it is a security boundary (see the
Phase 4 plan, "The security crux"). Two rules hold everywhere here:

1.  Config is data, never code. The config is a plain Python dict built
    from validated content values and serialized with ``json.dumps``.
    Content strings become JSON string values; content numbers must be
    real int/float. Nothing from content is ever concatenated into JS
    source text.

2.  Breakout defense. ``serialize_chart_config`` escapes every ``<`` in
    the JSON as ``\\u003c`` (a JSON string escape that parses back to
    the same character), so the payload cannot contain ``</script>``,
    ``<script``, or ``<!--`` and therefore cannot terminate the script
    element, open a new one, or start an HTML comment. With the default
    ``ensure_ascii=True`` every non-ASCII character (including the JS
    line separators U+2028/U+2029) is ``\\uXXXX``-escaped too, leaving
    a pure-ASCII string safe to drop verbatim inside ``<script>``.

Chart data contract
-------------------
A chart's ``data_slot`` names a manifest slot of type ``data`` declared
WITHOUT an ``items`` schema. The slot value must be the mapping::

    {"labels": [<label>, ...],                  # 1+ scalar labels
     "series": [{"name": <name>,                # 1+ series
                 "values": [<number>, ...]},    # len == len(labels)
                ...]}

Because manifest v1 ``data`` slots are list-valued (content.py), the
mapping normally arrives wrapped as the single element of the slot
list; content authors write::

    sales_by_quarter:
      - labels: [Q1, Q2, Q3]
        series:
          - {name: Revenue, values: [1200, 1350, 1480]}
          - {name: Costs,   values: [900, 940, 1010]}

``build_chart_config`` accepts the bare mapping or that one-element
list wrapper. Labels and series names are scalars (coerced to str);
values must be finite int/float — anything else is a ContentError
naming the problem, never silently passed into the config.
"""
import json
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .errors import ContentError, ManifestError
from .manifest import CHART_ID_PATTERN, CHART_ID_RE, CHART_TYPES

# The dataviz-toolkit categorical palette (8 hues, fixed CVD-optimized
# order — see the dataviz skill's color-formula.md and palette.md).
# These are the LIGHT-mode values; they must match toolkit/tokens.css's
# --jm-chart-1..8 exactly (tests/test_charts.py checks the two files
# stay in sync). Chart.js renders to <canvas>, which cannot read CSS
# custom properties, so this Python list — not the CSS tokens — is the
# actual source of truth for rendered chart colors; the tokens exist
# for documentation and any CSS-styled chart chrome. Only the light
# palette is baked in: a rendered page's canvas colors are fixed at
# render time, while light/dark is a view-time CSS choice, so a
# dark-adaptive canvas is out of scope here (see toolkit/README.md).
#
# Cycled per dataset (per slice for pie/doughnut, which color by data
# point rather than by dataset); a 9th series wraps back to slot 1
# rather than inventing a new hue, per the dataviz skill's rule that
# categorical hues are never generated on the fly.
DEFAULT_PALETTE = (
    "#2a78d6",  # blue
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#008300",  # green
    "#4a3aa7",  # violet
    "#e34948",  # red
    "#e87ba4",  # magenta
    "#eb6834",  # orange
)


def _coerce_scalar(context: str, value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContentError(
            f"chart data {context} must be text or a number, got {value!r}"
        )
    return str(value)


def _unwrap_slot_value(slot_value: Any) -> Dict[str, Any]:
    # Manifest 'data' slots are list-valued, so the {labels, series}
    # mapping arrives as the single element of the slot list; accept
    # the bare mapping too for direct callers.
    if isinstance(slot_value, list):
        if len(slot_value) != 1 or not isinstance(slot_value[0], dict):
            raise ContentError(
                "chart data slot must contain exactly one "
                "{labels, series} mapping, got a list of "
                f"{len(slot_value)} item(s)"
            )
        slot_value = slot_value[0]
    if not isinstance(slot_value, dict):
        raise ContentError(
            "chart data slot must be a {labels, series} mapping, got "
            f"{type(slot_value).__name__}"
        )
    return slot_value


def _validate_labels(data: Dict[str, Any]) -> List[str]:
    if "labels" not in data:
        raise ContentError("chart data missing required key 'labels'")
    labels = data["labels"]
    if not isinstance(labels, list) or not labels:
        raise ContentError("chart data 'labels' must be a non-empty list")
    return [
        _coerce_scalar(f"labels[{i}]", label) for i, label in enumerate(labels)
    ]


def _validate_series(data: Dict[str, Any], n_labels: int) -> List[Dict[str, Any]]:
    if "series" not in data:
        raise ContentError("chart data missing required key 'series'")
    series = data["series"]
    if not isinstance(series, list) or not series:
        raise ContentError("chart data 'series' must be a non-empty list")

    validated: List[Dict[str, Any]] = []
    for i, entry in enumerate(series):
        if not isinstance(entry, dict):
            raise ContentError(
                f"chart data series[{i}] must be a {{name, values}} object"
            )
        for key in entry:
            if key not in ("name", "values"):
                raise ContentError(
                    f"chart data series[{i}] has unknown key {key!r} "
                    "(allowed: ['name', 'values'])"
                )
        for field in ("name", "values"):
            if field not in entry:
                raise ContentError(
                    f"chart data series[{i}] missing required key {field!r}"
                )
        name = _coerce_scalar(f"series[{i}].name", entry["name"])

        values = entry["values"]
        if not isinstance(values, list):
            raise ContentError(
                f"chart data series[{i}] ({name!r}) 'values' must be a list"
            )
        if len(values) != n_labels:
            raise ContentError(
                f"chart data series[{i}] ({name!r}) has {len(values)} "
                f"values but there are {n_labels} labels"
            )
        for j, value in enumerate(values):
            # bool is an int subclass; a bare true/false in chart data
            # is an authoring mistake, not a number.
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ContentError(
                    f"chart data series[{i}] ({name!r}) values[{j}] must "
                    f"be a number, got {value!r}"
                )
            if isinstance(value, float) and not math.isfinite(value):
                raise ContentError(
                    f"chart data series[{i}] ({name!r}) values[{j}] must "
                    f"be finite, got {value!r}"
                )
        validated.append({"name": name, "values": list(values)})
    return validated


def build_chart_config(
    chart_decl: Dict[str, Any],
    slot_value: Any,
    palette: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Build a Chart.js config dict from a manifest chart declaration
    and the content's data-slot value (shape documented above).

    Raises ManifestError for a bad declaration (load_manifest already
    validates declarations; this re-check keeps the boundary safe for
    direct callers) and ContentError for data that does not fit.
    """
    if not isinstance(chart_decl, dict):
        raise ManifestError(f"chart declaration must be an object, got {chart_decl!r}")
    for field in ("id", "type", "data_slot"):
        if field not in chart_decl:
            raise ManifestError(
                f"chart declaration missing required field {field!r}"
            )
    chart_type = chart_decl["type"]
    if chart_type not in CHART_TYPES:
        raise ManifestError(
            f"chart {chart_decl['id']!r} has invalid type {chart_type!r} "
            f"(must be one of {list(CHART_TYPES)})"
        )

    if palette is None:
        palette = DEFAULT_PALETTE
    if (
        not isinstance(palette, (list, tuple))
        or not palette
        or not all(isinstance(c, str) for c in palette)
    ):
        raise ValueError("palette must be a non-empty sequence of color strings")

    data = _unwrap_slot_value(slot_value)
    for key in data:
        if key not in ("labels", "series"):
            raise ContentError(
                f"chart data has unknown key {key!r} "
                "(allowed: ['labels', 'series'])"
            )
    labels = _validate_labels(data)
    series = _validate_series(data, len(labels))

    datasets: List[Dict[str, Any]] = []
    for i, entry in enumerate(series):
        dataset: Dict[str, Any] = {
            "label": entry["name"],
            "data": entry["values"],
        }
        if chart_type in ("pie", "doughnut"):
            # Chart.js colors these per data point, not per dataset.
            dataset["backgroundColor"] = [
                palette[j % len(palette)] for j in range(len(labels))
            ]
        else:
            color = palette[i % len(palette)]
            dataset["backgroundColor"] = color
            dataset["borderColor"] = color
        datasets.append(dataset)

    config: Dict[str, Any] = {
        "type": chart_type,
        "data": {"labels": labels, "datasets": datasets},
        "options": {},
    }
    # A chart's title is rendered once, by the toolkit block heading
    # (see render.py's `charts` context and the template's <h2>), which
    # reads chart_decl["title"] directly. Chart.js's own title plugin is
    # deliberately left unset here so the title never renders a second
    # time, in Chart.js's own font, inside the canvas.
    return config


# --- the inline init script body -------------------------------------------
# chart_init_js is the ONLY producer of a chart's init <script> body
# (render.py Markup-wraps its output for the chart macro to emit
# verbatim), and parse_chart_init_js is the matching recognizer lint.py
# uses to accept nothing else inside <script> on a chart page. Building
# both from the same three literal segments makes the byte-exact shape a
# single source of truth that render and lint cannot drift apart on.
# The '"), ' separator (with the space) is pinned by the goldens.
_INIT_JS_PREFIX = 'new Chart(document.getElementById("'
_INIT_JS_MIDDLE = '"), '
_INIT_JS_SUFFIX = ');'

_INIT_JS_RE = re.compile(
    re.escape(_INIT_JS_PREFIX)
    + "(" + CHART_ID_PATTERN + ")"
    + re.escape(_INIT_JS_MIDDLE)
    + "(.*)"
    + re.escape(_INIT_JS_SUFFIX),
    re.ASCII | re.DOTALL,
)


def chart_init_js(chart_id: str, config_json: str) -> str:
    """The full JavaScript body of the single inline ``<script>`` that
    initializes one chart::

        new Chart(document.getElementById("<id>"), <config_json>);

    ``chart_id`` must be a manifest-validated chart id and
    ``config_json`` must be serialize_chart_config output; both are
    re-checked here because this text is emitted verbatim inside a
    script element and this module is the security boundary.
    """
    if not isinstance(chart_id, str) or not CHART_ID_RE.match(chart_id):
        raise ManifestError(
            f"chart id {chart_id!r} does not match {CHART_ID_RE.pattern} "
            "and cannot be embedded in an init script"
        )
    if "<" in config_json:
        raise ValueError(
            "config_json contains a raw '<' — it must be "
            "serialize_chart_config output, which \\u003c-escapes every '<'"
        )
    return _INIT_JS_PREFIX + chart_id + _INIT_JS_MIDDLE + config_json + _INIT_JS_SUFFIX


def parse_chart_init_js(script_body: str) -> Optional[Tuple[str, str]]:
    """``(chart_id, config_json)`` if ``script_body`` has exactly the
    byte shape chart_init_js emits, else None. Recognition only — the
    caller (lint) still judges whether the id is declared and the config
    text is the safe-serialized form."""
    match = _INIT_JS_RE.fullmatch(script_body)
    if match is None:
        return None
    return match.group(1), match.group(2)


def serialize_chart_config(config: Dict[str, Any]) -> str:
    """Serialize a chart config to a JSON string safe to embed verbatim
    inside an inline ``<script>`` element.

    The breakout defense: every ``<`` becomes the JSON string escape
    ``\\u003c``, so the output cannot contain ``</script>``,
    ``<script``, or ``<!--``. ``json.loads`` on the result recovers the
    original data exactly.
    """
    text = json.dumps(config, separators=(",", ":"), allow_nan=False)
    # ensure_ascii=True (the default above) already \uXXXX-escapes all
    # non-ASCII, including the JS line separators U+2028/U+2029; these
    # replaces are belt-and-braces should that ever change.
    text = text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return text.replace("<", "\\u003c")
