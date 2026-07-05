# Architecture

Orientation for contributors. The authoritative design is the spec:
`docs/superpowers/specs/2026-07-05-jimemo-design.md`; task-by-task
contracts are in `docs/superpowers/plans/2026-07-05-jimemo-phase3-core.md`.

- `jimemo` (repo root) — CLI entry point; puts `src/` and `vendor/` on
  `sys.path`. Users never pip-install anything.
- `src/jimemo/` — CLI implementation:
  - `cli.py` — argparse entry point; wires the `doctor`, `list`, `render`,
    `info`, `new-template`, `suggest` subcommands to the modules below.
  - `manifest.py` — `load_manifest`: parses and validates a template's
    `manifest.json` against the Manifest v1 schema.
  - `content.py` — `load_content`: parses a `.md`/`.json`/`.yaml` content
    file against a manifest's slots; renders `markdown`-typed slots to
    sanitized HTML.
  - `render.py` — `render_page`/`write_output`: Jinja2 render, then image
    inlining, then lint, fail-closed on lint errors.
  - `inline.py` — `assemble_css`/`inline_images`: concatenates the
    toolkit CSS a template declares into one `<style>`, and turns local
    `<img src>` references into data URIs.
  - `lint.py` — `lint_html`: post-render static checks (no remote
    fetches, no scripts unless the template declares charts, output
    size).
  - `sanitize.py` — `sanitize_html`: stdlib allowlist sanitizer for
    markdown-rendered slot content (untrusted input may carry raw HTML).
  - `charts.py` — `build_chart_config`/`serialize_chart_config`: builds
    a Chart.js config dict from a manifest chart declaration + the
    content's `{labels, series}` data slot, applying the dataviz
    palette, then serializes it with `json.dumps` and escapes every
    `<` so the result cannot break out of the `<script>` element it is
    embedded in.
  - `suggest.py` — `score_templates`: deterministic, LLM-free template
    suitability scoring from content signals; backs `suggest` and
    `render auto`.
  - `scaffold.py` — `create_template`: scaffolds a new personal template
    under `~/.jimemo/templates/<name>/` for `new-template`.
  - `errors.py` — `ManifestError`/`ContentError`/`ScaffoldError`: domain
    errors the CLI prints as a plain message (no traceback), exit 1.
  - `discovery.py`, `checksums.py`, `_paths.py`, `_vendor.py` — template
    discovery, vendor checksum verification, and `sys.path` setup
    (carried over from Phases 1-2).
- `vendor/` — pinned pure-Python dependencies (Jinja2, MarkupSafe,
  Markdown, PyYAML) with `SHA256SUMS`; verified by `jimemo doctor`.
- `charts/vendor/chartjs/` — vendored browser-side Chart.js
  (`chart.umd.min.js` + `LICENSE.md`), pinned and checksummed like
  `vendor/` but kept in its own tree with its own `SHA256SUMS` since
  it's JS the browser runs, not Python `import`ed at CLI runtime;
  `verify_checksums` covers both trees and `jimemo doctor` reports the
  pinned version (`charts vendored (chart.js X.Y.Z)`).
- `toolkit/` — the shared design system every template extends:
  `tokens.css` (CSS custom properties), `base.css` (reset, typography,
  print), `components/<name>.css` (one file per toolkit component,
  including `chart-block.css` for the chart-dashboard layout),
  `macros.html.j2` (the matching Jinja2 macro for each component,
  including the `chart(id, init_js)` macro — the only macro that
  emits a `<script>`), `page.html.j2` (the base template every
  seed/personal template extends).
- `templates/<name>/` — a template is a folder: `template.html.j2`,
  `manifest.json`, `sample/` (real-feeling sample content the golden
  tests render). Six seed templates ship in the repo: `briefing`,
  `chart-dashboard`, `data-dashboard`, `genealogy`, `photo-catalog`,
  `timeline`. Personal templates live in `~/.jimemo/templates/` and
  are discovered alongside the repo's own.
- `tests/goldens/<name>.html` — one golden render per seed template's
  sample, compared byte-for-byte by `tests/test_golden.py`;
  `JIMEMO_UPDATE_GOLDENS=1 python3 -m pytest tests/test_golden.py`
  regenerates them.
- `themes/`, `publish/` — theme token file overrides and generalized
  private-link publishing (later phases).

**Chart security model:** a template that declares `charts` in its
manifest lets `lint.py` reopen exactly one door it otherwise keeps
shut for every template — an inline, src-less `<script>` — but only
for the exact script BODIES the renderer emits: the vendored Chart.js
library, byte-compared against the pinned bundle, and one init per
declared chart in exactly `charts.chart_init_js`'s shape (declared id,
JSON config with no raw `<`). `render.py` builds each init body via the
same `chart_init_js`, so the text lint accepts and the text the macro
emits have one source of truth; any other inline script — even on a
chart page — is a hard error, so a shared third-party template cannot
ride a chart declaration to smuggle its own JavaScript. Everything
placed in the config is config-as-data: `charts.py` builds a plain
dict from validated content, `json.dumps`-serializes it, and escapes
every `<` so untrusted labels or values can never terminate the script
element or start a new one. `src`, `on*` handlers, `javascript:`, and
remote resources are still hard lint errors on every template,
chart-bearing or not.
