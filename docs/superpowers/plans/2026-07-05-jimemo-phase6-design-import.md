# jimemo Phase 6: Claude-Design Import — Implementation Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Per-task review gates + whole-phase review + roborev before merge.

**Goal:** `jimemo import-design <export-dir>` reads a Claude Design export and produces a jimemo **theme** (a `--jm-*` token override file) that `jimemo render --theme <name>` can apply — parse-only, never executing export code. Fonts map to family + fallback by default (embed opt-in). Validated against a real export (Chiba Tech Design System).

**Tracker:** kata j4dh (parent 9wk1). Branch: phase6. Base: main @ f3616e5.

## The real export format (grounded in the fixture, not the Phase-1 guess)
The fixture `/Users/joi/Downloads/Chiba Tech Design System` is a STRUCTURED export, not a single HTML:
- `_ds_manifest.json` — the clean source of truth. Keys used: `tokens` (list of `{name, value, kind, definedIn}` — kind ∈ color/font/spacing/typography/…), `fonts` (`{family, weight, style, cssPath, files[]}`), `brandFonts` (`{family, status, tokens[], path}` — tells which tokens reference which family), `globalCssPaths`, `themes` (may be empty), `namespace`.
- `tokens/{colors,typography,spacing,fonts}.css` — the human-readable `:root { --ns-*: value }` + `@font-face` forms (same values as the manifest).
- `uploads/` + `assets/fonts/` — real font files (.ttf/.otf), often licensed.
- `_ds_bundle.js`, `*.jsx`, `*.ts`, `_adherence.oxlintrc.json`, SKILL.md, guidelines/components/slides/templates — IGNORE for import; NEVER execute the JS/TS.

## Security posture (non-negotiable)
- **Parse-only.** Read `_ds_manifest.json` (JSON) and `tokens/*.css` (text). NEVER execute or import `_ds_bundle.js`/`.jsx`/`.ts` or any export code. Treat the whole export as untrusted DATA.
- Token VALUES are untrusted: a color/spacing value goes into the generated theme CSS, so validate/sanitize — reject values containing `<`, `}`, `;`-injection, `url(` with remote/non-data targets, `expression(`, `javascript:` (a malicious export could put `red; } body{...` or `url(https://evil)` in a token value). The generated theme must pass the SAME Phase-3 self-contained lint (no remote url/@import) when a page using it is rendered. Reuse the sanitizer/lint helpers.
- Generated theme is a plain `:root` custom-props CSS file — no script, no @import, no remote url().

## Global Constraints
- Python 3.9 floor; stdlib + vendor/ only; PYTHONDONTWRITEBYTECODE=1.
- Fonts: do NOT commit the licensed .ttf/.otf into the repo. The checked-in test fixture = the manifest + token CSS (text) only, under tests/fixtures/design-export/ (a trimmed copy, no font binaries). The full folder is referenced for manual verification.
- Commits end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Tasks

