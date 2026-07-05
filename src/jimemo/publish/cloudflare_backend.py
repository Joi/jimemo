"""The "cloudflare" publish backend: native Cloudflare Pages + KV, driven
entirely through the Wrangler seam (wrangler.py) -- no other external CLI.
This is the backend Task 5's `jimemo publish setup` wizard provisions for
a friend who doesn't already have a publish site of their own (unlike
Joi, who keeps notes-publish/notes.ito.com authoritative via the
`command` backend instead).

Why this backend keeps local state (read this before changing publish()):
a `wrangler pages deploy <dir>` replaces the CURRENT production
deployment wholesale -- files not present in <dir> stop being served the
moment the next deploy lands. But every published hash must stay
reachable at `{base_url}/<hash>/` until it is explicitly purged (mirroring
notes-ito-com's model: purging only tombstones a hash in KV, it does not
remove the underlying files -- `gc` is the separate, explicit step that
deletes them and shrinks the next deploy). If publish() deployed only the
newly staged hash each time, the SECOND publish() call would silently
make the FIRST hash's URL 404, since the whole production tree would be
replaced by a directory containing just the new hash. So this backend
keeps a local, persistent staging directory (under `~/.jimemo/cloudflare/
<project>/` by default) that accumulates every hash it has ever staged,
and redeploys that accumulated content on every publish -- the same shape
as notes-ito-com's git-committed `public/` tree, just kept under
`~/.jimemo/` instead of a git repo, since jimemo has no repo of its own
to commit into.

What actually gets handed to wrangler, though, is never the raw state
directory: each deploy assembles a throwaway, strictly allowlisted copy
of it first (see _build_deploy_dir). The state dir is user-owned and
syncable (docs/publish-setup.md tells multi-machine users to sync it via
git/Dropbox/Syncthing), so it accumulates strays -- `.git/`, `.DS_Store`,
editor backups, sync-conflict copies -- and deploying it raw would serve
all of them publicly at guessable paths outside the unguessable hash
namespace. The allowlisted copy makes that leak structurally impossible
while the persistent state dir stays the syncable source of truth.
"""
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union
from urllib.parse import urlparse

from .._paths import REPO_ROOT
from ..config import PublishConfig
from ..errors import PublishError
from . import Publisher
from .staging import stage_page
from .wrangler import NO_WRANGLER_MESSAGE, Wrangler

_HASH_RE = re.compile(r"^[a-f0-9]{24}$")

#: publish/cloudflare/ -- the middleware + _headers + index.html source
#: installed into every project's local state directory, both by
#: setup.py's wizard (unconditionally) and by this module's own publish()
#: (self-heal, only when missing -- see _ensure_state_dir_assets). Defined
#: here rather than in setup.py so both callers share one copy of the
#: install logic instead of two implementations that could drift.
CLOUDFLARE_ASSETS_DIR = REPO_ROOT / "publish" / "cloudflare"

Clock = Callable[[], str]


def _now_iso() -> str:
    """UTC timestamp, millisecond precision, "Z" suffix -- matches the
    format JS's `Date.prototype.toISOString()` produces, which is what
    the ported middleware (publish/cloudflare/_middleware.js) writes when
    a purge happens through its own `?purge` POST route. Keeping both
    write paths' timestamp format the same is cosmetic (the middleware
    only ever displays this string, never parses it back into a date),
    but it means the same KV field doesn't look inconsistent depending on
    which purge path wrote it.
    """
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _extract_hash(hash_or_url: str) -> str:
    """Accept either a bare 24-hex-char hash or a full published URL;
    return the hash. Mirrors notes-ito-com's bin/notes-publish
    `parse_hash`: strip a URL down to its path, take the first path
    segment, and require it to look like a hash."""
    s = hash_or_url.strip()
    if "://" in s:
        s = urlparse(s).path
    s = s.strip("/").split("/", 1)[0]
    if not _HASH_RE.match(s):
        raise PublishError(f"not a valid 24-hex-char hash: {hash_or_url!r}")
    return s


def _default_state_dir(project: str) -> Path:
    return Path.home() / ".jimemo" / "cloudflare" / project


