import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.config import CloudflareConfig, PublishConfig
from jimemo.errors import PublishError
from jimemo.publish.cloudflare_backend import (
    CLOUDFLARE_ASSETS_DIR,
    CloudflarePublisher,
    _ensure_state_dir_assets,
)
from jimemo.publish.wrangler import MockWrangler, Wrangler

HASH_RE = re.compile(r"[a-f0-9]{24}")


def _publish_config():
    cf = CloudflareConfig(
        project="friend-notes",
        account_id="acct1",
        kv_namespace_id="ns1",
        base_url="https://friend-notes.pages.dev",
    )
    return PublishConfig(backend="cloudflare", cloudflare=cf)


def _publisher(tmp_path, wrangler=None, clock=None):
    kwargs = {"state_dir": tmp_path / "state"}
    if wrangler is not None:
        kwargs["wrangler"] = wrangler
    if clock is not None:
        kwargs["clock"] = clock
    return CloudflarePublisher(_publish_config(), **kwargs)


def _hash_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


class TreeCapturingWrangler(MockWrangler):
    """MockWrangler that also records each deployed directory's file tree
    (sorted relative POSIX paths) in ``.deployed_trees`` AT DEPLOY TIME.
    publish()/gc() deploy a throwaway allowlisted build dir that is
    deleted as soon as the deploy call returns, so a test can only see
    what was actually deployed by capturing it during the call."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.deployed_trees = []

    def pages_deploy(self, project, directory, branch="main"):
        directory = Path(directory)
        self.deployed_trees.append(sorted(
            p.relative_to(directory).as_posix()
            for p in directory.rglob("*")
            if p.is_file()
        ))
        return super().pages_deploy(project, directory, branch)


def _seed_hash_dir(state_dir: Path, page_hash: str) -> None:
    (state_dir / page_hash).mkdir(parents=True)
    (state_dir / page_hash / "index.html").write_text("<html></html>")


def _seed_strays(state_dir: Path) -> set:
    """Plant every category of stray a git/Dropbox/Syncthing-synced state
    dir accumulates. Returns the set of top-level stray names so tests
    can assert they survive in the state dir but never deploy."""
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / ".git").mkdir()
    (state_dir / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (state_dir / ".git" / "config").write_text("[core]\n")
    (state_dir / ".DS_Store").write_bytes(b"\x00Bud1")
    (state_dir / "index (conflicted copy).html").write_text("<html>conflict</html>")
    (state_dir / "notes.html~").write_text("<html>backup</html>")
    (state_dir / "#notes#").write_text("<html>autosave</html>")
    (state_dir / "secret").mkdir()
    (state_dir / "secret" / "passwords.txt").write_text("hunter2\n")
    return {
        ".git", ".DS_Store", "index (conflicted copy).html",
        "notes.html~", "#notes#", "secret",
    }


def test_constructor_requires_cloudflare_config():
    config = PublishConfig(backend="cloudflare", cloudflare=None)
    with pytest.raises(PublishError):
        CloudflarePublisher(config)


def test_publish_stages_deploys_and_returns_hash_url(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html><title>hi</title></html>")
    wrangler = MockWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler)

    url = publisher.publish(html)

    assert url.startswith("https://friend-notes.pages.dev/")
    assert HASH_RE.fullmatch(_hash_from_url(url))
    deploy_calls = [c for c in wrangler.calls if c[0] == "pages_deploy"]
    assert len(deploy_calls) == 1
    assert deploy_calls[0][1] == "friend-notes"


def test_publish_title_is_accepted_but_does_not_change_argv(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    wrangler = MockWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler)

    publisher.publish(html, title="Q3 Briefing")

    deploy_calls = [c for c in wrangler.calls if c[0] == "pages_deploy"]
    assert len(deploy_calls) == 1  # title never reaches wrangler's argv


def test_second_publish_redeploys_directory_containing_both_hashes(tmp_path):
    """A Pages deploy replaces the whole production tree, so the state
    directory redeployed on the second publish() must still contain the
    first hash's files -- otherwise the first URL would 404 the moment a
    second page is published."""
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    wrangler = TreeCapturingWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler)

    url1 = publisher.publish(html)
    url2 = publisher.publish(html)
    hash1, hash2 = _hash_from_url(url1), _hash_from_url(url2)

    assert hash1 != hash2
    deploy_calls = [c for c in wrangler.calls if c[0] == "pages_deploy"]
    assert len(deploy_calls) == 2
    assert f"{hash1}/index.html" in wrangler.deployed_trees[-1]
    assert f"{hash2}/index.html" in wrangler.deployed_trees[-1]


def test_publish_raises_clear_error_when_wrangler_unavailable(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")

    class UnavailableWrangler(MockWrangler):
        def check_available(self):
            return False

    publisher = _publisher(tmp_path, wrangler=UnavailableWrangler())

    with pytest.raises(PublishError) as exc:
        publisher.publish(html)
    assert "wrangler" in str(exc.value).lower()


def test_publish_rolls_back_staged_hash_dir_when_deploy_fails(tmp_path):
    """publish() stages the new hash into the PERSISTENT state dir before
    deploying. If pages_deploy raises, that staged dir must not survive --
    otherwise the next publish() would redeploy a hash that was never
    actually served (an orphan URL that 404s but looks live in list()).
    Only a successful deploy may leave the staged hash behind."""
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    wrangler = MockWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler)
    state_dir = tmp_path / "state"

    url1 = publisher.publish(html)
    hash1 = _hash_from_url(url1)
    before = sorted(p.name for p in state_dir.iterdir())

    class FailingWrangler(MockWrangler):
        def pages_deploy(self, project, directory, branch="main"):
            self.calls.append(("pages_deploy", project, str(directory), branch))
            raise RuntimeError("deploy exploded")

    publisher._wrangler = FailingWrangler()

    with pytest.raises(PublishError):
        publisher.publish(html)

    after = sorted(p.name for p in state_dir.iterdir())
    assert after == before  # no orphan hash dir left behind
    assert (state_dir / hash1).is_dir()  # unrelated pre-existing hash untouched
    assert (state_dir / "functions" / "_middleware.js").is_file()  # middleware untouched


def test_purge_extracts_hash_from_bare_hash_and_writes_timestamp(tmp_path):
    wrangler = MockWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler, clock=lambda: "2026-07-05T00:00:00.000Z")

    publisher.purge("ab" * 12)

    assert ("kv_put", "ns1", "ab" * 12, "2026-07-05T00:00:00.000Z") in wrangler.calls


def test_purge_extracts_hash_from_full_url(tmp_path):
    wrangler = MockWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler, clock=lambda: "2026-07-05T00:00:00.000Z")

    publisher.purge(f"https://friend-notes.pages.dev/{'cd' * 12}/")

    assert ("kv_put", "ns1", "cd" * 12, "2026-07-05T00:00:00.000Z") in wrangler.calls


def test_purge_rejects_invalid_hash(tmp_path):
    publisher = _publisher(tmp_path, wrangler=MockWrangler())

    with pytest.raises(PublishError) as exc:
        publisher.purge("not-a-hash")
    assert "not-a-hash" in str(exc.value)


def test_purge_does_not_require_local_staging(tmp_path):
    """Read/purge are symmetric and machine-independent: purging a hash
    this machine never staged must still succeed."""
    wrangler = MockWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler, clock=lambda: "2026-07-05T00:00:00.000Z")

    publisher.purge("ef" * 12)

    assert ("kv_put", "ns1", "ef" * 12, "2026-07-05T00:00:00.000Z") in wrangler.calls


def test_list_combines_local_and_kv_state(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    wrangler = MockWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler, clock=lambda: "2026-07-05T00:00:00.000Z")

    url1 = publisher.publish(html)
    url2 = publisher.publish(html)
    hash1, hash2 = _hash_from_url(url1), _hash_from_url(url2)
    publisher.purge(hash1)

    entries = {e["hash"]: e for e in publisher.list()}

    assert entries[hash1]["status"] == "purged"
    assert entries[hash1]["tombstoned_at"] == "2026-07-05T00:00:00.000Z"
    assert entries[hash1]["staged_locally"] is True
    assert entries[hash2]["status"] == "live"
    assert entries[hash2]["tombstoned_at"] is None


def test_list_includes_kv_only_hash_not_staged_on_this_machine(tmp_path):
    wrangler = MockWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler, clock=lambda: "2026-07-05T00:00:00.000Z")

    other_hash = "11" * 12
    publisher.purge(other_hash)

    entries = {e["hash"]: e for e in publisher.list()}
    assert entries[other_hash]["status"] == "purged"
    assert entries[other_hash]["staged_locally"] is False


def test_gc_removes_tombstoned_local_dirs_and_redeploys(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    wrangler = MockWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler, clock=lambda: "2026-07-05T00:00:00.000Z")

    url1 = publisher.publish(html)
    url2 = publisher.publish(html)
    hash1, hash2 = _hash_from_url(url1), _hash_from_url(url2)
    publisher.purge(hash1)

    deploys_before = len([c for c in wrangler.calls if c[0] == "pages_deploy"])
    publisher.gc()
    deploys_after = len([c for c in wrangler.calls if c[0] == "pages_deploy"])

    state_dir = tmp_path / "state"
    assert not (state_dir / hash1).exists()
    assert (state_dir / hash2).exists()
    assert deploys_after == deploys_before + 1


def test_gc_is_a_noop_when_nothing_tombstoned(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    wrangler = MockWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler)
    publisher.publish(html)

    deploys_before = len([c for c in wrangler.calls if c[0] == "pages_deploy"])
    publisher.gc()
    deploys_after = len([c for c in wrangler.calls if c[0] == "pages_deploy"])

    assert deploys_after == deploys_before


def test_gc_ignores_tombstones_with_no_local_directory(tmp_path):
    """gc must not error when a tombstoned hash was never staged on this
    machine (e.g. published from elsewhere, or already gc'd here)."""
    wrangler = MockWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler, clock=lambda: "ts")
    publisher.purge("22" * 12)

    publisher.gc()  # must not raise

    assert not any(c[0] == "pages_deploy" for c in wrangler.calls)


