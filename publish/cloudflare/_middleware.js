/**
 * jimemo publish (cloudflare backend) — purge & tombstone middleware
 *
 * Ported from notes-ito-com (Joi's own private-notes site: see CREDITS.md),
 * generalized so it works on any *.pages.dev deployment rather than one
 * hardcoded domain. Runs for every request hitting the Pages site. Handles
 * three flows:
 *
 *   1. Normal GET on /<24-hex-hash>/...       → check tombstone, 404 if marked
 *   2. GET /<hash>/?purge                     → render confirmation page
 *   3. POST /<hash>/?purge                    → write tombstone to KV
 *
 * Anything outside the /<24-hex-hash>/ namespace passes through to static
 * Pages serving (root index, _headers, etc.).
 *
 * Security model (unchanged from notes-ito-com):
 *   - The 24-hex-char hash is the access control. 96 bits = unguessable.
 *   - Read access and purge access are intentionally symmetric: anyone with
 *     the URL can read, anyone with the URL can purge.
 *   - Click-confirm prevents accidents from forwarded URLs / prefetchers.
 *   - A simple Origin / Sec-Fetch-Site check rejects cross-site form posts.
 *   - Staged files remain on disk locally after purge; the tombstone in KV
 *     makes them inaccessible. Run `jimemo publish gc` locally to actually
 *     delete files and reclaim deploy size.
 *
 * KV binding: this Worker reads/writes the tombstone namespace as
 * `env.TOMBSTONES`. Whatever provisions the Pages project (jimemo's
 * `publish setup` wizard, or a manual wrangler/dashboard binding) MUST
 * create the KV namespace binding under this exact name — if it's bound
 * under a different name, `env.TOMBSTONES` is `undefined`, tombstone reads
 * silently become "nothing purged" (open-fail, not fail-safe), and any
 * purge attempt throws on `.put()`.
 *
 * The only change from the notes-ito-com original beyond the rename above
 * is that purge/confirm page copy now reads the hostname from the
 * incoming request (`url.host`) instead of a hardcoded "notes.ito.com" —
 * everything else (control flow, regex, headers, CSS) is verbatim.
 */

const HASH_RE = /^\/([a-f0-9]{24})(\/.*)?$/;

export const onRequest = async (ctx) => {
  const { request, env, next } = ctx;
  const url = new URL(request.url);
  const m = url.pathname.match(HASH_RE);

  // Not a hash path → pass through (root, _headers, etc.)
  if (!m) return next();

  const hash = m[1];
  const tombstone = env.TOMBSTONES ? await env.TOMBSTONES.get(hash) : null;

  // Purge flow (?purge in query string)
  if (url.searchParams.has("purge")) {
    if (request.method === "POST") {
      // Light CSRF check: require same-origin form post.
      const origin = request.headers.get("origin") || "";
      const sfs = request.headers.get("sec-fetch-site") || "";
      const sameOrigin = origin === url.origin || sfs === "same-origin";
      if (!sameOrigin) {
        return htmlResponse(errorPage("Cross-origin POST refused."), 403);
      }
      if (tombstone) {
        return htmlResponse(alreadyPurgedPage(hash, tombstone), 200);
      }
      const ts = new Date().toISOString();
      await env.TOMBSTONES.put(hash, ts);
      return htmlResponse(purgedPage(hash, url.host, ts), 200);
    }
    // GET ?purge → confirmation page
    return htmlResponse(
      tombstone ? alreadyPurgedPage(hash, tombstone) : confirmPage(hash, url.host),
      200,
    );
  }

  // Normal request → 404 if tombstoned
  if (tombstone) {
    return htmlResponse(purgedTombstonePage(hash, tombstone), 404);
  }

  return next();
};

function htmlResponse(body, status = 200) {
  return new Response(body, {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "x-robots-tag": "noindex, nofollow, noarchive",
      "referrer-policy": "no-referrer",
      "cache-control": "no-store",
    },
  });
}

const css = `
  :root { color-scheme: light dark; }
  body { font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
         max-width: 36rem; margin: 4rem auto; padding: 0 1.25rem; color: #222; }
  @media (prefers-color-scheme: dark) {
    body { color: #ddd; background: #1a1a1a; }
    code { background: #2a2a2a; }
    .danger { background: #b53636; color: #fff; border-color: #b53636; }
    .danger:hover { background: #cc4040; }
    .cancel { background: #2a2a2a; color: #ddd; border-color: #444; }
    .cancel:hover { background: #333; }
  }
  h1 { font-weight: 600; font-size: 1.25rem; margin-top: 0; }
  p { color: #666; }
  @media (prefers-color-scheme: dark) { p { color: #aaa; } }
  code { background: #f4f4f4; padding: 0.1rem 0.4rem; border-radius: 3px; font-size: 0.92em;
         font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; }
  .row { margin-top: 1.5rem; display: flex; gap: 0.75rem; flex-wrap: wrap; }
  button, .btn { font: inherit; padding: 0.55rem 1.1rem; border: 1px solid; border-radius: 6px;
                 cursor: pointer; text-decoration: none; display: inline-block; }
  .danger { background: #c0392b; color: #fff; border-color: #c0392b; }
  .danger:hover { background: #d04535; }
  .cancel { background: #f4f4f4; color: #222; border-color: #ddd; }
  .cancel:hover { background: #ebebeb; }
  .small { font-size: 0.85em; color: #888; margin-top: 2.5rem; }
`;

function shell(title, body) {
  return `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>${escapeHtml(title)}</title>
<style>${css}</style>
</head><body>${body}</body></html>`;
}

function confirmPage(hash, host) {
  return shell("Purge note?", `
<h1>Purge this note?</h1>
<p>You are about to make <code>${escapeHtml(host)}/${hash}/</code> permanently
inaccessible. Anyone who already has this URL will get a 404 immediately.</p>
<p>The staged files remain on disk locally and can be re-published later
under a <em>different</em> hash if needed — but this exact URL will be
unreachable forever.</p>
<form method="POST" action="?purge">
  <div class="row">
    <button type="submit" class="danger">Yes, purge it</button>
    <a class="btn cancel" href="./">Cancel</a>
  </div>
</form>
<p class="small">Hash: <code>${hash}</code></p>
`);
}

function purgedPage(hash, host, ts) {
  return shell("Purged", `
<h1>Purged</h1>
<p>The note at <code>${escapeHtml(host)}/${hash}/</code> has been tombstoned.
Most edges return 404 immediately; a few may serve the cached old response
for up to 60 seconds (Cloudflare KV's minimum read-cache window).</p>
<p class="small">Tombstoned at ${escapeHtml(ts)}</p>
`);
}

function alreadyPurgedPage(hash, ts) {
  return shell("Already purged", `
<h1>Already purged</h1>
<p>This note was already purged${ts ? ` at <code>${escapeHtml(ts)}</code>` : ""}.</p>
<p class="small">Hash: <code>${hash}</code></p>
`);
}

function purgedTombstonePage(hash, ts) {
  return shell("Not found", `
<h1>Not found</h1>
<p>This note has been purged.</p>
<p class="small">Hash: <code>${hash}</code> · purged at ${escapeHtml(ts || "?")}</p>
`);
}

function errorPage(msg) {
  return shell("Error", `<h1>Error</h1><p>${escapeHtml(msg)}</p>`);
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
