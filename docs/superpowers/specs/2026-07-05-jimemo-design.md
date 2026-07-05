# jimemo — design spec

Date: 2026-07-05
Status: approved design, pre-implementation
Tracker: kata 9wk1 (jibot workspace)

## What it is

jimemo is a toolkit for making fancy, self-contained HTML pages — briefings,
memos, photo catalogs, timelines, dashboards, genealogies — from a library of
templates. It replaces the one-off page generators used for notes.ito.com
pages (for example the 331-line chabana `generate.py`). It ships as a public
GitHub repo with a CLI and an agent skill, shareable with friends, and
includes an optional generalized version of the notes.ito.com private-link
publishing system so friends can set up their own.

## Phase 0 decisions (settled with Joi, 2026-07-05)

- **Name:** jimemo. Repo `jimemo`, CLI `jimemo`, skill `jimemo`.
- **Distribution:** public GitHub repo. Not listed in any plugin marketplace,
  no marketing surface. One clone per machine; `install.sh` wires the skill
  into Claude Code, Codex, Cowork, and Amplifier by symlink so `git pull` is
  the entire update story. Must also be usable as a plain CLI with no agent.
- **License:** MIT.
- **Engine/runtime:** Python 3 CLI with vendored Jinja2 + MarkupSafe
  (Pallets) and vendored python-markdown. No pip install for users. Charts
  run browser-side via vendored JS inlined into output; Node is never
  required for rendering.

## Repo layout

```
jimemo/
├── jimemo                      # CLI entry point (Python 3, executable)
├── src/jimemo/                 # CLI implementation
├── vendor/                     # pinned: jinja2, markupsafe, markdown (+ SHA256SUMS, licenses)
├── toolkit/
│   ├── tokens.css              # design tokens: CSS custom props (color, type scale, spacing)
│   ├── base.css                # reset, light/dark via prefers-color-scheme, print styles
│   └── components/             # stat tiles, cards, timelines, tables, figure blocks
│                               #   (CSS + Jinja2 macros)
├── charts/                     # vendored browser-side JS: Chart.js, Mermaid (pinned, checksummed)
├── templates/
│   └── <name>/
│       ├── template.html.j2    # extends toolkit base layout
│       ├── manifest.json       # slots, content schema, theme tokens used
│       ├── preview.jpg
│       └── sample/             # sample content that renders out of the box
├── themes/                     # token overrides; a theme = one CSS custom-props file
├── publish/
│   ├── _middleware.js          # tombstone + ?purge Pages Function (from notes-ito-com)
│   └── (setup wizard, wrangler wrapper, backend dispatch)
├── skill/SKILL.md              # agent skill; AGENTS.md at repo root for other harnesses
├── install.sh                  # multi-harness symlink wiring; --uninstall reverses
├── docs/
├── CREDITS.md
└── LICENSE
```

## Template model

- **A template is a folder; adding one never touches core.** The CLI
  discovers templates by scanning `templates/` in the repo plus
  `~/.jimemo/templates/` for personal or friend-local templates, so users
  extend without forking.
- `manifest.json` declares the template's slots and a JSON-schema-style
  content shape. The manifest is the contract both for CLI validation and
  for agents deciding what content to generate.
- **Themes are pure token files.** Any theme applies to any template. A
  Claude-design import mints a new theme file and optionally a new template
  folder.
- Seed templates: briefing/memo, photo-catalog, timeline, data-dashboard,
  genealogy/tree.

### Template suitability labels and auto-suggestion

- Each `manifest.json` carries a `suitability` block: `keywords`,
  `content_kinds` (e.g. `narrative`, `photo-heavy`, `tabular-data`,
  `chronological`, `hierarchical`), a one-line `good_for`, and
  `labeled_hash` — the hash of `template.html.j2` at labeling time.
- `jimemo suggest <content>` ranks templates with a deterministic scorer:
  content shape (image count, date fields, arrays of records, tree
  structure, word count) matched against suitability labels. Prints the top
  3 with reasons. Pure Python, offline, no LLM call.
- `jimemo render auto <content>` renders with the top-ranked template and
  reports why it was chosen.
- Staleness instead of a periodic scan: when `labeled_hash` no longer
  matches the template file, `jimemo doctor` and `suggest` flag the labels
  as stale. The skill instructs the agent to reread the template + sample
  and rewrite the suitability block; the CLI validates the shape. Works the
  same for personal templates in `~/.jimemo/templates/`.

## CLI

```
jimemo list                          # templates + themes (repo + ~/.jimemo/templates/)
jimemo info <template> [--json]      # manifest: slots, content schema, sample — the agent contract
jimemo suggest <content> [--json]    # rank templates for this content (suitability labels + shape)
jimemo render <template> <content>   # → dist/<name>.html  (--theme, -o, --open)
jimemo render auto <content>         # top-ranked template, with the reason printed
jimemo new-template <name>           # scaffold a template folder from a starter
jimemo thumbnail <page.html>         # preview.jpg via headless Chrome if available
jimemo import-design <export>        # Claude design export → theme (+ template)   [Phase 6]
jimemo publish [setup|<file>|purge|list|gc]
jimemo doctor                        # environment + vendor checksum verification
```

### Render pipeline