def test_list_filters_out_non_hash_kv_names(tmp_path):
    """setup.py writes a non-hash sentinel key (__jimemo_setup_check__)
    into the same KV namespace for its post-deploy round-trip check; that
    key must never show up in the published-hash listing."""
    wrangler = MockWrangler()
    wrangler.kv_put("ns1", "__jimemo_setup_check__", "ok")
    wrangler.kv_put("ns1", "ab" * 12, "2026-07-05T00:00:00.000Z")
    publisher = _publisher(tmp_path, wrangler=wrangler)

    entries = publisher.list()

    assert [e["hash"] for e in entries] == ["ab" * 12]


def test_gc_ignores_non_hash_kv_names_when_collecting_tombstones(tmp_path):
    wrangler = MockWrangler()
    wrangler.kv_put("ns1", "__jimemo_setup_check__", "ok")
    publisher = _publisher(tmp_path, wrangler=wrangler)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    # A directory that happens to share the sentinel's name must survive
    # gc even though a same-named KV key exists -- only a real hash-shaped
    # KV key may mark a directory for removal.
    (state_dir / "__jimemo_setup_check__").mkdir()

    publisher.gc()

    assert (state_dir / "__jimemo_setup_check__").is_dir()


def test_gc_never_removes_a_non_hash_named_state_dir_child(tmp_path):
    """Defense in depth: even if a KV key's name collided with a real,
    non-hash state-dir child (e.g. a KV key literally named "functions"),
    gc must never rmtree that child -- it only ever removes children whose
    OWN name matches the hash pattern."""
    wrangler = MockWrangler()
    wrangler.kv_put("ns1", "functions", "2026-07-05T00:00:00.000Z")
    publisher = _publisher(tmp_path, wrangler=wrangler)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "functions").mkdir()
    (state_dir / "functions" / "_middleware.js").write_text("// mw")

    publisher.gc()

    assert (state_dir / "functions" / "_middleware.js").is_file()


