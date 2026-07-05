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

The wizard's `wrangler` calls go through a narrow seam (`check_available`,
`pages_deploy`, `kv_put`, `kv_get`, `kv_list` -- see
`src/jimemo/publish/wrangler.py`). It does not create a Cloudflare
account, create a KV namespace, or bind a KV namespace to a Pages
project -- there's no single wrangler CLI verb for the last one (it's a
one-time dashboard action), so the wizard prints the exact command or
dashboard step and asks you to supply the resulting id instead of trying
to fake automating it.

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

   It will:
   - prompt for a Cloudflare Pages project name (default `jimemo-notes`)
     and your Cloudflare account id,
   - print the command to create a KV namespace
     (`npx wrangler kv namespace create <project>-tombstones`) and ask
     for the resulting id,
   - print the dashboard step to bind that namespace to the Pages
     project's Settings -> Functions -> KV namespace bindings under the
     exact binding name `TOMBSTONES` (the deployed middleware reads it
     as `env.TOMBSTONES` -- a typo here makes purge silently do nothing),
   - deploy `publish/cloudflare/` (the middleware, `_headers`, and root
     index) to the project, creating the project if your account
     doesn't already have one by that name,
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
end to end -- it's the one part of Phase 5 that automated tests can't
cover, since it needs a real account, a real token, and a real network
round trip. The dry-run plan and every wrangler call the wizard makes
(project/account/namespace prompts, `pages_deploy`, `kv_put`/`kv_get`)
are otherwise fully covered by `tests/test_setup.py` against a mock
wrangler runner.
