"""Optional git synchronization for the cloudflare backend's state dir.

The cloudflare backend deploys its whole local state directory
(`~/.jimemo/cloudflare/<project>/`) wholesale, so the live site is only
ever as complete as the machine that deployed last. Publishing from a
second machine whose copy is missing hashes silently 404s them — the
wipe-by-deploy hazard notes.ito.com hit in production three times before
fixing it with git as the sync channel (its kata `notes-ito-com#v8f0`).
This module ports that fix, with the same ordering:

1. **Refuse a dirty tree, then pull, before staging.** Local edits or
   deletions of tracked files would silently reach production on the
   next wholesale deploy, so they refuse; the fast-forward pull makes
   the copy the union of every machine's pages. A pull that cannot
   fast-forward, or cannot reach origin, refuses too.
2. **Commit + push BEFORE deploying.** The mutation lands on origin
   first; a push rejected by a racing machine is retried once via
   fetch + rebase. If the push still does not land, the deploy only
   proceeds when a freshness re-check proves origin has nothing newer —
   otherwise it refuses, leaving the change safely committed locally
   for the next successful publish to carry.
3. Deploy, then re-check origin: two machines CAN still finish
   wholesale deploys in an order inconsistent with git history (git
   alone cannot serialize the CDN upload — the reference implementation
   has the same residual window). The post-deploy check turns that
   silent race into a loud warning naming the fix: any synced
   publish/gc redeploys the union.

Opt-in: make the state dir itself a git repo with an `origin` remote.
A state dir that is not one behaves exactly as before and git is never
invoked; a state dir that IS one but is broken (unreadable repo, no
resolvable remote branch) fails CLOSED — a machine that asked for the
protection never silently deploys without it. `--no-sync` skips every
step above for one deliberate deploy.

Commits are pathspec-limited to the touched paths, so unrelated files —
dirty or even pre-staged — in the state dir are never swept in.
Everything shells out to the `git` CLI (list-form argv, never
shell=True): git stays the authority on merging, rebasing, and auth.
"""
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from ..errors import PublishError

_GIT_TIMEOUT_S = 120
_HASH_DIR_RE = re.compile(r"^[a-f0-9]{24}/?$")


