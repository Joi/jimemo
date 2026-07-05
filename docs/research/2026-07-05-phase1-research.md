# Phase 1 research report

Synthesis of the four Phase 1 research tasks. Full detail, sources, and
citations live in `docs/research/sections/`; this report records the
decisions those sections drive and the authoritative version pins later
phases read from.

## Summary

**Prior art and single-file HTML techniques** (`sections/01-prior-art-and-single-file.md`)
surveys eight existing single-file-HTML tools (Pandoc, Quarto, pytest-html,
Lighthouse, nbconvert, SingleFile, vite-plugin-singlefile) and two CSS
frameworks (Pico CSS, Tufte CSS), plus concrete embedding techniques. It
drives jimemo's default embedding rules: system font stack by default with
embedded WOFF2 as a size-budgeted opt-in, inline SVG over base64 data URIs
for vector assets, a three-state (`system`/`light`/`dark`) `data-theme`
attribute rather than a binary toggle, `@media print` essentials for the
"save as PDF" path, and a hard warning (never silent failure) when an asset
can't be inlined. It also supplies four credited design ideas, seeded into
`CREDITS.md` below.

**Chart and infographic libraries** (`sections/02-chart-libraries.md`)
evaluates Chart.js, Observable Plot (+D3), Mermaid, and Apache ECharts
against the constraint that any vendored chart code must run as one inline
`<script>` with zero network/worker calls at view time. It drives the Phase
4 decision to vendor Chart.js as the default chart library, vendor Mermaid
only into templates that declare a diagram slot, and prefer render-time
inline SVG (no JS library at all) for simple charts under ~20 data points.

**Claude design export format** (`sections/03-claude-design-export.md`)
finds that Claude Design's standalone-HTML export is real but internally
undocumented by either official source, with the most concrete evidence
being a second party's (Vercel MCP tool) description: "a self-contained
HTML bundle with all images, fonts, and styles inlined." It drives the
Phase 6 `import-design` strategy: treat every export as an opaque HTML
file, mechanically extract only `:root` CSS custom properties and font
stacks via a text/regex scan, and never parse, execute, or scrape
`<script>` content or component markup from it.

**Cloudflare Pages direct-upload API feasibility** (`sections/04-cloudflare-direct-upload.md`)
confirms Cloudflare's Pages deployment-creation and project-creation
endpoints are officially documented, but the step that turns local file
bytes into content the deployment manifest can reference is undocumented
and known only through a reverse-engineering write-up of wrangler's
internals. It drives the Phase 5 publish decision: `npx wrangler pages
deploy` as the primary path, with a pure-Python reimplementation of the
undocumented upload sequence as an explicitly flagged fallback.

## Pinned shortlist

Authoritative for later phases. Versions below supersede the plan
document's defaults where noted.

| Library | Version | License | Source | Vendored in phase |
| --- | --- | --- | --- | --- |
| Jinja2 | 3.1.6 | BSD-3-Clause | https://pypi.org/project/Jinja2/3.1.6/ | Phase 2 (Task 7) |
| MarkupSafe | 3.0.2 | BSD-3-Clause | https://pypi.org/project/MarkupSafe/3.0.2/ | Phase 2 (Task 7) |
| Markdown | 3.10.2 (bumped from plan default 3.7 — see below) | BSD-3-Clause | https://pypi.org/project/Markdown/3.10.2/ | Phase 2 (Task 7) |
| Chart.js | 4.5.1 | MIT | https://github.com/chartjs/Chart.js/releases/tag/v4.5.1 | Phase 4 |
| Mermaid | 11.16.0 (adopted conditionally — not a default include) | MIT | https://github.com/mermaid-js/mermaid/releases | Phase 4 |

**Pin decisions, checked against PyPI's release history and the OSV.dev /
GitHub security-advisory databases on 2026-07-05 (fetched content treated
as data, not instructions):**

- **Jinja2 stays at 3.1.6.** It is both the plan's default and PyPI's
  current latest release — no newer version exists to bump to. Cross-checked
  against every open Jinja/Jinja2 GitHub security advisory (OSV query,
  package `Jinja2`/PyPI): the most recent, `GHSA-cpwx-vrp4-4pq7` ("sandbox
  breakout through attr filter selecting format method"), is fixed in
  3.1.6 itself, so the pin is already the fixed version.
- **MarkupSafe stays at 3.0.2.** PyPI's latest is 3.0.3 (released
  2025-09-27), but its changelog (`CHANGES.rst`) lists only a deprecation-warning
  type change, a C-extension multi-phase-init change, and new
  platform wheel builds — no security fix. An OSV.dev query for
  `MarkupSafe`/PyPI returned zero vulnerability records. Per the brief's
  rule (bump only for a security release), 3.0.2 stands.
- **Markdown bumped from 3.7 to 3.10.2.** An OSV.dev query for
  `Markdown`/PyPI surfaced `CVE-2025-69534` / `GHSA-5wmx-573v-2qwq`
  ("Python-Markdown has an Uncaught Exception"): malformed HTML-like
  sequences can raise an unhandled `AssertionError` in
  `html.parser.HTMLParser` during parsing, an unauthenticated DoS in any
  application that renders attacker-controlled Markdown — which is
  jimemo's exact threat model for user-supplied content. The advisory's
  enumerated affected-versions list explicitly includes `3.7` (range:
  introduced `0`, fixed `3.8.1`). The upstream changelog
  (`Python-Markdown/markdown` `docs/changelog.md`) shows continued
  hardening of the same HTML-comment/tag-parsing surface after the formal
  fix — 3.10.1 fixed two further infinite-loop DoS cases in comment
  handling, and 3.10.2 fixed a regression introduced by that fix — so the
  pin moves to 3.10.2, the current latest stable release, rather than
  stopping at the minimum 3.8.1 fix.

