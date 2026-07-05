# jimemo Phase 3: Core Toolkit + Render/Info/Suggest + Seed Templates — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Executed task-by-task with per-task review gates and a final phase review.
>
> **Plan style note (deliberate deviation from full-code plans):** infrastructure tasks carry exact contracts and acceptance commands; design-heavy tasks (toolkit CSS, templates) carry binding interface contracts plus a quality bar, with implementation latitude — the review gate and golden tests enforce correctness, and rendered-output inspection enforces quality.

**Goal:** `jimemo render <template> <content.md>` produces a beautiful, self-contained single-file HTML page; `info`, `suggest`, `render auto`, and `new-template` complete the core CLI; five seed templates ship with suitability labels and golden tests.

**Tracker:** kata 7x84 (parent 9wk1). Spec: `docs/superpowers/specs/2026-07-05-jimemo-design.md`.

## Global Constraints

- Python ≥ 3.9; stdlib + `vendor/` only at runtime; pytest dev-only.
- All vendored additions: pinned, license shipped, SHA256SUMS updated, CREDITS.md row, OSV check recorded.
- Output: self-contained single-file HTML — no remote fetches at view time; lint enforces (hard-fail any `<script>` when the template declares no charts; hard-fail external script src always; warn external img/href; warn output > 8 MB).
- Jinja2 autoescape ON; slot types: `text` (escaped), `markdown` (vendored python-markdown), `data` (structured).
- Light/dark via `prefers-color-scheme` + `data-theme` override attribute; print stylesheet; system font stack (no webfonts in Phase 3).
- Run pytest / `./jimemo` with `PYTHONDONTWRITEBYTECODE=1`; `git status --porcelain vendor` clean before commit.
- Commits end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Binding contracts (all tasks)

**Manifest v1** (`templates/<name>/manifest.json`):
```json
{
  "name": "briefing",
  "version": 1,
  "title": "Briefing / memo",
  "description": "one line",
  "slots": {
    "title":    {"type": "text", "required": true},
    "date":     {"type": "text"},
    "body":     {"type": "markdown", "required": true},
    "sections": {"type": "data", "items": {"heading": "text", "body": "markdown"}}
  },
  "components": ["stat-tile", "card"],
  "charts": [],
  "suitability": {
    "keywords": ["briefing", "memo", "report"],
    "content_kinds": ["narrative"],
    "good_for": "one line",
    "labeled_hash": "<sha256 of template.html.j2>"
  }
}
```
`content_kinds` vocabulary (closed): `narrative`, `photo-heavy`, `tabular-data`, `chronological`, `hierarchical`.

**Content files:** `.md` = YAML frontmatter (slot values) + markdown body (fills the slot named `body`); `.json`/`.yaml` = object keyed by slot names. Unknown keys → validation error naming the key; missing required → error naming the slot.

**Module interfaces (exact names):**
- `jimemo.manifest.load_manifest(template_dir: Path) -> dict` — validated; raises `ManifestError(msg)` naming the field.
- `jimemo.content.load_content(path: Path, manifest: dict) -> dict` — parsed + validated slot values; raises `ContentError(msg)` naming the slot/key. Markdown slots are rendered to HTML here (python-markdown, extensions `["tables", "fenced_code"]`), returned as `markupsafe.Markup`.
- `jimemo.render.render_page(template_dir: Path, content: dict, theme: str | None) -> str` — full HTML string (assembled + inlined).
- `jimemo.render.write_output(html: str, out_path: Path) -> None`
- `jimemo.inline.assemble_css(manifest: dict, theme: str | None) -> str` — tokens.css + base.css + only the `components` listed + theme override file if given.
- `jimemo.inline.inline_images(html: str, base_dir: Path) -> tuple[str, list[str]]` — local `<img src>` → data URI; returns (html, warnings); missing files raise `ContentError` listing paths.
- `jimemo.lint.lint_html(html: str, manifest: dict) -> tuple[list[str], list[str]]` — (errors, warnings) per Global Constraints rules.
- `jimemo.suggest.score_templates(content_path: Path, templates: list) -> list[dict]` — sorted desc: `{"name", "score", "reasons": [str], "stale_labels": bool}`.
- Errors print to stderr, exit 1 (`ContentError`/`ManifestError` → message, no traceback).

