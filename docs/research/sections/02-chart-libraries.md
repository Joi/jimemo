# Chart / infographic libraries + licenses

Evaluated against jimemo's constraint that rendered output is self-contained
single-file HTML: any vendored chart library must run from one inline
`<script>` block with zero network fetches, workers, or external fonts at
view time. All facts below (license, version, release date, size) were
confirmed from each project's own repository — license text pulled from the
project's own `LICENSE`/`LICENSE.md` on GitHub, versions and release
timestamps from the npm registry and GitHub Releases API, and bundle sizes
measured directly by downloading each project's own minified UMD/IIFE
artifact from jsDelivr and checking byte size (raw and gzip).

## Candidates

| Name | Org | License (SPDX) | Latest stable version + release date | Minified size (KB) | Fully offline, single inline script | Notes |
|---|---|---|---|---|---|---|
| **Chart.js** | Chart.js (`chartjs` GitHub org; copyright "Chart.js Contributors") | MIT | v4.5.1 — 2025-10-13 | 203.6 KB (`chart.umd.min.js`); 69.0 KB gzip | Yes | Canvas-based. Single self-contained UMD file, no runtime dependencies, no web workers, no font/network fetch. Declarative `{type, data, options}` JSON config — easy for an agent to generate from structured content. Covers bar/line/pie/radar/scatter/bubble/mixed out of the box. |
| **Observable Plot** (+ D3 dependency) | Observable, Inc. (Plot); D3 org / Mike Bostock (D3) | ISC (Plot); ISC (D3) | Plot v0.6.17 — 2025-02-14; D3 v7.9.0 — 2024-03-12 | Plot 204.3 KB + D3 273.2 KB = **477.4 KB combined** (`plot.umd.min.js` + `d3.min.js`); 157.8 KB gzip combined | No | Fails the single-inline-script requirement: Plot's own UMD bundle does not embed D3; it declares D3 as an external UMD dependency (`require("d3@7.9.0/dist/d3.min.js")` in the bundle header) and calls `typeof d3` at load time, so jimemo would have to vendor and inline **two** separate scripts, not one. No network fetch is involved once both are inlined — the failure is the two-script requirement, not offline capability. SVG-based, grammar-of-graphics marks API. More verbose config for an agent to generate than Chart.js's flat JSON. Neither project has shipped a release in the last 12+ months (D3: Mar 2024, Plot: Feb 2025) — stable, not concerning on its own, but worth noting for a toolkit expecting to re-vendor periodically. |
| **Mermaid** | mermaid-js (GitHub org; copyright Knut Sveidqvist) | MIT | v11.16.0 — 2026-06-25 | 3,482.0 KB / 3.4 MB (`mermaid.min.js`); 952.0 KB gzip | Yes | Verified directly: the IIFE bundle contains no `fetch()` calls, no `new Worker(...)`, no `@font-face` declarations or remote font-file/font-CDN references (fonts.googleapis.com, fonts.gstatic.com, typekit, fontawesome — none present), and no CDN/network URLs beyond documentation strings embedded in error messages (chevrotain/lodash/jquery license comments). Diagram chunks are bundled internally, not fetched. Diagrams (flowchart, sequence, gantt, state, ER, etc.), not a chart library — different problem domain. Size is ~17x Chart.js and should never be a default inclusion; only worth inlining into templates that actually declare a diagram slot. A "tiny" build (~half the size) exists but drops mindmap/architecture diagrams, KaTeX math rendering, and lazy loading. |
| **Apache ECharts** (control) | Apache Software Foundation (`apache/echarts`) | Apache-2.0 | v6.1.0 — 2026-05-19 | 1,095.6 KB / 1.07 MB (`echarts.min.js`); 360.6 KB gzip | Yes | Self-contained UMD build (bundles its `zrender` canvas-rendering dependency internally), no network/worker requirement for standard 2D chart types. Broader chart-type/interaction surface than Chart.js (maps, 3D, WebGL large-data, treemaps) but ~5.4x Chart.js's inline size for capability jimemo's basic-chart use case doesn't need. Strong pedigree (ASF top-level project) but heavier than the job calls for as a default. |

## Recommendation

**Chart.js is the recommended default chart library** for jimemo's toolkit.
It is the only candidate that satisfies all four deciding constraints at
once: one self-contained UMD file with zero external script dependencies
and zero network/worker calls; MIT license from a well-known, actively
maintained org; the smallest inline footprint by a wide margin (203.6 KB
min / 69.0 KB gzip, versus 477.4 KB for Plot+D3, 1.07 MB for ECharts, and
3.4 MB for Mermaid); and a flat, declarative JSON config shape
(`{type, data, options}`) that is straightforward for an agent to generate
from a template's structured content schema. This confirms the design
spec's existing lean toward Chart.js.

**Observable Plot is not recommended** as the primary chart library despite
its permissive ISC license and Observable pedigree. Its own UMD bundle
requires D3 as a second, separately-loaded script — meaning jimemo would
have to vendor and inline two files instead of one, more than doubling the
size cost of Chart.js for equivalent basic charts — and its grammar-of-marks
API is more verbose for an agent to target than Chart.js's flat config. It
is worth revisiting only if a future template needs a chart type genuinely
outside Chart.js's plugin ecosystem (e.g., faceted small-multiples or
custom statistical marks).