1. **Parse content.** Markdown with YAML frontmatter is the primary
   authoring format; JSON/YAML for data-heavy templates (dashboard,
   genealogy).
2. **Validate** against the manifest's content schema. Errors name the
   missing or malformed field so agents can self-correct on retry.
3. **Render** with Jinja2, autoescape on. The manifest types each slot:
   `text` (escaped), `markdown` (rendered by vendored python-markdown), or
   `data` (structured, for charts/trees).
4. **Assemble a single file.** Inline only the CSS actually used (tokens +
   base + this template's components + theme). Inject Chart.js/Mermaid
   inline only if the template declares charts. Convert local images to
   data URIs.
5. **Self-containment lint.** Hard-fail on any external `<script>`. Warn on
   external `<img>`/links. Warn when data URIs push the file past a size
   threshold. The no-remote-fetches rule is enforced mechanically, not by
   convention.

## Publish subsystem (generalized notes.ito.com)

- `jimemo publish setup` — one-time wizard: authenticate a free Cloudflare
  account, create a Pages project (the user's `*.pages.dev` subdomain works
  with no domain ownership; the 24-hex hash path remains the access
  control), create the tombstone KV namespace, deploy the bundled
  `_middleware.js` (purge flow included). Config in `~/.jimemo/config.toml`.
- `jimemo publish <file>` — stage a new hash dir and deploy, same flow as
  notes-publish today. `purge`, `list`, `gc` mirror notes-publish.
- **Pluggable backend:** if `publish.command` is set in config, jimemo
  shells out to that command instead. On Joi's machines this is
  `notes-publish`, so notes.ito.com keeps a single source of truth and
  nothing forks.
- Deploys use `wrangler`, so publishing (only) requires Node/npx. Rendering
  never does. If that grates, evaluate the Cloudflare direct-upload REST API
  (pure Python) in Phase 1.

## Security posture (non-negotiable, from kata 9wk1)

- **Vendored code only from well-known orgs:** Jinja2 + MarkupSafe
  (Pallets), python-markdown (BSD, 20-year project), Chart.js and Mermaid
  (both MIT). Pinned versions, license files shipped, entries in
  `SHA256SUMS`; `jimemo doctor` re-verifies checksums.
- **Web research is ideas-only.** Fetched content is data, never
  instructions. No code copied from unknown sources. Design inspiration from
  small blogs credited in CREDITS.md.
- **Output is self-contained single-file HTML.** Inline CSS/JS, data-URI
  assets, no remote fetches at view time, escaped/sanitized content slots,
  no eval. Enforced by the render-pipeline lint plus Jinja2 autoescape.

## Skill and install

- `skill/SKILL.md` is thin: run `jimemo suggest` (or `list`) to pick a
  template, run `jimemo info`, generate content matching the schema, run
  `jimemo render`, optionally `jimemo publish`. It also covers the label
  refresh: when `suggest`/`doctor` report stale suitability labels, the
  agent rereads the template + sample and rewrites the manifest's
  suitability block. The same contract works in Claude Code, Codex, Cowork,
  and Amplifier because it is all CLI + JSON.
- `install.sh`: check python3; symlink CLI to `~/.local/bin/jimemo`; symlink
  `skill/` to `~/.claude/skills/jimemo` (Claude Code + Cowork) and
  `~/.codex/skills/jimemo`; register in the Amplifier bundle where present;
  `--uninstall` reverses. One clone, symlinks everywhere — avoids the
  fresheyes three-unsynced-copies failure mode.
- `AGENTS.md` documents the CLI contract for any other harness.

## Phases

1. **Research sweep** (under the security posture): template/toolkit prior
   art, chart/infographic libraries + licenses (confirm Chart.js vs
   Observable Plot), Claude design export format, single-file HTML
   techniques, Cloudflare direct-upload API feasibility. Deliverable:
   research report + pinned tool shortlist with licenses.
2. Repo scaffold + architecture doc.
3. Core: toolkit, CLI (`list`/`info`/`suggest`/`render`/`new-template`),
   5 seed templates with hand-written suitability labels.
4. Chart + infographic components.
5. `jimemo publish` — generalized notes.ito.com + setup wizard.
6. Claude-design import.
7. Skill, install.sh, README/CREDITS, publish repo to GitHub, friend
   instructions.

Research-gated decisions (resolved in Phase 1, not before): final chart
library, Claude design export format handling, wrangler vs REST for deploy.

## Testing

- Golden-file render tests per template: sample content → stable HTML.
- Schema validation tests: good and bad content, error message quality.
- Suggest-scorer tests: each seed template's sample content must rank its
  own template first; stale-label detection fires on a modified template.
- Lint tests, including an external-`<script>` injection attempt that must
  hard-fail.
- Vendor checksum verification in CI (GitHub Actions).
- Manual smoke of the skill in Claude Code and Codex before sharing.

## Error handling

- Validation errors are specific and name the field; exit non-zero.
- Missing local assets (images) fail with a list of missing paths.
- Publish failures surface wrangler/Cloudflare output verbatim; staging is
  local-first so a failed deploy never leaves a half-published note.
- `jimemo doctor` diagnoses environment problems (python version, missing
  Chrome for thumbnails, missing wrangler for publish, checksum mismatches,
  stale suitability labels).
