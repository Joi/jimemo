# jimemo Phase 4: Charts + Infographic Components — Implementation Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Per-task review gates + a security-focused whole-phase review + roborev before merge.

**Goal:** templates can declare charts; `jimemo render` emits self-contained pages with vendored Chart.js inlined and chart config **safely** derived from content data. Ships a chart component/macro, a dataviz palette, and upgrades data-dashboard to show a real chart.

**Tracker:** kata b28w (parent 9wk1). Branch: phase4. Base: main @ f881001.

## The security crux (read first — this phase punctures Phase 3's no-script invariant)

Phase 3 made lint hard-reject every `<script>` when `manifest.charts == []`, and deliberately left one door: an inline `<script>` (NO src) is permitted when `manifest.charts` is non-empty. Phase 4 walks through that door, so these controls are NON-NEGOTIABLE:

1. **Config is data, never code.** Chart config is built as a Python dict from the content's chart data slot and serialized with `json.dumps`. Content values (labels, numbers, series names) are JSON string/number values — never interpolated into JS source.
2. **`</script>` / `<!--` breakout defense.** After `json.dumps`, escape the payload so it cannot terminate the script element or start a comment: replace `<` with `<` (covers `</script>`, `<!--`, `<script`). This is THE control that keeps untrusted chart data from becoming script. Unit-test it with a label literally containing `</script><img src=x onerror=alert(1)>`.
3. **Only the chart mechanism may emit script.** lint, when `charts` non-empty, allows inline `<script>` (no src) and `<canvas>`; it STILL rejects script `src`, `on*` handlers, `javascript:`, remote resources, exec/embed tags — everything from Phase 3 stays. Templates emit script ONLY via the chart macro; document this trust boundary.
4. **Chart.js is vendored + inlined** (no src, no CDN), pinned + checksummed + license, like every other vendored dep; `doctor` covers it. No network at view time — verify the vendored bundle has no fetch/Worker/font-CDN (research already confirmed for 4.5.x; re-verify the pinned build).

## Global Constraints
- Python 3.9 floor; stdlib + vendor/ only at runtime; PYTHONDONTWRITEBYTECODE=1.
- Vendored Chart.js: pin from research (v4.5.1 baseline — confirm latest stable + OSV at vendor time), MIT LICENSE shipped, SHA256SUMS updated (JS files included in the allowlist walk), CREDITS row. Chart.js is BROWSER JS (not imported by Python) — it lives under e.g. `charts/vendor/chartjs/` with its own checksums recorded in `vendor/SHA256SUMS` or a parallel `charts/SHA256SUMS` that `doctor` also verifies (decide in Task 1; the existing verify_checksums allowlist must cover it either way).
- Existing goldens for the 5 seed templates MUST stay byte-identical (they declare no charts) — regenerate only data-dashboard's if it gains a chart, and add new chart-template goldens.
- Commits end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Tasks

### Task 1: Vendor Chart.js (browser JS, pinned, checksummed)
Determine latest stable Chart.js on npm/GitHub (baseline v4.5.1) + OSV check; download the official `chart.umd.min.js` (or the UMD build) from the chartjs GitHub release (verify SHA against the release), place under `charts/vendor/chartjs/chart.umd.min.js` + `LICENSE`. Record in `vendor/SHA256SUMS` (extend verify_checksums to cover `charts/vendor/**` — or add a second sums file doctor verifies; keep the allowlist-complete + symlink/special-file rejection property). Re-verify the bundle is self-contained (no `fetch(`, `new Worker`, `@font-face`, no googleapis/gstatic/cdn URLs). `doctor` reports a `charts vendored (chart.js X.Y.Z)` line. CREDITS row. Accept: doctor clean; checksum tampering of the JS is caught.

### Task 2: Manifest chart declaration + safe config builder
- Manifest: a template declares charts via `charts: [{id, type, ...}]` or a `chart`-typed slot — define the shape (chart id, chart type enum: bar/line/pie/doughnut/radar/scatter, and which content data slot feeds it). Validate: type in the allowed enum; referenced data slot exists. ManifestError on bad shape.
- `src/jimemo/charts.py`: `build_chart_config(chart_decl, data) -> dict` producing a Chart.js `{type, data, options}` dict from the content's structured data (labels + datasets). Apply the dataviz palette (Task 4) for colors. `serialize_chart_config(config) -> str` = `json.dumps` + `<`→`<` escaping (the breakout defense). Unit-test the escaping with a `</script>`-bearing label.

### Task 3: Chart macro + render wiring + lint update
- `toolkit/macros.html.j2`: a `chart(id, config_json)` macro emitting `<canvas id=...>` + a single inline `<script>` that runs `new Chart(document.getElementById(id), <config_json>)`. The vendored Chart.js is inlined ONCE into the page (in the base skeleton or injected by the renderer) only when the manifest declares charts.
- Renderer: when `manifest.charts` non-empty, inline the vendored chart JS (read from charts/vendor, wrap in `<script>`), and pass each chart's serialized config to the macro. When empty, inject nothing (unchanged Phase 3 behavior; goldens stable).
- `src/jimemo/lint.py`: when `manifest["charts"]` non-empty, ALLOW inline `<script>` (no src) and `<canvas>`; keep every other rule (script src, on*, javascript:, remote resources, exec/embed tags, CSS fetches — all still errors). When empty, unchanged. Tests: a chart page passes lint; a chart page with an added `<script src=…>` still errors; on*/remote still error even with charts declared.
- Adversarial test: chart data containing `</script><script>alert(1)</script>` and `<img onerror>` renders to an INERT page (escaped in the JSON, no live script/handler).

### Task 4: dataviz palette
Load the dataviz skill; add a brand-neutral, accessible categorical + sequential palette as toolkit tokens (`--jm-chart-*`) and a Python mapping `charts.py` uses to color datasets. Light/dark aware. Keep it a small, documented default (swappable). Accept: a multi-series chart uses distinct, legible colors in light and dark.

### Task 5: chart-dashboard template + data-dashboard upgrade + goldens
- Add a `chart-dashboard` seed template (or extend data-dashboard) that declares 1-2 charts (a bar and a line) fed by a data slot, plus the existing stat tiles/tables. Real sample content. Suitability labels (tabular-data/metrics) + labeled_hash; self-rank #1.
- Generate its golden; if data-dashboard itself gains a chart, regenerate that golden and note it (the other 4 stay byte-identical).
- Integration: `jimemo render chart-dashboard <sample>` produces a self-contained page whose chart renders (verify by opening in a browser + screenshot; confirm the canvas draws and there are no console errors, no network requests).

### Task 6: docs + acceptance
Update README (chart example) + architecture.md (charts.py, charts/vendor). Full end-to-end: all CLI commands, full suite green, goldens stable (except the intentionally-changed chart ones), doctor clean incl. chart vendoring, adversarial chart-injection inert. Append phase summary to the SDD ledger.

## Out of scope (defer)
Mermaid diagrams (research: only when a diagram slot is declared; larger, separate concern — a fast-follow if wanted). Interactive/animated charts beyond static render. 3D/WebGL.

Whole-phase security review (adversarial: config injection, script-boundary, vendoring) + roborev before squash-merge to main.
