# jimemo Phase 5: Publish Subsystem (generalized notes.ito.com) — Implementation Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Per-task review gates + whole-phase review + roborev before merge.

**Goal:** `jimemo publish` turns a rendered page into an unlisted private link. Two backends: a `command` passthrough (delegates to any publish CLI — keeps `notes-publish`/notes.ito.com authoritative on Joi's machines) and a native `cloudflare` backend (a friend provisions their own free Cloudflare Pages + tombstone KV via a setup wizard). Mirrors notes.ito.com's model: a 24-hex-hash path is the access control, symmetric read/purge, click-confirm, tombstone middleware.

**Tracker:** kata k1tt (parent 9wk1). Branch: phase5. Base: main @ 9087a06.
**Source to generalize:** `~/repos/notes-ito-com/` — `functions/_middleware.js` (tombstone/purge), `bin/notes-publish` (publish/purge/list/gc subcommands), hash-dir staging.

## Testing boundary (honest scope)
- The `command` passthrough backend is FULLY testable — unit tests + a real end-to-end publish via `notes-publish` to notes.ito.com (Joi's own, purgeable).
- The native `cloudflare` backend's LIVE deploy needs a friend's Cloudflare API token (Pages:Edit + KV:Edit) and creating a CF account/project — which cannot be automated in this run (no account creation, no credential entry). So: build it behind a `Wrangler`-runner seam, unit-test with a MOCK runner, provide `--dry-run` that prints exactly what it would do, and DOCUMENT the manual live-verification steps. Real live setup is verified by Joi (or a friend) with their own token.

## Global Constraints
- Python 3.9 floor; stdlib + vendor/ only for RENDER; publish MAY shell out to `wrangler` (Node) or a configured command — that's the one place external processes are allowed, and only for the `cloudflare`/`command` backends, never for render.
- Config in `~/.jimemo/config.toml` (parse with stdlib `tomllib` on 3.11+, else a vendored/minimal TOML reader — decide in Task 1; tomllib is 3.11+, our floor is 3.9, so vendor `tomli` (pinned, checksummed, MIT) OR write a tiny reader for the small schema. Prefer vendoring tomli for correctness.).
- Never store secrets in the repo or in published output. The CF API token lives only in the friend's environment/CF-wrangler config, never written by jimemo to a world-readable file without explicit consent; jimemo reads it from env or the wrangler-managed store.
- Published output is the already-self-contained HTML from Phase 3/4 — publish does NOT re-process or weaken it.
- Commits end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Tasks

### Task 1: Vendor tomli + config module + backend seam
- Vendor `tomli` (latest, MIT, pinned+checksummed+OSV, pure-Python) under vendor/ (or write a minimal reader if cleaner — but tomli is small and correct). Add to SHA256SUMS + CREDITS + doctor.
- `src/jimemo/config.py`: load `~/.jimemo/config.toml` → a Config object. Schema: `[publish] backend = "command"|"cloudflare"`, `[publish] command = "notes-publish"` (for command backend), `[publish.cloudflare] project = "...", account_id = "...", kv_namespace_id = "...", base_url = "https://<proj>.pages.dev"`. Missing config → a clear "run jimemo publish setup" error. Validate.
- `src/jimemo/publish/__init__.py` defining the backend interface: `Publisher` with `publish(html_path, title) -> url`, `purge(hash_or_url)`, `list()`, `gc()`. A registry keyed by backend name.
- Accept: config round-trips; doctor covers tomli.

### Task 2: Hash staging + `command` passthrough backend
- `src/jimemo/publish/staging.py`: generate a 24-hex hash (stdlib `secrets.token_hex(12)`), stage the page as `<hash>/index.html` in a publish work dir. (Deterministic-testable via an injected token source.)
- `src/jimemo/publish/command_backend.py`: the `command` backend shells out to the configured command (e.g. `notes-publish <file> --title ...`), parsing the returned URL from its stdout. purge/list/gc dispatch to the same command's subcommands. This is the backend Joi uses (keeps notes-publish authoritative).
- `jimemo publish <rendered.html> [--title]`, `jimemo publish purge <url>`, `list`, `gc` wired in cli.py, dispatching via config's backend.
- Tests: staging hash format/uniqueness; command backend invokes the right argv and parses the URL (mock subprocess); a full dispatch with a fake command.
- **End-to-end (real):** with `publish.command = notes-publish`, `jimemo render briefing <sample> | jimemo publish` → a live notes.ito.com URL (verify it loads), then `jimemo publish purge <url>` tombstones it. (This dogfoods the passthrough against Joi's real, purgeable site.)

### Task 3: Port the tombstone/purge middleware
- `publish/cloudflare/_middleware.js`: port notes-ito-com's `functions/_middleware.js` generalized (hash regex, tombstone KV lookup → 404, `?purge` GET confirm page + POST tombstone, Origin/Sec-Fetch-Site cross-site guard). Parameterize anything site-specific. Ship it as a bundled asset the setup wizard deploys.
- A `_headers` asset (noindex) + a minimal root index.
- Tests: since it's JS-for-Workers, test the pure logic by extracting the hash-match + tombstone-decision into testable form OR document a manual test; at minimum a Python-side test that the bundled middleware file exists, has the hash regex, and matches the notes-ito-com security model (no eval, cross-site guard present). Credit notes-ito-com in CREDITS.

### Task 4: Native `cloudflare` backend + Wrangler seam
- `src/jimemo/publish/wrangler.py`: a `Wrangler` runner wrapping `npx wrangler ...` (deploy pages, KV put/get), with a `MockWrangler` for tests. Detect wrangler/npx availability; clear error if Node missing ("cloudflare backend needs Node/npx; or use the command backend").
- `src/jimemo/publish/cloudflare_backend.py`: publish = stage hash dir + `wrangler pages deploy`; purge = `wrangler kv key put` a tombstone (mirroring notes-ito-com); list = read deployed hashes; gc = remove tombstoned dirs + redeploy. All via the Wrangler seam (unit-tested with MockWrangler).
- Tests: MockWrangler-driven publish/purge/list/gc issue the right wrangler commands; a real wrangler is NOT invoked in tests.

### Task 5: `jimemo publish setup` wizard
- Interactive (and `--dry-run`) wizard for the `cloudflare` backend: explain the free-tier steps, prompt for the CF API token (read from env or prompt; NEVER write it to the repo; store only the non-secret project/account/kv ids in `~/.jimemo/config.toml`; the token stays in the friend's env / wrangler's own store), create the Pages project + KV namespace via the Wrangler seam, deploy the middleware + _headers + root index, write config. `--dry-run` prints every step + the exact wrangler commands WITHOUT executing (fully offline-testable). Idempotent-ish (detect existing project/namespace).
- Tests: `--dry-run` prints the expected plan; setup with MockWrangler creates project+KV+deploy in the right order and writes a correct config (no token in it); a missing-token path errors clearly.
- **Manual live-verification doc** (README/docs): the exact steps for a friend to run real setup with their own token, and how Joi would verify the native backend end-to-end. This is the part that can't be auto-tested.

### Task 6: docs + acceptance
- README: a "Publish" section — the two backends, `jimemo publish setup` for friends (cloudflare) vs `publish.command` for an existing site (Joi's notes-publish), and the security model (hash = access control, symmetric read/purge, tombstone). architecture.md: the publish/ package + backends + Wrangler seam.
- Acceptance: full suite green; the command-backend end-to-end (real notes.ito.com publish+purge) captured; doctor clean incl. tomli; dry-run wizard output captured; `import jimemo.cli` loads no vendored python at import.
- Append phase summary to the SDD ledger.

## Out of scope (defer)
Pure-Python Cloudflare REST backend (research: wrangler primary; REST is a flagged fallback — a Phase-5.1 if Node-free publish is wanted). Custom domains (friends use `*.pages.dev`). Auth beyond the unguessable hash (by design).

Whole-phase review (config/secret handling, the passthrough, middleware security parity with notes-ito-com) + roborev, then squash-merge to main.
