# jimemo toolkit

The design system every jimemo template renders through: design tokens,
base typography, nine components, and their Jinja2 macros. Templates
import the macros and declare which components they use; the renderer
inlines only that CSS into the final single-file page.

The aesthetic is an exhibition catalog, not a dashboard: unbleached
paper and sumi ink, a dyed-indigo accent, serif prose with a tracked
sans "apparatus" voice for labels, captions, and table headers. Dark
mode is the same page after dark — ink ground, paper-toned text. Print
always gets the light palette.

## Files

| File | Purpose |
|---|---|
| `tokens.css` | All custom properties (`--jm-*`). Light on `:root`, dark via `prefers-color-scheme` **and** `[data-theme]` (the attribute wins in both directions). Themes override this file's values. |
| `base.css` | Reset, document defaults, `.jm-container`, `.jm-prose` typography, focus/selection, print rules. |
| `components/<name>.css` | One file per component, loaded only when the template's manifest lists it. |
| `macros.html.j2` | One macro per component. `{% import "macros.html.j2" as ui %}` |
| `page.html.j2` | Base page skeleton templates extend: blocks `title`, `head_extra`, `content`, `footer`; the renderer injects the `<style>` tag through the `styles` variable. No JavaScript, ever. |

## Tokens

### Color roles

| Token | Light | Dark | Role |
|---|---|---|---|
| `--jm-bg` | `#faf9f7` | `#16181b` | page ground |
| `--jm-surface` | `#ffffff` | `#1e2126` | tiles, cards, code blocks |
| `--jm-text` | `#262521` | `#e9e5dc` | ink |
| `--jm-muted` | `#6b6459` | `#a29b8d` | secondary text, apparatus |
| `--jm-accent` | `#17597a` | `#85b8d3` | dyed indigo (ai): links, markers, kickers |
| `--jm-accent-contrast` | `#ffffff` | `#0e2431` | text on accent fills |
| `--jm-border` | `#e4e0d8` | `#34383f` | hairlines |
| `--jm-positive` | `#2f6b46` | `#8ac29c` | moss: gains, good states |
| `--jm-negative` | `#a63d33` | `#dd9182` | bengara: losses, warnings |

### Type

Two voices, three stacks — all system fonts:

| Token | Stack | Carries |
|---|---|---|
| `--jm-font-prose` | Iowan Old Style → Palatino → Georgia (+ Hiragino Mincho for CJK) | content: body, titles, values, tree names |
| `--jm-font-ui` | system-ui (+ Hiragino Kaku Gothic for CJK) | apparatus: labels, captions, dates, table headers — usually uppercase with `--jm-tracking-caps` |
| `--jm-font-mono` | ui-monospace → Menlo/Consolas | code |

Scale (ratio 1.25, anchored at a 17px body): `--jm-text-xs` 0.68rem ·
`sm` 0.85 · `md` 1.0625 · `lg` 1.3281 · `xl` 1.6602 · `2xl` 2.0752 ·
`3xl` 2.594. Leading: `--jm-leading-tight` 1.12, `-snug` 1.4, `-body` 1.72.

### Chart palette

Eight categorical colors, `--jm-chart-1` through `--jm-chart-8`, for charts
built by `src/jimemo/charts.py` (Phase 4). The order (blue, aqua, yellow,
green, violet, red, magenta, orange) is fixed and CVD-optimized — never
reorder it, and never cycle past 8; a 9th series wraps back to slot 1
rather than inventing a hue.

| Slot | Hue | Light | Dark |
|---|---|---|---|
| `--jm-chart-1` | blue | `#2a78d6` | `#3987e5` |
| `--jm-chart-2` | aqua | `#1baf7a` | `#199e70` |
| `--jm-chart-3` | yellow | `#eda100` | `#c98500` |
| `--jm-chart-4` | green | `#008300` | `#008300` |
| `--jm-chart-5` | violet | `#4a3aa7` | `#9085e9` |
| `--jm-chart-6` | red | `#e34948` | `#e66767` |
| `--jm-chart-7` | magenta | `#e87ba4` | `#d55181` |
| `--jm-chart-8` | orange | `#eb6834` | `#d95926` |

Deliberately brand-neutral rather than derived from `--jm-accent`: chart
series need an identity channel distinct from the accent's editorial role
(links, kickers, markers). This is the dataviz skill's reference categorical
palette, unmodified, validated with `dataviz/scripts/validate_palette.js`
against both `--jm-bg` and `--jm-surface` (a chart `<canvas>` has no
background of its own, so it shows whichever surface it sits on):

- **Light** (surface `#ffffff` or `#faf9f7`): lightness band, chroma floor,
  and CVD separation all pass (worst adjacent ΔE 24.2). Three slots (aqua,
  yellow, magenta) fall below 3:1 contrast — a documented WARN, not a
  failure, that obligates a relief channel: Chart.js's own legend plus
  direct labels satisfy this, so no further mitigation is needed.
- **Dark** (surface `#1e2126` or `#16181b`): lightness band, chroma floor,
  and contrast all pass. CVD separation sits in the 8–12 floor band (worst
  adjacent ΔE 10.3) rather than the ≥12 target — legal per the skill only
  with secondary encoding (legend + direct labels), which the chart macro
  already provides.