def _install_state_dir_assets(state_dir: Union[str, Path]) -> None:
    """Copy CLOUDFLARE_ASSETS_DIR's middleware + _headers + index.html
    into the persistent state directory, in the layout Cloudflare Pages
    actually requires: Functions are only picked up from a ``functions/``
    directory at the deploy root, so ``_middleware.js`` goes to
    ``<state_dir>/functions/_middleware.js`` -- NOT flattened at the
    state dir's root the way it sits in the repo. ``_headers`` and
    ``index.html`` stay at the state dir's root.

    Shared by two callers -- setup.py's wizard (unconditionally, so
    re-running setup after a jimemo upgrade refreshes the deployed
    middleware) and this module's own publish(), via
    _ensure_state_dir_assets (only when the middleware is missing) -- so
    the copy logic itself lives in exactly one place.

    Safe to re-run: always overwrites with the current bundled source.
    """
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    functions_dir = state_dir / "functions"
    functions_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        CLOUDFLARE_ASSETS_DIR / "_middleware.js", functions_dir / "_middleware.js"
    )
    shutil.copyfile(CLOUDFLARE_ASSETS_DIR / "_headers", state_dir / "_headers")
    shutil.copyfile(CLOUDFLARE_ASSETS_DIR / "index.html", state_dir / "index.html")


def _ensure_state_dir_assets(state_dir: Union[str, Path]) -> None:
    """Self-heal: install any of the baseline functions/_middleware.js +
    _headers + index.html that are missing from ``state_dir`` -- e.g.
    `jimemo publish setup` was interrupted before it finished, the state
    directory was deleted or partially copied between machines, or some
    other partial state -- without clobbering whichever of the three are
    already installed.

    Checks all three files individually rather than just the middleware:
    a state dir can have functions/_middleware.js but be missing _headers
    (losing the noindex/noarchive headers on every live page) or
    index.html, e.g. if a partial copy or an interrupted setup dropped
    only some of the baseline. Each missing file is reinstalled from
    CLOUDFLARE_ASSETS_DIR independently; a file that's already present is
    left untouched (so a hand-edited or otherwise customized copy of any
    one of the three survives a self-heal of the other two).

    Called by publish() and gc() before every deploy: both always
    redeploy the state directory's whole allowlisted content (see module
    docstring and _build_deploy_dir), so a
    deploy that goes out missing functions/_middleware.js has no
    tombstone Function at all -- every hash ever published to that
    project starts serving ``?purge`` as a silent no-op (or a 405) with
    no warning -- and one missing _headers or index.html means the live
    site silently loses the noindex/noarchive headers or the tombstone
    landing page.
    """
    state_dir = Path(state_dir)
    functions_dir = state_dir / "functions"
    middleware = functions_dir / "_middleware.js"
    headers = state_dir / "_headers"
    index_html = state_dir / "index.html"

    if middleware.is_file() and headers.is_file() and index_html.is_file():
        return

    functions_dir.mkdir(parents=True, exist_ok=True)
    if not middleware.is_file():
        shutil.copyfile(CLOUDFLARE_ASSETS_DIR / "_middleware.js", middleware)
    if not headers.is_file():
        shutil.copyfile(CLOUDFLARE_ASSETS_DIR / "_headers", headers)
    if not index_html.is_file():
        shutil.copyfile(CLOUDFLARE_ASSETS_DIR / "index.html", index_html)


def _ignore_symlinks(src: Union[str, Path], names: List[str]) -> List[str]:
    """``shutil.copytree`` ignore= callback: skip symlinks. A synced-in
    symlink inside a hash directory would otherwise be FOLLOWED by the
    copy (copytree's default materializes link targets), pulling files
    from outside the state dir into a public deploy."""
    return [n for n in names if (Path(src) / n).is_symlink()]


def _build_deploy_dir(state_dir: Path, deploy_dir: Path) -> Path:
    """Copy ONLY the expected, allowlisted deployable content of
    ``state_dir`` into ``deploy_dir`` (a fresh, empty directory) and
    return ``deploy_dir``:

    - ``functions/_middleware.js`` (the tombstone/purge Function)
    - ``_headers``
    - ``index.html``
    - every child that is a real directory (not a symlink) whose name is
      exactly a 24-hex-char hash -- each staged page, with any assets
      inside it, symlinks skipped (see _ignore_symlinks)

    Nothing else: a strict allowlist, not a denylist. Dotfiles/dotdirs
    (``.git/``, ``.DS_Store``), editor backups (``page.html~``,
    ``#page#``), sync-conflict copies (``index (conflicted copy).html``),
    non-hash-named directories, and stray root files all stay behind in
    the state dir -- excluded from the deploy, never deleted. Deploying
    the raw state dir instead would publish every one of them: the
    middleware passes non-hash paths straight through to Pages' static
    serving, at guessable paths outside the unguessable hash namespace
    (a git-synced state dir would expose its entire ``.git`` history).

    Callers run _ensure_state_dir_assets first, so the three baseline
    files are guaranteed present in ``state_dir``.
    """
    functions_dir = deploy_dir / "functions"
    functions_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        state_dir / "functions" / "_middleware.js",
        functions_dir / "_middleware.js",
    )
    shutil.copyfile(state_dir / "_headers", deploy_dir / "_headers")
    shutil.copyfile(state_dir / "index.html", deploy_dir / "index.html")
    for child in state_dir.iterdir():
        if (
            _HASH_RE.match(child.name)
            and child.is_dir()
            and not child.is_symlink()
        ):
            shutil.copytree(child, deploy_dir / child.name, ignore=_ignore_symlinks)
    return deploy_dir