**Mermaid is worth its size, but only conditionally.** At 3.4 MB minified
it must never be a default inline — it should be injected only into
templates that declare a diagram slot, per the render pipeline's existing
"inject Chart.js/Mermaid inline only if the template declares charts/diagrams"
step. For templates that render flowcharts, sequence diagrams, gantt charts,
or entity diagrams, Mermaid is still the right call: hand-rolled SVG cannot
reasonably reproduce its layout engine, and its license/org pedigree are
sound. If diagram templates turn out to be common, evaluate the "tiny"
Mermaid build (~half size, drops mindmap/architecture/KaTeX) to cut the
per-page cost further.

**Apache ECharts should be held in reserve, not vendored by default.** Its
Apache-2.0/ASF pedigree is as strong as Chart.js's MIT/Chart.js-org pedigree,
but at over 5x the inline size it isn't justified for jimemo's typical
single-chart memo or dashboard tile. Vendor it only if a specific template
needs a chart type Chart.js's ecosystem can't cover (geographic maps, 3D,
WebGL-scale datasets).

**Prefer inline SVG generated at render time (no JS library at all)** when
a template's chart need is simple: a single bar/line/pie series with fewer
than about 20 data points, a sparkline-style trend indicator, or a one-off
stat-tile decoration, with no interactivity (tooltips, zoom, legend
toggling) required. jimemo's Python render pipeline should generate this
SVG directly — as a `toolkit/components/` macro, consistent with the stat
tile/card components already planned — at zero added JS payload and zero
vendored-library cost. Switch to vendored Chart.js once a template needs
more than one chart, more than roughly 20 points, mixed chart types, or any
interactivity; switch to Mermaid once the content is a diagram rather than
a data chart.

### Sources

- [Chart.js LICENSE.md](https://github.com/chartjs/Chart.js/blob/master/LICENSE.md) — MIT, "Chart.js Contributors"
- [Chart.js GitHub releases](https://github.com/chartjs/Chart.js/releases) / [GitHub Releases API, tag v4.5.1](https://api.github.com/repos/chartjs/Chart.js/releases/tags/v4.5.1) — v4.5.1, published 2025-10-13
- [chart.js npm registry](https://registry.npmjs.org/chart.js) — version/time cross-check
- [chart.umd.min.js via jsDelivr](https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js) — size measured directly (208,522 bytes)
- [Observable Plot LICENSE](https://github.com/observablehq/plot/blob/main/LICENSE) — ISC, Observable, Inc.
- [Observable Plot GitHub releases API](https://api.github.com/repos/observablehq/plot/releases) — v0.6.17, published 2025-02-14
- [@observablehq/plot npm registry](https://registry.npmjs.org/@observablehq/plot) — version/time cross-check
- [plot.umd.min.js via jsDelivr](https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6.17/dist/plot.umd.min.js) — size measured directly (209,183 bytes); header confirms `require("d3@7.9.0/dist/d3.min.js")` external dependency
- [D3 LICENSE](https://github.com/d3/d3/blob/main/LICENSE) — ISC, Mike Bostock
- [D3 GitHub releases API](https://api.github.com/repos/d3/d3/releases) — v7.9.0, published 2024-03-12
- [d3.min.js via jsDelivr](https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js) — size measured directly (279,706 bytes)
- [Mermaid LICENSE](https://github.com/mermaid-js/mermaid/blob/develop/LICENSE) — MIT, Knut Sveidqvist
- [Mermaid GitHub releases API](https://api.github.com/repos/mermaid-js/mermaid/releases) — mermaid@11.16.0, published 2026-06-25
- [mermaid npm registry](https://registry.npmjs.org/mermaid) — version/time cross-check
- [mermaid.min.js via jsDelivr](https://cdn.jsdelivr.net/npm/mermaid@11.16.0/dist/mermaid.min.js) — size measured directly (3,565,102 bytes); scanned for `fetch(`/`new Worker`/CDN URLs to confirm offline self-containment
- [Mermaid bundle-size discussion #4314](https://github.com/orgs/mermaid-js/discussions/4314) — history of bundle growth; basis for noting the "tiny" build alternative
- [Apache ECharts LICENSE](https://github.com/apache/echarts/blob/master/LICENSE) — Apache-2.0
- [Apache ECharts package.json](https://raw.githubusercontent.com/apache/echarts/master/package.json) / [README](https://github.com/apache/echarts/blob/master/README.md) — confirms `apache/echarts` org, Apache Software Foundation governance
- [Apache ECharts GitHub releases API](https://api.github.com/repos/apache/echarts/releases) — v6.1.0, published 2026-05-19
- [echarts npm registry](https://registry.npmjs.org/echarts) — version/time cross-check
- [echarts.min.js via jsDelivr](https://cdn.jsdelivr.net/npm/echarts@6.1.0/dist/echarts.min.js) — size measured directly (1,121,883 bytes)
- [ApexCharts license page](https://apexcharts.com/license/) — noted but not used as the control candidate: dual-licensed, OEM fee required above $2M annual revenue, which conflicts with jimemo's plain-permissive vendoring posture; Apache ECharts (Apache-2.0) used as the control instead
