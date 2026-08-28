"""Git state-dir synchronization for the cloudflare backend.

Uses real `git` against a local bare "origin" — the sync contract is
about what git actually does (ff-only pulls, staged deletions, pushes),
so mocking git would test the mock. Every repo sets a local identity so
the suite doesn't depend on the machine's git config.
"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.errors import PublishError
from jimemo.publish import gitsync
from jimemo.publish.cloudflare_backend import CloudflarePublisher
from jimemo.publish.wrangler import MockWrangler

from test_cloudflare_backend import _publish_config, _hash_from_url


def _git(cwd, *args):
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True
    )
    assert result.returncode == 0, f"git {args}: {result.stderr}"
    return result.stdout


def _init_synced_state_dir(tmp_path):
    """A state dir that is a git repo tracking a local bare origin, with
    an initial commit already pushed (so pulls have a remote ref)."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    state = tmp_path / "state"
    state.mkdir()
    _git(state, "init", "-q")
    _git(state, "config", "user.email", "test@example.invalid")
    _git(state, "config", "user.name", "test")
    _git(state, "remote", "add", "origin", str(origin))
    (state / ".gitkeep").write_text("")
    _git(state, "add", ".gitkeep")
    _git(state, "commit", "-q", "-m", "init")
    _git(state, "push", "-q", "-u", "origin", "HEAD")
    return state, origin


def _clone(origin, dest):
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(dest)], check=True
    )
    _git(dest, "config", "user.email", "test@example.invalid")
    _git(dest, "config", "user.name", "test")
    return dest


def _publisher(state_dir, wrangler=None):
    return CloudflarePublisher(
        _publish_config(),
        wrangler=wrangler or MockWrangler(),
        state_dir=state_dir,
        clock=lambda: "2026-08-28T00:00:00.000Z",
    )


def test_plain_state_dir_is_not_synced(tmp_path):
    assert not gitsync.is_synced_repo(tmp_path)


def test_repo_without_origin_is_not_synced(tmp_path):
    _git_dir = tmp_path / "state"
    _git_dir.mkdir()
    _git(_git_dir, "init", "-q")
    assert not gitsync.is_synced_repo(_git_dir)


def test_state_dir_inside_a_larger_repo_is_not_synced(tmp_path):
    outer = tmp_path / "outer"
    (outer / "state").mkdir(parents=True)
    _git(outer, "init", "-q")
    _git(outer, "remote", "add", "origin", str(tmp_path / "nowhere.git"))
    assert not gitsync.is_synced_repo(outer / "state")


def test_publish_commits_and_pushes_the_new_hash(tmp_path):
    state, origin = _init_synced_state_dir(tmp_path)
    html = tmp_path / "page.html"
    html.write_text("<html></html>")

    url = _publisher(state).publish(html)
    page_hash = _hash_from_url(url)

    # committed locally, tree clean for the touched paths
    assert page_hash in _git(state, "log", "--oneline", "-1")
    # and pushed: a fresh clone of origin has the hash content
    other = _clone(origin, tmp_path / "other")
    assert (other / page_hash / "index.html").is_file()


def test_publish_refuses_when_pull_cannot_fast_forward(tmp_path):
    state, origin = _init_synced_state_dir(tmp_path)
    # someone else pushes; this copy also commits independently -> divergence
    other = _clone(origin, tmp_path / "other")
    (other / "from-other.txt").write_text("x")
    _git(other, "add", "from-other.txt")
    _git(other, "commit", "-q", "-m", "other machine")
    _git(other, "push", "-q", "origin", "HEAD")
    (state / "local.txt").write_text("y")
    _git(state, "add", "local.txt")
    _git(state, "commit", "-q", "-m", "local divergence")

    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    wrangler = MockWrangler()
    with pytest.raises(PublishError, match="diverged from origin"):
        _publisher(state, wrangler).publish(html)
    # refused before staging or deploying anything
    assert not [c for c in wrangler.calls if c[0] == "pages_deploy"]