# ---------------------------------------------------------------------------
# Self-heal: publish() must install the baseline functions/_middleware.js +
# _headers + index.html into the state dir if missing -- e.g. `jimemo
# publish setup` was interrupted, the state dir was deleted, or some other
# partial state -- since publish() always redeploys the WHOLE state
# directory (see module docstring). A deploy that goes out without the
# middleware has no tombstone Function, so ?purge silently stops working.
# ---------------------------------------------------------------------------

def test_publish_installs_missing_middleware_into_state_dir(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    wrangler = MockWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler)
    state_dir = tmp_path / "state"

    publisher.publish(html)

    installed = state_dir / "functions" / "_middleware.js"
    assert installed.is_file()
    assert installed.read_text(encoding="utf-8") == (
        CLOUDFLARE_ASSETS_DIR / "_middleware.js"
    ).read_text(encoding="utf-8")
    assert (state_dir / "_headers").is_file()
    assert (state_dir / "index.html").is_file()


def test_publish_deploys_a_dir_that_always_contains_the_middleware(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    wrangler = TreeCapturingWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler)

    publisher.publish(html)

    assert "functions/_middleware.js" in wrangler.deployed_trees[-1]


def test_publish_does_not_clobber_already_installed_middleware(tmp_path):
    """Idempotent: if the middleware is already present (e.g. installed by
    `jimemo publish setup`, or by an earlier publish() call's self-heal),
    a later publish() must not re-copy over it."""
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    wrangler = MockWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler)
    state_dir = tmp_path / "state"
    functions_dir = state_dir / "functions"
    functions_dir.mkdir(parents=True)
    sentinel = "// pre-existing installed copy, must survive publish()\n"
    (functions_dir / "_middleware.js").write_text(sentinel)

    publisher.publish(html)

    assert (functions_dir / "_middleware.js").read_text() == sentinel


