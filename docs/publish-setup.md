# Setting up the `cloudflare` publish backend

`jimemo publish setup` provisions a free Cloudflare Pages site for the
`cloudflare` publish backend: unlisted-link hosting for rendered jimemo
pages, mirroring notes.ito.com's security model (a 24-hex-hash path is
the access control, read and purge are symmetric, purging tombstones a
hash rather than deleting it). This is the backend for someone who
doesn't already run their own publish site -- if you do (like
notes.ito.com), use the `command` backend instead and point
`[publish].command` at your existing CLI.

## What the wizard can't automate

The wizard's `wrangler` calls go through a narrow seam
(`check_available`, `pages_project_names`, `pages_project_create`,
`pages_deploy`, `kv_put`, `kv_get`, `kv_list` -- see
`src/jimemo/publish/wrangler.py`). It does not create a Cloudflare
account, create a KV namespace, or bind a KV namespace to a Pages
project -- there's no single wrangler CLI verb for the last one (it's a
one-time dashboard action), so the wizard prints the exact command or
dashboard step and asks you to supply the resulting id instead of trying
to fake automating it.

## The wipe-by-deploy hazard, and the git fix (read this)

The `cloudflare` backend deploys from a local state directory:
`~/.jimemo/cloudflare/<project>/`. That directory accumulates every hash
this machine has published and is the *only* source of truth for what
the next deploy will contain -- `jimemo publish` always redeploys the
whole directory, replacing the production tree wholesale. Publishing
from a second machine (or a reinstalled first one) whose copy is missing
hashes silently 404s every one of them. notes.ito.com hit exactly this
three times in production before fixing it by syncing its tree through
git; jimemo ships the same fix, opt-in.

**Single machine:** nothing to do. A state dir that is not a git repo
behaves as it always has, and git is never invoked.

**Multiple machines: make the state dir a git repo.** Create a PRIVATE
empty repo (the pages are unlisted-by-hash; their content does not
belong in a public repo), then on the machine that has the state dir:

```
cd ~/.jimemo/cloudflare/<project>
git init
git remote add origin git@github.com:you/jimemo-notes-state.git
git add -A && git commit -m init && git push -u origin HEAD
```

On every other machine, clone it into place instead:

```
git clone git@github.com:you/jimemo-notes-state.git ~/.jimemo/cloudflare/<project>
```

From then on every deploying operation — `jimemo publish`, `jimemo
publish gc`, `publish setup` re-runs, and `setup --assets-only` — syncs
automatically, mirroring notes.ito.com's model and ordering:

- **Dirty tree refused.** Local edits or deletions of tracked files
  would ship wholesale on the next deploy (a deleted tracked hash
  silently removes that live page), so they refuse the deploy;
  untracked strays only warn.