def test_publish_refuses_when_origin_unreachable(tmp_path):
    state, origin = _init_synced_state_dir(tmp_path)
    _git(state, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    with pytest.raises(PublishError, match="refusing to deploy"):
        _publisher(state).publish(html)


def test_publish_pulls_other_machines_hashes_before_deploy(tmp_path):
    state, origin = _init_synced_state_dir(tmp_path)
    # another machine publishes a hash and pushes it
    other = _clone(origin, tmp_path / "other")
    foreign = "ab" * 12
    (other / foreign).mkdir()
    (other / foreign / "index.html").write_text("<html>foreign</html>")
    _git(other, "add", foreign)
    _git(other, "commit", "-q", "-m", f"publish {foreign}")
    _git(other, "push", "-q", "origin", "HEAD")

    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    _publisher(state).publish(html)
    # the pull brought the foreign hash into this machine's state dir,
    # so the deploy shipped the union
    assert (state / foreign / "index.html").is_file()


def test_no_sync_skips_pull_and_push(tmp_path):
    state, origin = _init_synced_state_dir(tmp_path)
    _git(state, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    html = tmp_path / "page.html"
    html.write_text("<html></html>")

    publisher = CloudflarePublisher(
        _publish_config(),
        wrangler=MockWrangler(),
        state_dir=state,
        clock=lambda: "ts",
        no_sync=True,
    )
    url = publisher.publish(html)  # unreachable origin, but no pull happens
    page_hash = _hash_from_url(url)
    # nothing committed: HEAD still the init commit
    assert page_hash not in _git(state, "log", "--oneline")


def test_push_failure_warns_but_publish_succeeds(tmp_path, capsys):
    state, origin = _init_synced_state_dir(tmp_path)
    html = tmp_path / "page.html"
    html.write_text("<html></html>")

    publisher = _publisher(state)
    # break origin AFTER construction; the pull uses the fetch URL too,
    # so break only the push URL to isolate the push failure
    _git(state, "remote", "set-url", "--push", "origin", str(tmp_path / "gone.git"))
    url = publisher.publish(html)
    page_hash = _hash_from_url(url)

    assert page_hash in _git(state, "log", "--oneline", "-1")  # local commit kept
    assert "push did not land" in capsys.readouterr().err


def test_gc_commits_deletions(tmp_path):
    state, origin = _init_synced_state_dir(tmp_path)
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    wrangler = MockWrangler()
    publisher = _publisher(state, wrangler)

    url = publisher.publish(html)
    page_hash = _hash_from_url(url)
    publisher.purge(page_hash)
    assert publisher.gc() == 1

    assert not (state / page_hash).exists()
    # the deletion is committed and pushed: a fresh clone lacks the hash
    other = _clone(origin, tmp_path / "other")
    assert not (other / page_hash).exists()
    assert "gc: remove 1" in _git(other, "log", "--oneline", "-1")


def test_empty_origin_is_not_an_error(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    state = tmp_path / "state"
    state.mkdir()
    _git(state, "init", "-q")
    _git(state, "config", "user.email", "test@example.invalid")
    _git(state, "config", "user.name", "test")
    _git(state, "remote", "add", "origin", str(origin))

    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    url = _publisher(state).publish(html)  # brand-new sync repo: no remote ref yet
    assert _hash_from_url(url) in _git(state, "log", "--oneline", "-1")


def test_broken_opt_in_repo_fails_closed(tmp_path):
    """A .git marker the tool cannot read means the operator asked for
    sync; deploying without it would recreate the wipe hazard."""
    state = tmp_path / "state"
    state.mkdir()
    (state / ".git").write_text("gitdir: /nonexistent/nowhere")
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    wrangler = MockWrangler()
    with pytest.raises(PublishError, match="refusing to deploy"):
        _publisher(state, wrangler).publish(html)
    assert not [c for c in wrangler.calls if c[0] == "pages_deploy"]


def test_dirty_tracked_tree_refuses_deploy(tmp_path):
    """A locally deleted tracked hash would silently vanish from
    production on the next wholesale deploy — refuse instead."""
    state, origin = _init_synced_state_dir(tmp_path)
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    publisher = _publisher(state)
    url = publisher.publish(html)
    page_hash = _hash_from_url(url)

    import shutil as _shutil
    _shutil.rmtree(state / page_hash)  # local, uncommitted deletion

    wrangler = MockWrangler()
    with pytest.raises(PublishError, match="uncommitted changes to tracked"):
        _publisher(state, wrangler).publish(html)
    assert not [c for c in wrangler.calls if c[0] == "pages_deploy"]


def test_commit_lands_on_origin_before_deploy(tmp_path):
    """The ordering that makes the race safe: by the time wrangler
    deploys, the new hash is already committed AND on origin."""
    state, origin = _init_synced_state_dir(tmp_path)
    html = tmp_path / "page.html"
    html.write_text("<html></html>")

    seen = {}

    class OrderProbe(MockWrangler):
        def pages_deploy(self, project, directory, branch="main"):
            probe = subprocess.run(
                ["git", "-C", str(origin), "log", "--oneline", "-1"],
                capture_output=True, text=True,
            )
            seen["origin_tip_at_deploy"] = probe.stdout
            return super().pages_deploy(project, directory, branch)

    url = _publisher(state, OrderProbe()).publish(html)
    assert _hash_from_url(url) in seen["origin_tip_at_deploy"]


def test_racing_push_is_rebased_and_union_deployed(tmp_path):
    """A commit that raced us onto origin between our pull and our push
    is folded in by the rebase retry; the deploy ships the union."""
    state, origin = _init_synced_state_dir(tmp_path)
    html = tmp_path / "page.html"
    html.write_text("<html></html>")

    other = _clone(origin, tmp_path / "other")
    foreign = "cd" * 12

    class RaceInjector(MockWrangler):
        """Simulates the race by pushing from the other clone the first
        time the publisher's own push would run — via kv namespace probe
        hooking is impossible, so inject on deploy? No: inject before
        push by making the first push fail is git-level. Instead push
        the foreign commit NOW, after the publisher pulled? The pull
        happens inside publish(), so push the racing commit here, in
        pages_deploy — too late. Simplest honest race: push it before
        publish, after a manual pull of state."""

    # Deterministic equivalent of the race: state is current (pulled),
    # then the other machine pushes, then our commit_and_push runs and
    # its first push is rejected -> fetch + rebase retry must fold the
    # foreign commit in and push the union.
    (other / foreign).mkdir()
    (other / foreign / "index.html").write_text("<html>foreign</html>")
    _git(other, "add", foreign)
    _git(other, "commit", "-q", "-m", f"publish {foreign}")

    from jimemo.publish import gitsync as gs

    original_pull = gs.pull_before_deploy

    def pull_then_race(state_dir):
        original_pull(state_dir)
        _git(other, "push", "-q", "origin", "HEAD")

    gs.pull_before_deploy = pull_then_race
    try:
        url = _publisher(state).publish(html)
    finally:
        gs.pull_before_deploy = original_pull

    page_hash = _hash_from_url(url)
    # both pages now on origin, and both present locally for the deploy
    fresh = _clone(origin, tmp_path / "fresh")
    assert (fresh / page_hash / "index.html").is_file()
    assert (fresh / foreign / "index.html").is_file()
    assert (state / foreign / "index.html").is_file()


def test_prestaged_unrelated_file_refuses_like_any_tracked_change(tmp_path):
    """A pre-staged file is a tracked change: the dirty-tree gate
    refuses the publish before anything could sweep it in."""
    state, origin = _init_synced_state_dir(tmp_path)
    (state / "unrelated.txt").write_text("do not commit me")
    _git(state, "add", "unrelated.txt")  # pre-staged by hand

    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    with pytest.raises(PublishError, match="uncommitted changes to tracked"):
        _publisher(state).publish(html)


def test_commit_and_push_is_pathspec_limited(tmp_path):
    """Defense in depth below the dirty gate: commit_and_push itself
    never sweeps a pre-staged unrelated file into its commit."""
    from jimemo.publish import gitsync as gs

    state, origin = _init_synced_state_dir(tmp_path)
    (state / "unrelated.txt").write_text("do not commit me")
    _git(state, "add", "unrelated.txt")
    page = "ab" * 12
    (state / page).mkdir()
    (state / page / "index.html").write_text("<html></html>")

    assert gs.commit_and_push(state, [f"{page}/"], f"publish {page}") is True

    files = _git(state, "show", "--name-only", "--format=", "HEAD").split()
    assert f"{page}/index.html" in files
    assert "unrelated.txt" not in files
    # still staged, untouched, for the human who staged it
    assert "unrelated.txt" in _git(state, "diff", "--cached", "--name-only")


def test_gc_after_no_sync_publish_does_not_trip_pathspec(tmp_path):
    """A hash staged by a --no-sync publish is untracked; after gc
    deletes it there is nothing for git to record — the run must not
    die on git's unmatched-pathspec error."""
    state, origin = _init_synced_state_dir(tmp_path)
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    wrangler = MockWrangler()

    nosync = CloudflarePublisher(
        _publish_config(), wrangler=wrangler, state_dir=state,
        clock=lambda: "ts", no_sync=True,
    )
    url = nosync.publish(html)
    page_hash = _hash_from_url(url)

    synced = _publisher(state, wrangler)
    synced.purge(page_hash)
    assert synced.gc() == 1
    assert not (state / page_hash).exists()


def test_setup_deploy_pulls_the_union_first(tmp_path, monkeypatch):
    """A setup re-run from a stale clone must not wipe remote-only
    pages: its deploy step pulls before installing assets + deploying."""
    from jimemo.publish.setup import run_setup

    state, origin = _init_synced_state_dir(tmp_path)
    other = _clone(origin, tmp_path / "other")
    foreign = "ef" * 12
    (other / foreign).mkdir()
    (other / foreign / "index.html").write_text("<html>foreign</html>")
    _git(other, "add", foreign)
    _git(other, "commit", "-q", "-m", f"publish {foreign}")
    _git(other, "push", "-q", "origin", "HEAD")

    class ScriptedIO:
        def __init__(self):
            self.lines = []

        def print(self, *a, **k):
            self.lines.append(" ".join(str(x) for x in a))

        def prompt(self, message, default=None):
            return default or "acct-or-ns-id"

        def confirm(self, message, default=False):
            return True

    import jimemo.publish.cloudflare_backend as cf_mod

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setattr(
        "jimemo.publish.setup._default_state_dir",
        lambda project: state,
    )
    monkeypatch.setattr(
        cf_mod, "_default_state_dir", lambda project: state
    )
    wrangler = MockWrangler()
    config_path = tmp_path / "config.toml"
    run_setup(False, wrangler, config_path, ScriptedIO())

    # the stale clone now holds the other machine's page: the deploy
    # shipped the union, and the asset refresh is committed + pushed
    assert (state / foreign / "index.html").is_file()
    fresh = _clone(origin, tmp_path / "fresh2")
    assert (fresh / "functions" / "_middleware.js").is_file()