### Task 1: Export reader (parse-only) + trimmed fixture
- `src/jimemo/design/reader.py`: `read_export(export_dir: Path) -> DesignExport` — locate + parse `_ds_manifest.json` (preferred); if absent, fall back to parsing `tokens/*.css` / `globalCssPaths` for `:root` custom props (defensive, for exports lacking a manifest). Returns a normalized structure: tokens (name/value/kind), fonts (family/weight/files), brandFonts (family→referencing tokens). NEVER reads/opens `.js/.jsx/.ts`. Raise DesignImportError (new, in errors.py) on a malformed/absent manifest+css.
- Value sanitization: each token value validated (reject `<`, unbalanced `}`, `url(` non-data/remote, `expression(`, `javascript:`/`vbscript:`) → DesignImportError naming the bad token. (Reuse sanitize/lint scheme helpers where possible.)
- Fixture: create tests/fixtures/design-export/ = a trimmed copy of the Chiba Tech export's `_ds_manifest.json` + `tokens/*.css` (NO font binaries — strip/ië the uploads). Small, checked-in, license-safe.
- Tests: reads the fixture manifest → correct token count/kinds; the css-fallback path (delete manifest in a tmp copy) still extracts :root tokens; a token value with `url(https://evil)` → DesignImportError; a `.js` file in the export is never opened (assert reader doesn't touch it).

### Task 2: Token → jimemo theme mapping
- `src/jimemo/design/mapping.py`: `build_theme(export: DesignExport, name: str) -> str` producing a jimemo theme CSS string: a `:root` block that (a) re-declares the imported raw tokens verbatim (namespaced, e.g. `--ct-*`), and (b) MAPS them onto jimemo's semantic `--jm-*` roles via deterministic heuristics:
  - font: from brandFonts (the family whose status="ok" and referenced by the most tokens, or the one tied to a `--*-font` token) → `--jm-font: "<family>", <safe fallback stack>`.
  - accent: a token named like `*-accent`/`*-primary`/`*-core`/`*-brand` (color kind) → `--jm-accent`.
  - text/bg/surface/border/muted: map by name heuristics (black/ink→text, white/bg→bg, surface/grey-light→surface, grey→muted, a light grey→border) — best-effort, documented.
  - positive/negative: green→positive, red→negative if present.
  Anything unmapped stays available as its raw `--ct-*` token. Emit a header comment listing what was auto-mapped and what the user should review.
- Deterministic (no LLM); mappings are named heuristics with a comment table. The SKILL (Phase 7) can layer agent refinement, but the CLI is deterministic.
- Tests: the Chiba fixture → --jm-font maps to Finder with a fallback; --jm-accent maps to --ct-blue-core (or the brand's primary); positive/negative map to green/red; the raw tokens are all present; the output is valid CSS with balanced braces and no remote url/@import (passes the Phase-3 lint).

### Task 3: `jimemo import-design` CLI + theme install + font handling
- `jimemo import-design <export-dir> [--name NAME] [--embed-fonts]`:
  - read → build theme → write to `themes/<name>.css` in the repo templates area OR `~/.jimemo/themes/<name>.css` (personal; mirror the personal-templates dir). Decide: personal `~/.jimemo/themes/` (so a friend's import doesn't dirty the repo) — and make `--theme` resolution (Phase 3 assemble_css) also look in `~/.jimemo/themes/`. If assemble_css only reads repo themes/, extend it to also read ~/.jimemo/themes/ (small, mirror discovery's personal dir).
  - Fonts: default = family + fallback in the theme (no binaries). `--embed-fonts` = read the export's font files, base64 data-URI them into `@font-face` in the theme (self-contained but large) — validate they're font files by extension, confine to the export dir (no traversal), and WARN about size + licensing ("only embed fonts you're licensed to redistribute"). Never copy font binaries into the repo.
  - print what was mapped + the theme path + how to use it (`jimemo render <template> <content> --theme <name>`).
- Tests: import the fixture → themes/<name>.css written; `jimemo render briefing <sample> --theme <name>` produces a page using the imported font family + accent (grep the output for the brand font family + accent color); --embed-fonts path (with a tiny fake font file in a tmp export) inlines an @font-face data: URI and warns; a font path escaping the export dir → rejected.

### Task 4: docs + acceptance
- README: an "Import a design" section — `jimemo import-design <export> --name mybrand` then `--theme mybrand`; parse-only/never-executes-export-code; fonts family-by-default / --embed-fonts opt-in + licensing note; where themes live.
- architecture.md: the design/ package (reader, mapping), theme resolution incl. ~/.jimemo/themes/.
- Acceptance: full suite green; import the trimmed fixture end-to-end; render a seed template with the imported theme and confirm the brand identity shows (font + accent); the generated theme passes the self-contained lint; doctor clean; import jimemo.cli loads no vendored python.
- MANUAL verification note: import the FULL Chiba Tech folder (with fonts, --embed-fonts) and eyeball a rendered page — documented for Joi.
- Append phase summary to the SDD ledger.

## Out of scope (defer)
Importing components/slides/templates (only tokens+fonts → theme this phase; the export's React components aren't jimemo templates). Multi-theme exports (`themes[]` non-empty) — handle the single-theme case; note multi-theme as a follow-up. Auto light/dark derivation from a single brand palette.

Whole-phase review (parse-only enforcement, value sanitization, font traversal/licensing) + roborev, then squash-merge to main. Then Phase 7 (skill/install/go-public) is the last phase.