def _git(state_dir: Path, args: Sequence[str]) -> "subprocess.CompletedProcess":
    try:
        return subprocess.run(
            ["git", "-C", str(state_dir), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublishError(
            f"git {' '.join(args[:2])} failed to run in {state_dir}: {exc}"
        ) from exc


def _detail(result) -> str:
    return ((result.stderr or "") + (result.stdout or "")).strip()[:300]


def is_synced_repo(state_dir: Path) -> bool:
    """True when the state dir itself is a git work-tree root with an
    `origin` remote — the opt-in signal for sync.

    The root check matters: a state dir merely *inside* some larger repo
    (a home directory under version control, say) must not start pushing
    page content there. And a state dir that carries a `.git` marker but
    cannot be inspected fails CLOSED (PublishError), never open: the
    marker says the operator asked for sync, so silently deploying
    without it would recreate the exact wipe hazard.
    """
    state_dir = Path(state_dir)
    if not (state_dir / ".git").exists():
        return False
    inside = _git(state_dir, ["rev-parse", "--is-inside-work-tree"])
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise PublishError(
            f"{state_dir} has a .git entry but git cannot read it "
            f"({_detail(inside)}); refusing to deploy without the sync "
            "protection it asks for. Repair or remove the repo, or pass "
            "--no-sync."
        )
    remotes = _git(state_dir, ["remote"])
    if remotes.returncode != 0:
        raise PublishError(
            f"could not list git remotes in {state_dir} "
            f"({_detail(remotes)}); refusing to deploy. Repair the repo "
            "or pass --no-sync."
        )
    return "origin" in remotes.stdout.split()


def _remote_branch(state_dir: Path) -> Optional[str]:
    """The branch every machine converges on: origin's default (HEAD)
    branch. None when origin is truly empty (no refs at all — a
    brand-new sync repo). A remote that has branches but no resolvable
    HEAD fails closed rather than guessing.
    """
    ls = _git(state_dir, ["ls-remote", "--symref", "origin"])
    if ls.returncode != 0:
        raise PublishError(
            f"cannot reach origin from {state_dir} ({_detail(ls)}); "
            "refusing to deploy a possibly stale copy. Reconnect, or pass "
            "--no-sync for a deliberate deploy of exactly this copy."
        )
    if not ls.stdout.strip():
        return None
    for line in ls.stdout.splitlines():
        if line.startswith("ref:") and line.endswith("\tHEAD"):
            # "ref: refs/heads/<branch>\tHEAD" — branch names may
            # themselves contain slashes, so strip the prefix rather
            # than splitting on "/".
            ref = line.split()[1]
            prefix = "refs/heads/"
            if ref.startswith(prefix):
                return ref[len(prefix):]
    raise PublishError(
        "origin has branches but no resolvable default (HEAD) branch; "
        "refusing to guess which branch to sync. Set the default branch "
        "on the hosting side (e.g. the repo settings page, or `git "
        "symbolic-ref HEAD refs/heads/<branch>` in the bare repo), or "
        "pass --no-sync."
    )


def refuse_dirty(state_dir: Path) -> None:
    """Refuse to proceed when tracked files in the state dir are
    locally modified or deleted — a successful pull cannot see those,
    and the wholesale deploy would silently ship them (deleting a
    tracked live hash locally would remove that page from production).
    Untracked non-page strays only warn (they never deploy or
    commit); untracked HASH directories are not handled here — the
    caller adopts them into its next synced commit via
    untracked_hash_dirs(), so a `--no-sync` page does not stay
    invisible to other machines forever.
    """
    state_dir = Path(state_dir)
    status = _git(state_dir, ["status", "--porcelain"])
    if status.returncode != 0:
        raise PublishError(
            f"git status failed in {state_dir} ({_detail(status)}); "
            "refusing to deploy. Repair the repo or pass --no-sync."
        )
    tracked_changes = []
    untracked = []
    for line in status.stdout.splitlines():
        if not line.strip():
            continue
        if line.startswith("??"):
            untracked.append(line[3:])
        else:
            tracked_changes.append(line)
    if tracked_changes:
        listing = "\n  ".join(tracked_changes[:10])
        raise PublishError(
            "the state dir has uncommitted changes to tracked files:\n"
            f"  {listing}\n"
            "A wholesale deploy would silently ship them (a deleted "
            "tracked hash would remove that live page). Commit or restore "
            f"them in {state_dir}, or pass --no-sync."
        )
    strays = [u for u in untracked if not _is_hash_dir(u)]
    if strays:
        print(
            f"warning: {len(strays)} untracked non-page path(s) in the "
            "state dir are never deployed or committed: "
            + ", ".join(strays[:5]),
            file=sys.stderr,
        )


def _is_hash_dir(porcelain_path: str) -> bool:
    return bool(_HASH_DIR_RE.match(porcelain_path.strip().strip('"')))


def untracked_hash_dirs(state_dir: Path) -> List[str]:
    """Hash directories on disk that git does not know about — pages an
    earlier `--no-sync` publish (or an interrupted run) left behind.
    They deploy from this machine but are invisible to every other
    machine, so a synced mutation ADOPTS them into its commit: the
    escape hatch stays one deploy, not a permanent divergence.
    """
    state_dir = Path(state_dir)
    status = _git(state_dir, ["status", "--porcelain"])
    if status.returncode != 0:
        raise PublishError(
            f"git status failed in {state_dir} ({_detail(status)}); "
            "refusing to deploy. Repair the repo or pass --no-sync."
        )
    found = []
    for line in status.stdout.splitlines():
        if line.startswith("??") and _is_hash_dir(line[3:]):
            found.append(line[3:].strip().strip('"').rstrip("/"))
    return found


def pull_before_deploy(state_dir: Path) -> None:
    """Fast-forward the state dir to origin's default branch so the
    coming deploy ships the union of every machine's pages. Refuses
    (PublishError) on divergence or an unreachable origin; an origin
    with no commits yet is fine.
    """
    state_dir = Path(state_dir)
    branch = _remote_branch(state_dir)
    if branch is None:
        return
    fetch = _git(state_dir, ["fetch", "--quiet", "origin", branch])
    if fetch.returncode != 0:
        raise PublishError(
            f"git fetch origin {branch} failed ({_detail(fetch)}); "
            "refusing to deploy a possibly stale copy. Reconnect, or pass "
            "--no-sync."
        )
    behind = _count(state_dir, "HEAD..FETCH_HEAD")
    if behind == 0:
        return  # up to date, possibly ahead — the push shares that
    ahead = _count(state_dir, "FETCH_HEAD..HEAD")
    if ahead:
        raise PublishError(
            f"the state dir has diverged from origin/{branch} "
            f"({ahead} local commit(s), {behind} remote); a fast-forward "
            "is not possible and deploying now could wipe newer pages. "
            f"Reconcile {state_dir} (git rebase origin/{branch}), or pass "
            "--no-sync."
        )
    merge = _git(state_dir, ["merge", "--ff-only", "--quiet", "FETCH_HEAD"])
    if merge.returncode != 0:
        raise PublishError(
            f"fast-forward onto origin/{branch} failed ({_detail(merge)}); "
            f"refusing to deploy. Resolve in {state_dir}, or pass --no-sync."
        )


def _count(state_dir: Path, range_spec: str) -> int:
    result = _git(state_dir, ["rev-list", "--count", range_spec])
    if result.returncode != 0:
        # An unborn HEAD (fresh `git init`, no commits yet) legitimately
        # cannot be compared — there is nothing local to diverge with.
        # Any other rev-list failure fails CLOSED: an unprovable
        # comparison must never become permission to deploy.
        head = _git(state_dir, ["rev-parse", "--verify", "--quiet", "HEAD"])
        if head.returncode != 0:
            return 0
        raise PublishError(
            f"git rev-list {range_spec} failed in {state_dir} "
            f"({_detail(result)}); refusing to deploy on an unprovable "
            "comparison. Repair the repo, or pass --no-sync."
        )
    return int(result.stdout.strip() or "0")


def verify_fresh(state_dir: Path) -> None:
    """After a push that did not land: allow the deploy only if origin
    still has nothing this copy lacks (the failure was connectivity, not
    a racing machine). Otherwise refuse — the change is committed
    locally and the next successful publish carries it.
    """
    state_dir = Path(state_dir)
    branch = _remote_branch(state_dir)
    if branch is None:
        return
    fetch = _git(state_dir, ["fetch", "--quiet", "origin", branch])
    if fetch.returncode != 0 or _count(state_dir, "HEAD..FETCH_HEAD"):
        raise PublishError(
            "the push did not land and origin could not be proven "
            "current, so deploying now could wipe another machine's "
            "pages. This change is committed locally and will deploy "
            "with the next successful publish; reconcile "
            f"{state_dir} (git pull --rebase && git push), or pass "
            "--no-sync to force this copy."
        )


def commit_and_push(state_dir: Path, paths: Sequence[str], message: str) -> bool:
    """Stage and commit exactly `paths` (additions and deletions), then
    push to origin's default branch, retrying a rejected push once via
    fetch + rebase (a racing machine pushed first; the rebase folds
    their pages in). Returns True when the branch is on origin
    afterward; False (with a stderr warning) when the push did not land —
    the caller decides whether deploying is still safe (verify_fresh).

    The commit is pathspec-limited, so a pre-staged unrelated file in
    the index is not swept in. Paths that neither exist on disk nor are
    known to git (a hash staged by an earlier `--no-sync` run and since
    gc'd) are skipped rather than tripping git's unmatched-pathspec
    error.
    """
    state_dir = Path(state_dir)
    known = _known_paths(state_dir, paths)
    if known:
        add = _git(state_dir, ["add", "--", *known])
        if add.returncode != 0:
            raise PublishError(f"state-dir git add failed: {_detail(add)}")
        pending = _git(state_dir, ["status", "--porcelain", "--", *known])
        if pending.returncode != 0:
            raise PublishError(
                f"state-dir git status failed: {_detail(pending)}"
            )
        if pending.stdout.strip():
            commit = _git(
                state_dir,
                ["commit", "--quiet", "-m", message, "--", *known],
            )
            if commit.returncode != 0:
                raise PublishError(
                    f"state-dir git commit failed: {_detail(commit)}"
                )
    return _push_with_rebase_retry(state_dir)


def _known_paths(state_dir: Path, paths: Sequence[str]) -> List[str]:
    known = []
    for p in paths:
        rel = p.rstrip("/")
        if (state_dir / rel).exists():
            known.append(rel)
            continue
        tracked = _git(state_dir, ["ls-files", "--", rel])
        if tracked.returncode != 0:
            raise PublishError(
                f"state-dir git ls-files failed: {_detail(tracked)}"
            )
        if tracked.stdout.strip():
            known.append(rel)
    return known


def _push_with_rebase_retry(state_dir: Path) -> bool:
    branch = _remote_branch(state_dir)
    target = branch if branch is not None else _local_branch(state_dir)
    push = _git(
        state_dir, ["push", "--quiet", "origin", f"HEAD:refs/heads/{target}"]
    )
    if push.returncode == 0:
        return True
    fetch = _git(state_dir, ["fetch", "--quiet", "origin", target])
    if fetch.returncode == 0:
        rebase = _git(state_dir, ["rebase", "--quiet", f"origin/{target}"])
        if rebase.returncode != 0:
            _git(state_dir, ["rebase", "--abort"])
            _warn_unpushed(state_dir, "rebase onto origin did not apply cleanly")
            return False
        push2 = _git(
            state_dir, ["push", "--quiet", "origin", f"HEAD:refs/heads/{target}"]
        )
        if push2.returncode == 0:
            return True
        _warn_unpushed(state_dir, _detail(push2))
        return False
    _warn_unpushed(state_dir, _detail(fetch) or _detail(push))
    return False


def _local_branch(state_dir: Path) -> str:
    result = _git(state_dir, ["rev-parse", "--abbrev-ref", "HEAD"])
    name = result.stdout.strip() if result.returncode == 0 else ""
    return name or "main"


def _warn_unpushed(state_dir: Path, detail: str) -> None:
    print(
        f"warning: state-dir git push did not land ({detail}); the commit "
        "is local-only. Other machines cannot see this change until "
        f"`git -C {state_dir} push` succeeds.",
        file=sys.stderr,
    )


def warn_if_origin_moved_during_deploy(state_dir: Path) -> bool:
    """Concurrent wholesale deploys cannot be serialized through git
    alone: after OUR deploy returns, another machine may have pushed
    (and deployed) meanwhile, and whichever wrangler call finished last
    now owns production. Detect it instead of missing it: when origin
    moved past our HEAD during the deploy, say so and name the fix (any
    synced publish/gc redeploys the union). Returns True when the
    warning fired. Detection is best-effort — an unreachable origin
    here changes nothing about what was already deployed."""
    state_dir = Path(state_dir)
    try:
        branch = _remote_branch(state_dir)
        if branch is None:
            return False
        fetch = _git(state_dir, ["fetch", "--quiet", "origin", branch])
        if fetch.returncode != 0:
            return False
        moved = _count(state_dir, "HEAD..FETCH_HEAD")
    except PublishError:
        return False
    if not moved:
        return False
    print(
        "warning: another machine pushed while this deploy was running; "
        "production may be missing whichever machine deployed first. "
        "Run `jimemo publish gc` (or any synced publish) on either "
        "machine to redeploy the union.",
        file=sys.stderr,
    )
    return True


def touched_asset_paths() -> List[str]:
    """The baseline asset paths publish()'s self-heal may (re)create,
    for staging alongside a new hash directory."""
    return ["functions/_middleware.js", "_headers", "index.html"]
