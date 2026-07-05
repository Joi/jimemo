"""Static-content assertions for the Cloudflare publish backend's bundled
assets (`publish/cloudflare/`).

These are the "purge & tombstone" middleware ported from notes-ito-com
(Joi's own private-notes site; see CREDITS.md). The middleware runs inside
Cloudflare Workers, not Python, so it can't be executed or covered by
runtime tests here -- instead we assert the *shape* of the shipped source
that matters for security parity with the original: the hash-based access
control regex, the tombstone KV binding, the purge flow, and the
cross-site POST guard, plus the absence of `eval`/remote-fetch escape
hatches. Live behavior (an actual request round-trip against a deployed
Worker) is verified manually against a real deploy as part of the Task 5
`jimemo publish setup` wizard's documented manual-verification steps --
there is no Workers runtime available to exercise it here.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

CLOUDFLARE_DIR = Path(__file__).resolve().parents[1] / "publish" / "cloudflare"
MIDDLEWARE = CLOUDFLARE_DIR / "_middleware.js"
HEADERS = CLOUDFLARE_DIR / "_headers"
INDEX = CLOUDFLARE_DIR / "index.html"


def _middleware_source() -> str:
    return MIDDLEWARE.read_text(encoding="utf-8")


def test_middleware_file_exists():
    assert MIDDLEWARE.is_file()


def test_middleware_has_24_hex_hash_regex():
    src = _middleware_source()
    assert "[a-f0-9]{24}" in src


def test_middleware_references_tombstone_kv_binding():
    src = _middleware_source()
    # The KV namespace binding name the Task 4/5 Wrangler seam must
    # provision the Pages project with -- see the docstring below.
    assert "env.TOMBSTONES" in src


def test_middleware_fails_closed_on_missing_tombstone_binding():
    """jimemo deliberately diverges from the notes-ito-com original here:
    a missing/misconfigured TOMBSTONES binding must refuse to serve
    (fail CLOSED) rather than silently pass through to next() and serve
    the page anyway (fail OPEN) -- see the middleware's own header
    comment and CREDITS.md for why this matters more for jimemo's
    auto-provisioned-per-friend deployments than it did for the single
    hand-configured original."""
    src = _middleware_source()
    hash_idx = src.index("const hash = m[1]")
    guard_idx = src.index("!env.TOMBSTONES", hash_idx)
    get_idx = src.index("env.TOMBSTONES.get(hash)")
    # The guard must run after the hash is extracted but before the
    # tombstone is ever read.
    assert hash_idx < guard_idx < get_idx
    # Between the guard and the (now unconditional) tombstone read, the
    # code must short-circuit with an error response and must never call
    # next() -- a hash-path request with no binding may never fall
    # through to static-asset serving.
    guard_to_get = src[guard_idx:get_idx]
    assert "return htmlResponse(" in guard_to_get
    assert "errorPage(" in guard_to_get
    assert "next()" not in guard_to_get


def test_middleware_implements_purge_get_and_post_flow():
    src = _middleware_source()
    assert re.search(r'searchParams\.has\(["\']purge["\']\)', src)
    assert 'request.method === "POST"' in src
    assert "TOMBSTONES.put(hash" in src  # the actual tombstone write


def test_middleware_has_cross_site_post_guard():
    src = _middleware_source()
    lower = src.lower()
    assert "sec-fetch-site" in lower
    assert 'request.headers.get("origin")' in src
    assert "sameorigin" in lower


def test_middleware_has_no_eval():
    src = _middleware_source()
    assert "eval(" not in src


def test_middleware_has_no_remote_fetch_or_cdn_reference():
    src = _middleware_source()
    assert "fetch(" not in src
    assert "http://" not in src
    assert "https://" not in src


def test_headers_file_exists_and_sets_noindex():
    assert HEADERS.is_file()
    src = HEADERS.read_text(encoding="utf-8")
    assert "noindex" in src.lower()


def test_headers_file_sets_no_store_on_live_pages():
    """Live hash pages must never be cached by browsers/intermediaries --
    otherwise a purge (which tombstones the hash) can leave a stale cached
    copy viewable after the page is supposed to be gone. _middleware.js
    already sets `cache-control: no-store` on its own purge/confirm/error
    responses, but those are a distinct code path from the `next()` fall-
    through that serves the static hash pages themselves; this asserts the
    static-serving path (governed by _headers) gets the same guarantee."""
    src = HEADERS.read_text(encoding="utf-8")
    assert "Cache-Control: no-store" in src


def test_index_html_exists_and_is_self_contained():
    assert INDEX.is_file()
    src = INDEX.read_text(encoding="utf-8")
    # No external script/style/image/link references -- everything a
    # friend's Pages project serves at the root must be inline.
    assert not re.search(r'(?:src|href)\s*=\s*["\']https?://', src, re.IGNORECASE)