**CLI additions:** `info <template> [--json]`, `render <template|auto> <content> [-o PATH] [--theme NAME] [--open]` (default out `dist/<content-stem>.html`), `suggest <content> [--json]`, `new-template <name>`. `doctor` gains stale-label reporting.

**Toolkit component set (Task 3; exact kebab names double as manifest `components` entries and CSS filenames):** `page-header`, `stat-tile`, `card-grid`, `timeline`, `data-table`, `figure-block`, `badge`, `toc`, `tree`. Each = `toolkit/components/<name>.css` + Jinja2 macro in `toolkit/macros.html.j2` (imported by templates via `{% import %}`; loader search path includes `toolkit/` and the template dir).

**Golden tests:** `tests/test_golden.py` renders each template's `sample/` and compares to `tests/goldens/<name>.html`. `JIMEMO_UPDATE_GOLDENS=1 python3 -m pytest tests/test_golden.py` rewrites goldens. Sample images: tiny checked-in PNGs (< 2 KB) so goldens are byte-stable.

---

### Task 1: Carry-forward hardening
**Files:** `src/jimemo/checksums.py`, `src/jimemo/cli.py`, `src/jimemo/discovery.py`, `src/jimemo/_paths.py` (new), `.github/workflows/ci.yml`, tests.
Fold in the deferred phase-1-2 findings: (a) malformed SHA256SUMS line → `problems.append(f"malformed SHA256SUMS line: {line!r}")`, never a traceback (+test); (b) annotations `-> list[str]` / precise types across checksums & discovery; (c) `find_templates`: wrap `root.iterdir()` in try/except OSError → skip dir with a stderr warning (+test using chmod 000, skip on root); (d) `--version` → `action="version"`, `version=f"jimemo {__version__}"` (adjust test); (e) new `src/jimemo/_paths.py` with `REPO_ROOT` consumed by `_vendor.py` and `discovery.py` (single source); (f) CI: `permissions: contents: read`, actions pinned to full commit SHAs (look up current v4/v5 SHAs), pytest pinned (`pytest==8.*` acceptable floor form: exact pin).
**Accept:** full suite green; `./jimemo doctor` unchanged behavior; `./jimemo --version` prints `jimemo 0.0.1`.

### Task 2: Vendor PyYAML (pure Python)
Latest stable PyYAML from PyPI (verify on PyPI + OSV — record decision in `docs/research/2026-07-05-phase1-research.md` shortlist as an addendum row). sdist → copy `lib/yaml/*.py` ONLY (exclude `_yaml`/C ext) → `vendor/yaml/` + LICENSE (MIT) + SHA256SUMS regen + CREDITS row. Import check `yaml.safe_load` from vendor. ONLY `safe_load` is ever used (constraint: never `yaml.load`).
**Accept:** doctor ok; purity find = 0; suite green.

### Task 3: Toolkit — tokens, base, components, macros
**Files:** `toolkit/tokens.css`, `toolkit/base.css`, `toolkit/components/*.css` (the 9 named), `toolkit/macros.html.j2`, `toolkit/README.md` (token reference).
Tokens: full CSS-custom-prop system (`--jm-*`): color roles (bg, surface, text, muted, accent, accent-contrast, border, positive, negative), type scale (1.25 ratio, `--jm-text-{xs..3xl}`), spacing scale, radius, shadow, content max-width. Light + dark values (`prefers-color-scheme` and `:root[data-theme=…]` both honored, explicit attr wins). Base: modern reset, prose typography (system stack), print styles (light theme forced, no shadows, page margins). Quality bar: the *frontend-design skill's* standards — deliberate typographic hierarchy, restrained palette, generous whitespace; no framework look-alikes. Components must render well at 360 px and in print.
**Accept:** a throwaway HTML file using every component renders correctly in light/dark (implementer verifies by opening it; screenshot via `agent-browser` if available); CSS contains no `@import`/`url(http`.