class CloudflarePublisher(Publisher):
    """Publisher backed by a Cloudflare Pages project + tombstone KV
    namespace, driven through the Wrangler seam. See module docstring
    for why publish() maintains a local persistent staging directory
    rather than a throwaway temp dir.
    """

    def __init__(
        self,
        publish_config: PublishConfig,
        wrangler: Optional[Wrangler] = None,
        state_dir: Optional[Union[str, Path]] = None,
        clock: Clock = _now_iso,
    ):
        cf = publish_config.cloudflare
        if cf is None:
            raise PublishError(
                "cloudflare backend selected but [publish.cloudflare] is "
                "not configured"
            )
        self._cf = cf
        self._wrangler = (
            wrangler if wrangler is not None else Wrangler(account_id=cf.account_id)
        )
        self._state_dir = (
            Path(state_dir) if state_dir is not None else _default_state_dir(cf.project)
        )
        self._clock = clock

    def _ensure_wrangler_available(self) -> None:
        if not self._wrangler.check_available():
            raise PublishError(NO_WRANGLER_MESSAGE)

    def _deploy_state_dir(self) -> None:
        """Deploy the state directory's allowlisted content via a fresh
        throwaway build directory, removed again as soon as the deploy
        call returns -- never the raw state dir itself (see
        _build_deploy_dir for the allowlist and the leak it prevents).
        """
        with tempfile.TemporaryDirectory(prefix="jimemo-deploy-") as tmp:
            deploy_dir = _build_deploy_dir(self._state_dir, Path(tmp))
            self._wrangler.pages_deploy(self._cf.project, deploy_dir)

    def publish(self, html_path: Path, title: Optional[str] = None) -> str:
        """Stage html_path as a new hash directory and redeploy the whole
        accumulated set of hashes (see module docstring for why every
        hash, not just the new one -- and why via an allowlisted deploy
        dir, not the raw state directory).

        `title` is accepted for Publisher-interface compatibility but
        unused: the input HTML is already fully rendered and
        self-contained (with its own <title>) by the render pipeline,
        unlike the `command` backend, which may hand raw markdown/text to
        an external CLI that still needs a title to wrap it with.

        Before staging or deploying, self-heals the state directory's
        baseline assets (functions/_middleware.js, _headers, index.html)
        if they're missing -- see _ensure_state_dir_assets. This covers a
        state directory that was never set up via `jimemo publish setup`,
        one where setup was interrupted, or one that lost its Functions
        bundle some other way; without this, publish() would deploy a
        site with no tombstone Function and ``?purge`` would silently
        stop working.

        If the deploy (or the deploy-dir build) fails, the just-staged
        ``<hash>/`` directory is removed from the state directory before
        the error propagates (as a PublishError). The accumulated hashes
        are redeployed in full on every publish() call (see module
        docstring), so leaving a staged hash behind after a failed deploy
        would make the NEXT publish() redeploy a hash that was never
        actually served -- an orphan URL that looks live in
        `list()`/`gc()` bookkeeping but 404s. Only a successful deploy
        leaves the staged hash in persistent state.
        """
        self._ensure_wrangler_available()
        self._state_dir.mkdir(parents=True, exist_ok=True)
        _ensure_state_dir_assets(self._state_dir)
        page_hash, staged_dir = stage_page(Path(html_path), self._state_dir)
        try:
            self._deploy_state_dir()
        except Exception as exc:
            # Best-effort: only remove the hash dir THIS call just staged,
            # never touch other hashes or functions/_middleware.js.
            shutil.rmtree(staged_dir, ignore_errors=True)
            if isinstance(exc, PublishError):
                raise
            raise PublishError(f"cloudflare pages deploy failed: {exc}") from exc
        return f"{self._cf.base_url.rstrip('/')}/{page_hash}/"

    def purge(self, hash_or_url: str) -> None:
        """Tombstone a hash by writing a timestamp to the KV namespace
        the ported middleware reads as `env.TOMBSTONES` -- mirroring
        notes-ito-com's tombstone model directly via KV instead of that
        site's HTTP-POST-to-its-own-`?purge`-endpoint approach (jimemo
        already has direct KV access through the Wrangler seam, so there
        is no need to round-trip through the deployed site itself).
        Purging does not require the hash to be one this machine staged
        locally -- read and purge access are intentionally symmetric and
        machine-independent, same as notes-ito-com.
        """
        self._ensure_wrangler_available()
        page_hash = _extract_hash(hash_or_url)
        self._wrangler.kv_put(self._cf.kv_namespace_id, page_hash, self._clock())

    def list(self) -> List[Dict[str, Any]]:
        """Return one entry per hash this backend knows about, combining
        locally staged hash directories ("live" unless also tombstoned)
        with tombstoned hashes from KV ("purged", whether or not this
        machine staged them). Each entry:
        ``{"hash": ..., "status": "live"|"purged", "tombstoned_at":
        str|None, "staged_locally": bool}``.
        """
        self._ensure_wrangler_available()
        tombstones: Dict[str, str] = {}
        for entry in self._wrangler.kv_list(self._cf.kv_namespace_id):
            name = entry.get("name") if isinstance(entry, dict) else entry
            # KV can hold non-hash keys too (e.g. setup.py's
            # __jimemo_setup_check__ sentinel) -- only real published
            # hashes belong in the published-hash listing.
            if name and _HASH_RE.match(name):
                tombstones[name] = self._wrangler.kv_get(self._cf.kv_namespace_id, name)

        local_hashes = set()
        if self._state_dir.is_dir():
            for child in self._state_dir.iterdir():
                if child.is_dir() and _HASH_RE.match(child.name):
                    local_hashes.add(child.name)

        rows = []
        for h in sorted(local_hashes | set(tombstones)):
            rows.append({
                "hash": h,
                "status": "purged" if h in tombstones else "live",
                "tombstoned_at": tombstones.get(h),
                "staged_locally": h in local_hashes,
            })
        return rows

    def gc(self) -> None:
        """Remove locally staged hash directories that are tombstoned in
        KV, then redeploy the (now smaller) accumulated content -- only if
        something was actually removed, to avoid a needless no-op deploy.

        This differs from the `command` backend's gc(), which just
        dispatches to the configured external CLI's own `gc` subcommand
        and trusts it to know its own storage model end to end. Here,
        jimemo owns the local state directory itself (see module
        docstring), so gc's job is exactly notes-ito-com's `gc --apply`:
        delete tombstoned directories from disk and redeploy to shrink
        the next deploy's size. Unlike notes-ito-com's CLI, there is no
        separate dry-run mode at this layer -- Task 5's `--dry-run` wizard
        covers the "show me what would happen" need for setup; a plain
        `gc()` call here always applies.

        Before that redeploy, self-heals the state directory's baseline
        assets the same way publish() does (see _ensure_state_dir_assets):
        gc() redeploys the whole accumulated content too, so a damaged,
        partially copied, or never-set-up state dir missing
        functions/_middleware.js would otherwise go back out with no
        tombstone Function, silently disabling ?purge for every hash gc
        just redeployed.
        """
        self._ensure_wrangler_available()
        tombstoned_names = set()
        for entry in self._wrangler.kv_list(self._cf.kv_namespace_id):
            name = entry.get("name") if isinstance(entry, dict) else entry
            # Same non-hash-key filter as list() -- a KV key that isn't a
            # hash (e.g. setup.py's sentinel) must never be treated as a
            # tombstoned hash directory to remove.
            if name and _HASH_RE.match(name):
                tombstoned_names.add(name)

        removed_any = False
        if self._state_dir.is_dir():
            for child in list(self._state_dir.iterdir()):
                # Belt-and-suspenders: only ever rmtree a child whose own
                # name is a hash, even if tombstoned_names somehow held a
                # non-hash entry -- a KV key named e.g. "functions" must
                # never delete the Functions bundle.
                if (
                    child.is_dir()
                    and _HASH_RE.match(child.name)
                    and child.name in tombstoned_names
                ):
                    shutil.rmtree(child)
                    removed_any = True

        if removed_any:
            _ensure_state_dir_assets(self._state_dir)
            self._deploy_state_dir()