**Chart.js draws to `<canvas>`, which cannot read CSS custom properties.**
These tokens exist for documentation and any CSS-styled chart chrome (axis
labels, legends built outside canvas, etc.) — they are not read by the
chart renderer. The actual source of truth for rendered chart colors is the
Python list `charts.DEFAULT_PALETTE` in `src/jimemo/charts.py`, which must
match this table's **light** values exactly (`tests/test_charts.py` parses
`tokens.css` and asserts the two stay in sync). Only the light palette is
baked into rendered charts: a page's light/dark appearance is a view-time
CSS choice (`prefers-color-scheme` or `data-theme`), but canvas pixels are
fixed at render time, so a dark-adaptive chart is a documented future item,
not current behavior — a chart viewed in dark mode today still draws in
light-palette colors.

### Space, radius, elevation, layout

- `--jm-space-1..8`: 0.25 / 0.5 / 0.75 / 1 / 1.5 / 2.25 / 3.25 / 4.5 rem
- `--jm-radius-sm` 4px · `-md` 8px · `-lg` 14px
- `--jm-shadow-sm`, `--jm-shadow-md` — whispers; `none` in print
- `--jm-content-max` 46rem (reading column) · `--jm-content-wide` 72rem
  (grids and dashboards; `.jm-container--wide`, or `wide=true` in the
  page skeleton context)

## Conventions

- Class naming: `.jm-<component>` block, `__element` children,
  `--modifier` variants (`.jm-stat-tile__delta--positive`).
- Markdown slots render inside a `.jm-prose` wrapper. Components are
  **siblings** of prose blocks, never children, so prose element
  selectors cannot leak into component internals.
- Every component holds up at 360px viewport width and in print; wide
  tables scroll inside `.jm-data-table__scroll`, never the page.
- Nothing in the toolkit imports other stylesheets, references remote
  URLs, or emits script tags — pages render identically offline.

## Components and macros

Import once per template:

```jinja
{% import "macros.html.j2" as ui %}
```

### page-header

The masthead: short indigo bar, kicker, serif title, apparatus meta row.

```jinja
{{ ui.page_header("The Wabana Garden in Midsummer",
     kicker="Garden survey",
     subtitle="Thirty flowering plants for the tea room.",
     meta=["Thimphu", "28 June 2026", "30 species"]) }}
```

### stat-tile

A quiet figure with a tracked label; `stat_row` lays tiles in a grid.
`delta` is pre-signed text; `tone` is `positive` or `negative`.

```jinja
{{ ui.stat_row([
     {"value": "30", "label": "Species"},
     {"value": "14", "label": "In bloom", "delta": "+4", "tone": "positive"},
   ]) }}
{{ ui.stat_tile("2,320 m", "Elevation") }}
```

### card-grid

Catalog entries: 4:3 image plate, serif title, badge, apparatus meta.

```jinja
{{ ui.card_grid([
     {"title": "Kikyō", "image": "kikyo.jpg", "image_alt": "Balloon flower",
      "text": "Buds swelling on all eight crowns.",
      "meta": "Terrace wall · T-1", "badge": "budding"},
   ]) }}
```

### timeline

Vertical rail with square markers; dates in the apparatus voice.
`badges` entries are strings or `{label, tone}`.

```jinja
{{ ui.timeline([
     {"date": "12 June 2026", "title": "Serrata hedge opens",
      "body": "Ten days ahead of 2025.",
      "badges": [{"label": "in bloom", "tone": "accent"}]},
   ]) }}
```

### data-table

A booktable: heavy rule above, hairline rows, no zebra. Columns are
strings or `{label, key?, num?}`; `num: true` right-aligns with tabular
figures. Rows are dicts (looked up by `key`, defaulting to the label)
or positional lists. Always wrapped in a scroll container.

```jinja
{{ ui.data_table(
     [{"label": "Species"}, {"label": "Height (cm)", "key": "height", "num": true}],
     rows, caption="Measured soil to tip.") }}
```

### figure-block

A mounted plate with a numbered caption. `plain=true` drops the frame.

```jinja
{{ ui.figure_block("east-bed.jpg", alt="The east bed after rain",
     label="Fig. 1", caption="The serrata hedge closes the view.",
     credit="Photograph: survey walk") }}
```

### badge

Outlined tracked label. Tones: default (muted), `accent`, `positive`,
`negative`.

```jinja
{{ ui.badge("in bloom", tone="accent") }}
```

### toc

Contents between hairline rules; top level numbered by CSS counter.
Items nest via `children`.

```jinja
{{ ui.toc([
     {"label": "Overview", "href": "#overview"},
     {"label": "Calendar", "href": "#calendar",
      "children": [{"label": "July onward", "href": "#july"}]},
   ]) }}
```

### tree

Genealogy or hierarchy with hairline connectors; recurses to any
depth. A node is `{label (or name), meta?, note?, children?}`; pass one
node or a list.

```jinja
{{ ui.tree({"label": "Mother plant", "meta": "Kyoto, 2019",
            "children": [{"label": "Cutting A", "meta": "2021 · E-2"}]}) }}
```

## Page skeleton

```jinja
{% extends "page.html.j2" %}
{% block title %}My page{% endblock %}
{% block content %} … {% endblock %}
{% block footer %}Rendered with jimemo{% endblock %}
```

Context: `styles` (the `<style>` tag, injected by the renderer),
optional `theme` (`"light"`/`"dark"` pins the theme via `data-theme`;
omit to follow the OS), `lang` (default `en`), `wide` (widens the
column).