# ---------------------------------------------------------------------------
# Self-heal: gc() redeploys the WHOLE state directory too (see module
# docstring / _ensure_state_dir_assets), so it must install the same
# baseline assets before its redeploy if they're missing -- otherwise a
# damaged or partial state dir that gc() happens to touch first would go
# back out with no tombstone Function, silently disabling ?purge. Mirrors
# the publish() self-heal tests above.
# ---------------------------------------------------------------------------

def test_gc_installs_missing_middleware_before_redeploy(tmp_path):
    wrangler = TreeCapturingWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler, clock=lambda: "2026-07-05T00:00:00.000Z")
    state_dir = tmp_path / "state"
    tombstoned = "ab" * 12
    (state_dir / tombstoned).mkdir(parents=True)
    (state_dir / tombstoned / "index.html").write_text("<html></html>")
    wrangler.kv_put("ns1", tombstoned, "2026-07-05T00:00:00.000Z")
    assert not (state_dir / "functions" / "_middleware.js").is_file()

    publisher.gc()

    installed = state_dir / "functions" / "_middleware.js"
    assert installed.is_file()
    assert installed.read_text(encoding="utf-8") == (
        CLOUDFLARE_ASSETS_DIR / "_middleware.js"
    ).read_text(encoding="utf-8")
    assert (state_dir / "_headers").is_file()
    assert (state_dir / "index.html").is_file()
    assert "functions/_middleware.js" in wrangler.deployed_trees[-1]


def test_gc_does_not_clobber_already_installed_middleware(tmp_path):
    """Idempotent: if the middleware is already present, a gc() redeploy
    must not re-copy over it."""
    wrangler = MockWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler, clock=lambda: "2026-07-05T00:00:00.000Z")
    state_dir = tmp_path / "state"
    tombstoned = "cd" * 12
    (state_dir / tombstoned).mkdir(parents=True)
    (state_dir / tombstoned / "index.html").write_text("<html></html>")
    wrangler.kv_put("ns1", tombstoned, "2026-07-05T00:00:00.000Z")
    functions_dir = state_dir / "functions"
    functions_dir.mkdir(parents=True)
    sentinel = "// pre-existing installed copy, must survive gc()\n"
    (functions_dir / "_middleware.js").write_text(sentinel)

    publisher.gc()

    assert (functions_dir / "_middleware.js").read_text() == sentinel