- **Pull before staging** (fast-forward to origin's default branch): a
  deploy always ships the union of every machine's pages. Divergence or
  an unreachable origin refuses the deploy with the git error.
- **Commit + push BEFORE deploying**: the mutation lands on origin
  first, so no other machine can pull-and-deploy a tree that lacks it.
  Commits are pathspec-limited to the touched paths — an unrelated file
  in the state dir is never swept in. A push rejected by a racing
  machine is retried once via fetch + rebase (folding their pages in);
  if the push still does not land, the deploy proceeds only when origin
  provably has nothing newer, and otherwise refuses — the change stays
  committed locally and rides out with the next successful publish.
- **`--no-sync`** skips all of it for a deliberate emergency deploy
  from a copy you know is current.

## Steps a friend runs for real

1. **Create a Cloudflare account** (free tier) if you don't have one:
   https://dash.cloudflare.com/sign-up

2. **Create an API token** at
   https://dash.cloudflare.com/profile/api-tokens with scopes:
   - `Account | Cloudflare Pages | Edit`
   - `Account | Workers KV Storage | Edit`

   Export it in your shell -- jimemo never stores this token; wrangler
   reads it directly from the environment:

   ```
   export CLOUDFLARE_API_TOKEN=...
   ```

3. **Install Node** if you don't have it (wrangler runs via `npx
   wrangler`): https://nodejs.org

4. **Run the wizard**:

   ```
   jimemo publish setup
   ```

   How to read its output: a command you must run yourself appears on
   its own `$ `-prefixed line (run it in a separate terminal where the
   wizard says so); `running:` lines are wrangler calls the wizard makes
   for you.

   It will:
   - prompt for a Cloudflare Pages project name (default `jimemo-notes`)
     and your Cloudflare account id,
   - create that Pages project if your account does not already have one
     by that name,
   - print the command to create a KV namespace
     (`npx wrangler kv namespace create <project>-tombstones`) and ask
     for the resulting id,
   - print the dashboard step to bind that namespace to the Pages
     project's Settings -> Functions -> KV namespace bindings under the
     exact binding name `TOMBSTONES` (the deployed middleware reads it
     as `env.TOMBSTONES` -- a typo here makes purge silently do nothing),
   - install the middleware, `_headers`, and root index into
     `~/.jimemo/cloudflare/<project>/` -- the persistent local state
     directory `jimemo publish` deploys from every time, not a one-off
     copy of the repo's `publish/cloudflare/` template (see "Single-
     machine limitation" above for why this directory matters),
   - deploy that directory to the Pages project,
   - run a best-effort KV round-trip check,
   - write `~/.jimemo/config.toml` (no token in it -- see below).

   To see the exact plan and every wrangler command without running
   anything or touching your account, use:

   ```
   jimemo publish setup --dry-run
   ```

5. **Verify it end to end** (the one thing the automated test suite
   cannot exercise, since it needs a real Cloudflare account/token):

   ```
   jimemo render briefing templates/briefing/sample/content.md -o /tmp/test.html
   jimemo publish /tmp/test.html
   # -> https://<project>.pages.dev/<hash>/
   ```

   Open that URL -- it should load the rendered page. Then:

   ```
   jimemo publish purge https://<project>.pages.dev/<hash>/
   curl -o /dev/null -w '%{http_code}\n' https://<project>.pages.dev/<hash>/
   # -> expect 404
   ```

   A `200` instead of `404` after purging means the `TOMBSTONES` KV
   binding is missing or misnamed in the Pages project's dashboard
   settings -- go back and re-check step 4's binding step. Cloudflare
   KV's read-cache means a 404 may take up to ~60 seconds to appear
   everywhere even once the binding is correct.

## Upgrading

The state directory keeps its own copy of `functions/_middleware.js`,
`_headers`, and the root index, installed at setup time. `jimemo
publish` self-heals a *missing* file but deliberately never overwrites
an existing one — so after a `git pull` that changes the middleware,
existing sites keep serving the old copy until you run:

```
jimemo publish setup --assets-only
```

It re-copies the current bundled assets into the state directory and
redeploys — published hashes untouched, `config.toml` untouched. (A
full `jimemo publish setup` re-run also refreshes the assets, but it
rewrites `config.toml` from scratch — dropping any `[pdf]` section or
comments you added — so it's the wrong tool for a routine upgrade.)

## Config written

`~/.jimemo/config.toml` (or `$JIMEMO_CONFIG`, if set):

```toml
[publish]
backend = "cloudflare"

[publish.cloudflare]
project = "jimemo-notes"
account_id = "..."
kv_namespace_id = "..."
base_url = "https://jimemo-notes.pages.dev"
```

No token is ever written here. `CLOUDFLARE_API_TOKEN` must stay in your
shell environment (or wherever `wrangler login`/your shell profile keeps
it) -- `jimemo publish`/`purge`/`list`/`gc` all shell out to `wrangler`,
which resolves its own auth the same way `setup` does.

## Who verifies this

This live path (a friend's own Cloudflare account and token) is what
Joi or a friend runs manually to confirm the `cloudflare` backend works
end to end -- it's the one part of the publish subsystem automated tests can't
cover, since it needs a real account, a real token, and a real network
round trip. The dry-run plan and every wrangler call the wizard makes
(project/account/namespace prompts, `pages_deploy`, `kv_put`/`kv_get`)
are otherwise fully covered by `tests/test_setup.py` against a mock
wrangler runner.
