# Cloudflare Pages direct-upload API feasibility

Research for `jimemo publish` (Phase 5): can deploying a self-contained HTML page to a friend's
Cloudflare Pages project be done with pure-Python HTTP calls, with no Node/wrangler dependency?

## API findings

Cloudflare Pages exposes an official, versioned REST API
(`https://api.cloudflare.com/client/v4/accounts/{account_id}/pages/projects/...`), documented at
[developers.cloudflare.com/api/resources/pages](https://developers.cloudflare.com/api/resources/pages/).
Relevant confirmed endpoints:

- **Create project** — `POST /accounts/{account_id}/pages/projects`. Accepted permission: `Pages Write`.
- **Create deployment** — `POST /accounts/{account_id}/pages/projects/{project_name}/deployments`.
  Accepted permission: `Pages Write`. Body is `multipart/form-data` and officially documents a
  `manifest` field: *"JSON string containing a manifest of files to deploy. Maps file paths to
  their content hashes. Required for direct upload deployments. Maximum 20,000 entries."* This
  confirms Cloudflare treats non-Git ("direct upload") deployments as a first-class, documented
  case of this endpoint, not just Git-triggered builds.
- **Create KV namespace** — `POST /accounts/{account_id}/storage/kv/namespaces`. Accepted
  permission: `Workers KV Storage Write`.

**The gap:** the `manifest` field only maps file paths to content hashes — it does not carry file
bytes. Before calling `deployments`, the actual file contents must already exist in Cloudflare's
content-addressed asset store, keyed by those same hashes. The official Pages API reference tree
(`/api/resources/pages/...`) does **not** document any endpoint for uploading the file bytes
themselves. That upload step is only evidenced by non-official sources:

- A reverse-engineering write-up ([hunterashaw.com](https://hunterashaw.com/reverse-engineering-the-cloudflare-pages-deployment-api/))
  derived from wrangler's source code, describing three undocumented calls: `GET
  .../pages/projects/{project}/upload-token` (returns a short-lived JWT, ~300s), `POST
  v4/pages/assets/upload` (uploads base64-encoded file batches, ≤50MB per call, hash = digest of
  body+path), and `POST v4/pages/assets/upsert-hashes` (registers which hashes now have content
  before they can be referenced in a deployment's `manifest`). The author calls this integration
  "unofficial" and describes the deployment call as "the most finicky" step, with the server
  sensitive to exact multipart formatting.
- Two Cloudflare community forum threads —
  [Does Cloudflare Pages has documented REST API for direct upload?](https://community.cloudflare.com/t/does-cloudflare-pages-has-documented-rest-api-for-direct-upload/566364)
  and [Cloudflare Pages Direct Deployments. REST API](https://community.cloudflare.com/t/cloudflare-pages-direct-deployments-rest-api/441405) —
  echo the same conclusion in their titles and search-result summaries: the byte-upload path is
  wrangler-internal, not part of the public contract, with no stability guarantee. Neither was read
  in full text: one returned HTTP 403 on direct fetch, and the other was not fetched directly
  either. Both are corroborated only via search-result snippets and the reverse-engineering post
  above, not by reading their raw thread text — treat this as the weakest link in the citation
  trail, not as independently verified confirmation.

By contrast, Cloudflare's newer **Workers static assets** direct-upload flow (a different, related
product surface) documents an equivalent JWT-upload-token pattern officially
([developers.cloudflare.com/workers/static-assets/direct-upload](https://developers.cloudflare.com/workers/static-assets/direct-upload/)).
Pages has not received the same documentation treatment as of this research.

**Token scopes needed** (for the `jimemo publish setup` wizard, one custom API token):

| Capability | Permission group | Accepted-permission label on endpoint |
|---|---|---|
| Create/list Pages projects | Account → Cloudflare Pages → Edit | `Pages Write` |
| Create deployment (direct upload) | Account → Cloudflare Pages → Edit | `Pages Write` |
| Create KV namespace | Account → Workers KV Storage → Edit | `Workers KV Storage Write` |

("Edit" is the label shown in the dashboard token-creation UI; "Write" is the same permission
group's name as it appears in the API reference's "Accepted Permissions" list — they are the same
scope under two labels used in different parts of Cloudflare's docs.) No account-wide Read/Admin
scopes beyond these were required by any endpoint checked.

## Recommendation (wrangler vs REST)

**Recommend wrangler via `npx wrangler pages deploy`** for the Phase 5 publish path, not raw
Python REST calls.

Reasoning:
- The one undocumented piece — turning local file bytes into content the `manifest` hash can
  reference — is exactly the part with no official contract, sensitive multipart formatting, and
  no stability guarantee. Building and maintaining a hand-rolled version of it in `jimemo` means
  owning breakage risk for Cloudflare implementation details wrangler's maintainers already track.
- Everything else in the flow (project create, KV namespace create, and the deployment call's
  documented fields) is officially stable and could be called directly if needed, but the payoff
  of avoiding Node for only those calls is small since the hard part (asset upload) still isn't
  documented.
- `npx wrangler` requires Node/npm but no persistent install (`npx` fetches on demand), which is a
  reasonable ask for a one-time `jimemo publish setup` step; it keeps `jimemo`'s Python runtime
  dependency-free for the common (non-publish) path.

**Fallback:** if Node/npx is unavailable or undesirable in a given environment, fall back to
reimplementing the reverse-engineered upload-token → `pages/assets/upload` → `upsert-hashes` →
`deployments` sequence in pure Python, explicitly flagged in code and docs as depending on
undocumented Cloudflare behavior that may change without notice. This should be a documented
opt-in fallback, not the default path. Cloudflare could document the asset-upload endpoints at any
time (it already has for the newer Workers static-assets product) — re-check the official API
reference for a documented byte-upload endpoint before implementing this fallback in Phase 5.

**Token scopes for either path** are identical (both ultimately call the same account-scoped
Cloudflare API): `Pages Write` (a.k.a. "Cloudflare Pages: Edit") for project + deployment
operations, and `Workers KV Storage Write` (a.k.a. "Workers KV Storage: Edit") for KV namespace
creation. Wrangler needs these same scopes on the API token it's given (via
`CLOUDFLARE_API_TOKEN`); it does not require broader account Admin access.