def test_ensure_state_dir_assets_reinstalls_only_the_missing_file(tmp_path):
    """_ensure_state_dir_assets must check _headers and index.html, not
    just the middleware -- a state dir missing only _headers previously
    stayed that way forever (deploys silently lost the noindex/noarchive
    headers) because the old check short-circuited on the middleware
    alone being present."""
    state_dir = tmp_path / "state"
    functions_dir = state_dir / "functions"
    functions_dir.mkdir(parents=True)
    middleware_sentinel = "// pre-existing middleware, must survive\n"
    (functions_dir / "_middleware.js").write_text(middleware_sentinel)
    (state_dir / "index.html").write_text("pre-existing index, must survive\n")
    # _headers deliberately absent.

    _ensure_state_dir_assets(state_dir)

    assert (functions_dir / "_middleware.js").read_text() == middleware_sentinel
    assert (state_dir / "index.html").read_text() == "pre-existing index, must survive\n"
    assert (state_dir / "_headers").is_file()
    assert (state_dir / "_headers").read_text(encoding="utf-8") == (
        CLOUDFLARE_ASSETS_DIR / "_headers"
    ).read_text(encoding="utf-8")


def test_ensure_state_dir_assets_is_a_noop_when_all_three_present(tmp_path):
    state_dir = tmp_path / "state"
    functions_dir = state_dir / "functions"
    functions_dir.mkdir(parents=True)
    middleware_sentinel = "// mw\n"
    headers_sentinel = "# headers\n"
    index_sentinel = "<html>index</html>\n"
    (functions_dir / "_middleware.js").write_text(middleware_sentinel)
    (state_dir / "_headers").write_text(headers_sentinel)
    (state_dir / "index.html").write_text(index_sentinel)

    _ensure_state_dir_assets(state_dir)

    assert (functions_dir / "_middleware.js").read_text() == middleware_sentinel
    assert (state_dir / "_headers").read_text() == headers_sentinel
    assert (state_dir / "index.html").read_text() == index_sentinel


def test_default_wrangler_is_scoped_to_configured_account_id(tmp_path):
    """When CloudflarePublisher constructs its own Wrangler (no wrangler
    passed in), it must scope it to the configured account_id -- so
    every pages_deploy / kv_* call the Wrangler seam makes is scoped to
    the right Cloudflare account for a friend's multi-account token (see
    Wrangler's own account-scoping docstring)."""
    publisher = CloudflarePublisher(_publish_config(), state_dir=tmp_path / "state")

    assert isinstance(publisher._wrangler, Wrangler)
    assert publisher._wrangler.account_id == "acct1"


def test_default_state_dir_is_under_home_jimemo_cloudflare(monkeypatch, tmp_path):
    import jimemo.publish.cloudflare_backend as mod

    monkeypatch.setattr(mod.Path, "home", classmethod(lambda cls: tmp_path))
    publisher = CloudflarePublisher(_publish_config(), wrangler=MockWrangler())

    assert publisher._state_dir == tmp_path / ".jimemo" / "cloudflare" / "friend-notes"


def test_module_never_reads_or_logs_cf_token():
    import jimemo.publish.cloudflare_backend as mod

    src = Path(mod.__file__).read_text()
    assert "CLOUDFLARE_API_TOKEN" not in src
    assert "os.environ" not in src


# ---------------------------------------------------------------------------
# SECURITY: the deploy must be an ALLOWLISTED, freshly built directory --
# never the raw state dir. The state dir is user-owned and syncable
# (docs/publish-setup.md tells multi-machine users to sync it via
# git/Dropbox/Syncthing), so it accumulates strays: .git/, .DS_Store,
# editor backups, sync-conflict copies, arbitrary non-hash dirs. The
# middleware passes every non-hash path straight through to Pages' static
# serving, so deploying the raw state dir would publish all of that at
# guessable paths OUTSIDE the unguessable 24-hex namespace -- a git-synced
# state dir would expose its entire .git history publicly.
# ---------------------------------------------------------------------------

