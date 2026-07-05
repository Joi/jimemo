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
and redeploys that whole directory on every publish -- the same shape as
notes-ito-com's git-committed `public/` tree, just kept under `~/.jimemo/`
instead of a git repo, since jimemo has no repo of its own to commit into.
"""
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union
from urllib.parse import urlparse

from ..config import PublishConfig
from ..errors import PublishError
from . import Publisher
from .staging import stage_page
from .wrangler import NO_WRANGLER_MESSAGE, Wrangler

_HASH_RE = re.compile(r"^[a-f0-9]{24}$")

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
        self._wrangler = wrangler if wrangler is not None else Wrangler()
        self._state_dir = (
            Path(state_dir) if state_dir is not None else _default_state_dir(cf.project)
        )
        self._clock = clock

    def _ensure_wrangler_available(self) -> None:
        if not self._wrangler.check_available():
            raise PublishError(NO_WRANGLER_MESSAGE)

    def publish(self, html_path: Path, title: Optional[str] = None) -> str:
        """Stage html_path as a new hash directory and redeploy the whole
        accumulated state directory (see module docstring for why the
        whole directory, not just the new hash).

        `title` is accepted for Publisher-interface compatibility but
        unused: the input HTML is already fully rendered and
        self-contained (with its own <title>) by the render pipeline,
        unlike the `command` backend, which may hand raw markdown/text to
        an external CLI that still needs a title to wrap it with.
        """
        self._ensure_wrangler_available()
        self._state_dir.mkdir(parents=True, exist_ok=True)
        page_hash, _staged_dir = stage_page(Path(html_path), self._state_dir)
        self._wrangler.pages_deploy(self._cf.project, self._state_dir)
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
        KV, then redeploy the (now smaller) state directory -- only if
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
            self._wrangler.pages_deploy(self._cf.project, self._state_dir)