### Task 4: Render pipeline + `render` CLI + golden harness
**Files:** `src/jimemo/{render,inline,lint,manifest,content,errors}.py`, `src/jimemo/cli.py`, `tests/test_{manifest,content,render,lint}.py`, `tests/test_golden.py` harness (no goldens yet).
Implement the module interfaces above. Jinja2 `Environment(loader=FileSystemLoader([template_dir, TOOLKIT_DIR]), autoescape=True, undefined=StrictUndefined)`. CSS assembly inlines into one `<style>`. Renders fail closed: lint errors → exit 1 and no output file; warnings print to stderr but write proceeds.
**Accept:** unit tests incl.: missing required slot names the slot; unknown key named; external `<script src>` in a test template → lint error, no file written; local img → data URI; missing img → error listing path. Full suite green.

### Task 5: `info` + `new-template`
`info`: human view (slots table, components, suitability, sample path) and `--json` (manifest verbatim + `template_dir` + `sample_files`). `new-template <name>`: scaffolds `~/.jimemo/templates/<name>/` (manifest with empty suitability + TODO good_for, minimal template extending toolkit base block structure, sample content) — scaffold must render out of the box.
**Accept:** `./jimemo new-template zine && ./jimemo render zine <its sample> -o /tmp/z.html` succeeds; `info briefing --json | python3 -m json.tool` clean (after Task 6+ templates exist, test with scaffold).

### Task 6: `suggest` + `render auto` + stale labels
Deterministic scorer, no LLM: content signals — image refs count, ISO-date count, top-level array-of-records shape, nesting depth ≥ 3 (hierarchical), word count buckets; keyword overlap (case-folded) with `suitability.keywords`; content_kind priors (e.g. ≥ 4 images → photo-heavy +3). Weights are named constants with a comment table. `stale_labels` = sha256(template.html.j2) ≠ labeled_hash → score × 0.8 + reason "labels stale". `suggest` prints top 3 with reasons; `render auto` uses argmax (tie → alphabetical first, say so). `doctor` lists stale-label templates as warnings (not failures).
**Accept:** each seed template's own sample ranks its template #1 (test asserts, added in Task 12); stale-hash test.

### Tasks 7–11: Seed templates (parallelizable, no-git — controller commits)
Each: `templates/<name>/{template.html.j2, manifest.json, sample/}` + golden + a suggest-ranking expectation. All extend the toolkit (import macros; declare only used components). Hand-written suitability incl. correct `labeled_hash`. Samples must be REAL-feeling content (no lorem ipsum).
- **7 briefing** — memo/briefing: title/date/summary/sections/optional stat row. kinds: narrative.
- **8 photo-catalog** — card grid w/ figure blocks, captions, optional per-item fields table; sample: 6-item plant catalog (tiny placeholder PNGs). kinds: photo-heavy.
- **9 timeline** — vertical timeline of dated events w/ optional images/badges. kinds: chronological.
- **10 genealogy** — tree component; data slot = nested persons (name, years, note). kinds: hierarchical.
- **11 data-dashboard** — Phase-3 version: stat tiles + data tables + figure blocks, NO JS charts (Phase 4 adds). kinds: tabular-data.

### Task 12: Integration + goldens + docs
Generate all goldens; add suggest self-ranking test (each sample → own template #1); `jimemo list` now shows 5 templates + scratch; update `docs/architecture.md` (modules) and README (real usage example); run every CLI command end-to-end; append phase summary to ledger.
**Accept:** full suite green (goldens stable across two runs); `./jimemo render briefing templates/briefing/sample/*.md -o dist/demo.html && open dist/demo.html` produces a page worth showing Joi.

---

Phase review after Task 12: whole-phase diff review (most capable model) incl. rendered-output quality pass, then merge to main per finishing flow.