def test_publish_deploys_only_allowlisted_content_never_the_raw_state_dir(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    wrangler = TreeCapturingWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler)
    state_dir = tmp_path / "state"
    hash1, hash2 = "ab" * 12, "cd" * 12
    _seed_hash_dir(state_dir, hash1)
    _seed_hash_dir(state_dir, hash2)
    _seed_strays(state_dir)

    url = publisher.publish(html)
    new_hash = _hash_from_url(url)

    deploy_calls = [c for c in wrangler.calls if c[0] == "pages_deploy"]
    assert Path(deploy_calls[-1][2]) != state_dir  # never the raw state dir
    # Exact tree equality: the deployed dir held the allowlisted content
    # and NOTHING else -- no .git, .DS_Store, conflict copy, editor
    # backup, or non-hash "secret/" dir.
    assert wrangler.deployed_trees[-1] == sorted([
        "functions/_middleware.js",
        "_headers",
        "index.html",
        f"{hash1}/index.html",
        f"{hash2}/index.html",
        f"{new_hash}/index.html",
    ])


def test_gc_deploys_only_allowlisted_content_never_the_raw_state_dir(tmp_path):
    wrangler = TreeCapturingWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler, clock=lambda: "2026-07-05T00:00:00.000Z")
    state_dir = tmp_path / "state"
    hash1, hash2 = "ab" * 12, "cd" * 12
    _seed_hash_dir(state_dir, hash1)
    _seed_hash_dir(state_dir, hash2)
    stray_names = _seed_strays(state_dir)
    wrangler.kv_put("ns1", hash1, "2026-07-05T00:00:00.000Z")

    publisher.gc()

    deploy_calls = [c for c in wrangler.calls if c[0] == "pages_deploy"]
    assert len(deploy_calls) == 1
    assert Path(deploy_calls[-1][2]) != state_dir
    assert wrangler.deployed_trees[-1] == sorted([
        "functions/_middleware.js",
        "_headers",
        "index.html",
        f"{hash2}/index.html",
    ])
    # gc removes tombstoned hashes only; the user's synced strays survive.
    for name in stray_names:
        assert (state_dir / name).exists()


def test_publish_leaves_strays_in_state_dir_and_removes_the_deploy_dir(tmp_path):
    """Round-trip: strays are excluded from the DEPLOY, not deleted from
    the user's synced state dir -- and the throwaway deploy dir is gone
    once publish() returns."""
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    wrangler = TreeCapturingWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler)
    state_dir = tmp_path / "state"
    stray_names = _seed_strays(state_dir)

    publisher.publish(html)

    for name in stray_names:
        assert (state_dir / name).exists()
    assert (state_dir / "secret" / "passwords.txt").is_file()
    deploy_calls = [c for c in wrangler.calls if c[0] == "pages_deploy"]
    assert not Path(deploy_calls[-1][2]).exists()  # temp build dir cleaned up


def test_symlinked_hash_dir_is_not_followed_into_the_deploy(tmp_path):
    """A hash-named child must be a REAL directory to deploy. A symlink
    (synced in, or planted to point outside the state dir) is skipped --
    following it would publish arbitrary outside files under a valid
    hash URL."""
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    wrangler = TreeCapturingWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "index.html").write_text("<html>outside</html>")
    (outside / "secrets.txt").write_text("hunter2\n")
    linked_hash = "ef" * 12
    (state_dir / linked_hash).symlink_to(outside)

    url = publisher.publish(html)
    new_hash = _hash_from_url(url)

    tree = wrangler.deployed_trees[-1]
    assert f"{new_hash}/index.html" in tree
    assert not any(p.startswith(linked_hash) for p in tree)


def test_symlink_inside_a_hash_dir_is_not_followed_into_the_deploy(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    wrangler = TreeCapturingWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler)
    state_dir = tmp_path / "state"
    hash1 = "ab" * 12
    _seed_hash_dir(state_dir, hash1)
    loot = tmp_path / "loot.txt"
    loot.write_text("hunter2\n")
    (state_dir / hash1 / "leak.txt").symlink_to(loot)

    publisher.publish(html)

    tree = wrangler.deployed_trees[-1]
    assert f"{hash1}/index.html" in tree
    assert f"{hash1}/leak.txt" not in tree
