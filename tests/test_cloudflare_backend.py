import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.config import CloudflareConfig, PublishConfig
from jimemo.errors import PublishError
from jimemo.publish.cloudflare_backend import CLOUDFLARE_ASSETS_DIR, CloudflarePublisher
from jimemo.publish.wrangler import MockWrangler

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
    wrangler = MockWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler)

    url1 = publisher.publish(html)
    url2 = publisher.publish(html)
    hash1, hash2 = _hash_from_url(url1), _hash_from_url(url2)

    assert hash1 != hash2
    deploy_calls = [c for c in wrangler.calls if c[0] == "pages_deploy"]
    assert len(deploy_calls) == 2
    deployed_dir = Path(deploy_calls[-1][2])
    assert (deployed_dir / hash1 / "index.html").is_file()
    assert (deployed_dir / hash2 / "index.html").is_file()


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
    wrangler = MockWrangler()
    publisher = _publisher(tmp_path, wrangler=wrangler)

    publisher.publish(html)

    deploy_calls = [c for c in wrangler.calls if c[0] == "pages_deploy"]
    deployed_dir = Path(deploy_calls[-1][2])
    assert (deployed_dir / "functions" / "_middleware.js").is_file()


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