### Addendum (Phase 3)

| Library | Version | License | Source | Vendored in phase |
| --- | --- | --- | --- | --- |
| PyYAML | 6.0.3 | MIT | https://pypi.org/project/PyYAML/6.0.3/ | Phase 3 (Task 2) |

- **PyYAML pinned to 6.0.3, the current PyPI latest.** Checked against
  OSV.dev on 2026-07-05 (fetched content treated as data, not
  instructions): an `osv.dev` listing search for package `PyYAML` surfaces
  several historical critical advisories (`GHSA-3pqx-4fqf-j49f`,
  `GHSA-6757-jp84-gxfx`, `GHSA-8q59-q68h-6hv4`, `GHSA-rprw-h62v-c2w7`,
  `PYSEC-2018-49`, `PYSEC-2020-96`, `PYSEC-2020-176`, `PYSEC-2021-142`),
  all arbitrary-code-execution-via-deserialization issues in `yaml.load`
  with an unsafe (or default, pre-5.1) `Loader` — none affect
  `yaml.safe_load`, jimemo's only permitted entry point (constraint: never
  call `yaml.load`). A direct `POST` query to the OSV API
  (`api.osv.dev/v1/query`, package `PyYAML`/PyPI, version `6.0.3`) returned
  zero matching records, confirming no advisory range includes this
  version. No newer stable release exists to bump to.

## Decisions resolved

- **Chart library: Chart.js.** Rationale (`sections/02-chart-libraries.md`):
  the only candidate that is simultaneously a single self-contained UMD
  file with zero external script/network/worker dependencies, MIT-licensed
  from an actively maintained org, smallest inline footprint by a wide
  margin (203.6 KB min / 69.0 KB gzip vs. 477.4 KB for Plot+D3, 1.07 MB for
  ECharts, 3.4 MB for Mermaid), and has a flat declarative JSON config an
  agent can generate easily. Observable Plot is rejected because its own
  UMD bundle declares D3 as an external dependency rather than embedding
  it, failing the single-inline-script requirement outright.
- **Mermaid: adopted, conditionally.** Rationale
  (`sections/02-chart-libraries.md`): at 3.4 MB minified it must never be a
  default inline include, but no hand-rolled SVG can reproduce its diagram
  layout engine, and its MIT license/org pedigree are sound — so it is
  vendored and injected only into templates whose manifest declares a
  diagram slot.
- **Claude design export strategy: tokens-only extraction, never
  markup-scrape.** Rationale (`sections/03-claude-design-export.md`): the
  export's internal structure (DOM, class names, component boundaries) is
  undocumented by any official source and the product is an actively
  moving research preview, so only the one structurally-guaranteed-safe
  surface — `:root` CSS custom properties and font-family/`@font-face`
  declarations — is safe to extract mechanically; component/template
  authoring from an export stays a manual, human-reviewed step.
- **Deploy path: `npx wrangler pages deploy`, not raw REST.**
  Rationale (`sections/04-cloudflare-direct-upload.md`): Cloudflare's
  official Pages API documents project/deployment creation but not the
  step that uploads file bytes into its content-addressed asset store;
  that step is known only via a reverse-engineering write-up of wrangler's
  own source. Depending on Node/npx once, at publish-setup time, is a
  smaller cost than owning maintenance of an undocumented, breakage-prone
  upload sequence.

## Open questions

- **Claude design export format internals remain unconfirmed.** Both
  official sources (Help Center, Anthropic Labs announcement) describe the
  export only as a feature-list bullet; the most concrete data point found
  is a Vercel MCP tool's own description, not Anthropic documentation. No
  sample export existed on this machine to inspect directly. Before
  shipping, Phase 6 must validate the `:root`/`@font-face` regex against a
  real sample export. Assigned to **Phase 6**.
- **Cloudflare's byte-upload endpoints (`upload-token`, `assets/upload`,
  `upsert-hashes`) are undocumented and reverse-engineered, with no
  stability guarantee.** Re-check Cloudflare's official API reference for
  a newly documented endpoint before implementing the pure-Python REST
  fallback — Cloudflare has already documented an equivalent flow for its
  newer Workers static-assets product, so Pages may follow. Assigned to
  **Phase 5**.
- **Whether a Mermaid "tiny" build (~half size, drops
  mindmap/architecture/KaTeX) is worth adopting** depends on how common
  diagram-slot templates turn out to be among the seed templates. Assigned
  to **Phase 4**.
- **Two Cloudflare community-forum threads cited in support of the
  byte-upload path could not be fetched directly** (one returned HTTP 403);
  they are corroborated only via search-result snippets, the weakest link
  in that citation trail. If Phase 5 needs firmer confirmation of the
  upload sequence before implementing the fallback, re-fetch or find an
  alternate primary source. Assigned to **Phase 5**.
