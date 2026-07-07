# Architecture

Orientation for contributors. The module map and layout below is the
authoritative reference; each module's docstring documents its own
contract in detail.

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
    size). `lint_standalone` re-checks a file with no render context
    (the gate behind `check`, `pdf`, and `publish`).
  - `pdf.py` — `find_browser`/`render_pdf`: converts a rendered page to
    PDF by running a locally installed Chromium-family browser headless
    (Chart.js needs a real JS engine), through the same injectable-runner
    containment as the Wrangler seam; `PdfError` on any failure.
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
  Markdown, PyYAML, tomli) with `SHA256SUMS`; verified by `jimemo doctor`.
  tomli parses `~/.jimemo/config.toml` on the 3.9-3.10 floor, where
  stdlib `tomllib` isn't available yet (3.11+).
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
- `themes/` — repo-level theme token file overrides: a `<name>.css`
  `:root` block layered on top of `toolkit/tokens.css` by
  `assemble_css`'s `--theme NAME` resolution. `~/.jimemo/themes/` is the
  personal counterpart (mirroring `~/.jimemo/templates/` for personal
  templates) and is checked *first* — the opposite precedence from
  template discovery, where the repo copy wins a name collision: a
  theme a user just ran `jimemo import-design` against should take
  effect even if it collides with a repo theme's name, since applying
  the import is the entire point of running the command. Assembly
  order inside `assemble_css` is `tokens.css`, `base.css`, the
  manifest's components, then the resolved theme (if any), then
  `print-force.css` always last, so a theme's `:root` can override
  component defaults but never the print-force rules.
- `src/jimemo/design/` — parse-only import of a Claude-design export
  (a folder of design tokens/fonts a design-system Skill produces) into
  a jimemo theme; see `jimemo import-design --help` and the README's
  "Import a design" section.
  - `reader.py` — `read_export(export_dir) -> DesignExport`: reads
    `_ds_manifest.json` (preferred) or falls back to scanning
    `tokens/*.css` for `:root` custom properties. Only ever `json.load`s
    or regex-scans text; never opens, imports, or executes the export's
    `.js`/`.jsx`/`.ts` — the whole export is untrusted data. Every token
    value is passed through `validate_token_value` (rejecting `<`,
    braces, `expression(`, and any `url()`/bare value pointing anywhere
    but local or an allowlisted-mime `data:` URI) before it becomes
    part of a `DesignExport`, since it is destined to be dropped
    verbatim into generated theme CSS.
  - `mapping.py` — `build_theme(export, name) -> str`: deterministic,
    LLM-free token-to-role mapping. Re-declares every imported token
    verbatim under its own name, then maps a subset onto jimemo's
    `--jm-*` roles (font-prose/font-ui, accent/accent-contrast, text,
    bg, surface, muted, border, positive, negative) by name-keyword
    heuristics (with luminance as a tie-breaker for accent/grey
    choices), emitting `var(--source-token)` references rather than
    copied literals so hand-editing the raw token still moves the
    mapped role. A header comment documents what was auto-mapped and
    what needs manual review. Re-validates the assembled CSS against
    the same self-contained-page lint (`lint.css_reference_errors`)
    every other jimemo render output passes.
  - `importer.py` — `import_design(export_dir, name=, embed_fonts=)`:
    orchestrates reader → mapping → install to
    `~/.jimemo/themes/<name>.css` (via `inline.personal_themes_dir`).
    Fonts are family-name-only by default; `--embed-fonts` reads the
    export's actual font files, confines each path to the export
    directory (rejecting absolute paths or `..` traversal) and to a
    real font extension, base64-encodes it, and appends an `@font-face`
    with a `data:font/...` `src` — the one place in this package that
    reads bytes rather than text, and the one place that needs a
    path-traversal check. `DesignImportError` (`errors.py`) covers every
    failure mode across the three modules; the CLI (`cli.py`'s
    `cmd_import_design`) catches it, prints the message to stderr, and
    exits 1 — the same pattern as `ManifestError`/`ContentError`.
- `src/jimemo/config.py` — `load_config`: parses `~/.jimemo/config.toml`
  (vendored `tomli`) into a `Config`/`PublishConfig`; missing/invalid
  config raises `ConfigError` with a "run `jimemo publish setup`"
  message. Stores only non-secret identifiers (a command name, or a
  Cloudflare project/account/KV-namespace id + base URL) — never a
  token.
- `src/jimemo/publish/` — the publish subsystem: turns an already-
  rendered, self-contained HTML file into an unlisted private link,
  mirroring notes.ito.com's model (24-hex-hash path is the access
  control, symmetric read/purge, tombstone on purge).
  - `__init__.py` — the `Publisher` ABC (`publish`/`purge`/`list`/`gc`)
    and `get_publisher(config)`, which resolves `config.publish.backend`
    to one of two backends, importing each lazily so selecting one
    never pulls in the other's dependencies.
  - `staging.py` — `stage_page`: generates the 24-hex hash
    (`secrets.token_hex(12)`) and copies a rendered file to
    `<hash>/index.html`; used by the `cloudflare` backend only (the
    `command` backend delegates hashing/staging to the configured CLI).
  - `command_backend.py` — the `command` backend: shells out to a
    configured CLI (e.g. `notes-publish`) for publish/purge/list/gc and
    parses the published URL from its stdout. Keeps an existing site
    (like notes.ito.com) authoritative; jimemo is just a thin wrapper.
  - `wrangler.py` — the `Wrangler` seam: five narrow methods
    (`check_available`, `pages_deploy`, `kv_put`, `kv_get`, `kv_list`)
    wrapping `npx wrangler` subprocess calls, plus a `MockWrangler` for
    tests. Auth is never touched by jimemo — wrangler resolves its own
    `CLOUDFLARE_API_TOKEN` from the environment or its own credential
    store.
  - `cloudflare_backend.py` — the `cloudflare` backend: publishes by
    staging a hash directory into a persistent local state dir
    (`~/.jimemo/cloudflare/<project>/`) and redeploying that whole
    directory via the Wrangler seam (a Pages deploy replaces the entire
    production tree, so every previously published hash must stay
    present in the redeploy); purge/list/gc drive the tombstone KV
    namespace the same way. For someone without an existing publish
    site.
  - `setup.py` — the `jimemo publish setup` wizard (interactive and
    `--dry-run`): installs the bundled middleware/`_headers`/root index
    from `publish/cloudflare/` into the state dir, deploys it, walks the
    human through the two steps with no wrangler-CLI equivalent
    (creating the KV namespace, binding it to the Pages project as
    `TOMBSTONES`), and writes `~/.jimemo/config.toml` — never the API
    token. Full walkthrough: `docs/publish-setup.md`.
- `publish/cloudflare/` (repo root, distinct from `src/jimemo/publish/`)
  — `_middleware.js`, `_headers`, `index.html`: the Cloudflare Pages
  Functions bundle the `cloudflare` backend deploys. `_middleware.js` is
  a generalized port of notes-ito-com's tombstone/purge middleware (hash
  regex match, tombstone KV lookup → 404, `?purge` GET confirm + POST
  tombstone, Origin/Sec-Fetch-Site cross-site guard); credited in
  `CREDITS.md`.

**Publish subsystem boundary:** render (`render.py` and everything it
calls) never shells out or touches the network — the `vendor/`
constraint holds all the way through image inlining and lint. `publish/`
is the one place jimemo executes an external process (`wrangler` for
the `cloudflare` backend, the configured command for the `command`
backend), and only when a user explicitly runs `jimemo publish` or
`jimemo publish setup`; `jimemo render` never imports `publish/`.

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
