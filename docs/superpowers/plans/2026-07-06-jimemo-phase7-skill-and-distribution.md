# jimemo Phase 7: Skill, Install, Design-System Decoupling, Distribution — Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Per-task review gates + whole-phase review + roborev. FINAL phase.

**Goal:** make jimemo installable + shareable, and cleanly separate the copyrighted design systems from the public tool repo. Do NOT flip the repo public — that's Joi's manual command (build everything up to it).

**Tracker:** kata xs31 (parent 9wk1). Branch: phase7. Base: main @ bcfb71e.

## Joi's directives (2026-07-06)
- Option 1: build all of Phase 7 but LEAVE THE REPO PRIVATE; Joi flips visibility manually.
- NEW ARCHITECTURE: design systems (Chiba Tech + future) are COPYRIGHTED — they must live OUTSIDE the public jimemo repo, in a separate private repo. The public repo must contain ZERO copyrighted design material (including the test fixture, which currently embeds Chiba's real token values/font names).

## Tasks

### Task 1: Decouple copyrighted design material from the public repo (DO FIRST — gates publishability)
- Replace `tests/fixtures/design-export/` (currently a trimmed copy of the REAL Chiba export — real colors like #4c4499, real "Finder"/"Ro NOW Std" family names) with a fully SYNTHETIC, fictional design export: invented brand (e.g. "Northwind"/"Acme Field"), invented namespace, invented color hex values, invented font family names (e.g. "Northwind Sans"), a manifest + tokens/*.css of the SAME STRUCTURE (so it exercises the reader/mapping/import code paths identically), including brandFonts + a couple of fonts + the token kinds the tests need. No real brand's values.
- Update every test that asserts fixture specifics (test_design_reader, test_design_mapping, test_import_design) to the synthetic brand's values, preserving equivalent COVERAGE (token counts/kinds, font mapping, accent pick, self-rank, etc.). The tests must assert the same BEHAVIORS, just against fictional data.
- Verify: full suite green against the synthetic fixture; `grep -ri 'chiba\|finder\|ro now\|4c4499' tests/ src/ docs/` returns nothing (no residual real-brand references in the repo). The real Chiba export is NOT in the repo.
- Deliverable: the public repo is provably free of copyrighted design content.

### Task 2: Private design-systems repo + the seam
- Create a NEW PRIVATE GitHub repo `Joi/jimemo-design-systems` (private is explicitly authorized; going-public of THAT repo is never intended). Move the real `/Users/joi/Downloads/Chiba Tech Design System` into it (as `chiba-tech/`), add a README explaining it holds real Claude Design exports for use with `jimemo import-design`, MIT-or-noted-copyright note (the exports themselves are their owners' copyright; the repo is private).
- The seam in jimemo (public): `import-design <path>` already accepts any dir. Add a documented convention: a default design-systems dir `~/.jimemo/design-systems/` (clone the private repo there). Optionally add light sugar `jimemo import-design --from <name>` resolving `<name>` against `~/.jimemo/design-systems/<name>/` (only if clean; the positional path already works — keep minimal). Document that design systems are bring-your-own and never bundled with the public tool.
- Clone/set up the private repo at `~/repos/jimemo-design-systems` and push Chiba there.

### Task 3: Agent skill (SKILL.md) + AGENTS.md
- `skill/SKILL.md`: thin wrapper over the CLI contract — the agent runs `jimemo suggest` / `list` → `info` → generate content matching the schema → `render` (or `render auto`) → optionally `publish`; covers `import-design` for brand themes; covers the stale-label refresh. All CLI+JSON so it's portable. Include the "design systems live in a separate private repo / bring your own export" note.
- `AGENTS.md` at repo root: the CLI contract for any harness (Codex etc.).

### Task 4: install.sh (multi-harness, one clone)
- `install.sh`: check python3; symlink the `jimemo` CLI to `~/.local/bin/jimemo`; symlink `skill/` into `~/.claude/skills/jimemo` (Claude Code + Cowork), `~/.codex/skills/jimemo`; register in the Amplifier bundle where present; `--uninstall` reverses. One clone, `git pull` updates everywhere (avoids the fresheyes 3-unsynced-copies problem — reference that lesson). Idempotent.
- Test: a dry-run / a temp-HOME run that asserts the symlinks are created to the right targets and --uninstall removes them. (Mirror the publish setup test style — inject HOME.)

### Task 5: README + CREDITS + friend install instructions + go-public prep
- README: top-level "what it is + install" (`git clone`, `./install.sh`), the full command tour (list/suggest/info/render/render-auto/new-template/import-design/publish/doctor), the two-repo design-systems note, the security posture summary (self-contained output, sanitized, no-view-time-fetch), MIT.
- CREDITS: finalize (vendored libs already listed; add any design-inspiration credits; note notes-ito-com origin for publish).
- A `docs/friends.md` (or README section): exact steps for a friend to install + optionally set up their own publish site + bring their own design export.
- go-public PREP but do NOT execute: verify no secrets in the repo (`git log`/tree scan — there shouldn't be any; the token invariants already hold), MIT LICENSE present, .gitignore complete; print the exact command for Joi: `gh repo edit Joi/jimemo --visibility public`. Leave the repo PRIVATE.

### Task 6: whole-phase review + acceptance
- Full suite green; `./install.sh` into a temp HOME works + uninstall; no copyrighted content in the repo (the Task-1 grep); no secrets; doctor clean; the skill contract is accurate to the CLI. Append phase summary + a "go public: run `gh repo edit ... --visibility public`" note to the ledger.

Whole-phase review + roborev, then merge to main. Repo stays private; Joi flips it.

## Out of scope
Actually flipping the repo public (Joi's command). A public design-systems marketplace. Auto-cloning the private repo (Joi clones it).
