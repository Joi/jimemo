"""Publish backend interface + registry.

A jimemo "publish" turns an already-rendered, self-contained HTML file
into an unlisted private link (mirroring notes.ito.com's model: a
24-hex-hash path is the access control, symmetric read/purge, tombstone
on purge). Two backends implement `Publisher`:

- "command" (Task 2): shells out to a configured CLI (e.g. notes-publish).
- "cloudflare" (Task 4): a native Wrangler-driven Cloudflare Pages + KV
  backend, for friends without an existing publish setup.

Backend modules are imported lazily, inside get_publisher(), rather than
at this module's top: importing jimemo.publish must never pull in either
backend's implementation (subprocess plumbing for "command", the
Wrangler seam for "cloudflare") until one is actually selected and used.
"""
import abc
from pathlib import Path
from typing import Any, List, Optional

from ..config import Config
from ..errors import PublishError

BACKENDS = ("command", "cloudflare")


class Publisher(abc.ABC):
    """A publish backend: turns a rendered HTML file into an unlisted
    private link, and can purge/list/gc what it has published."""

    @abc.abstractmethod
    def publish(self, html_path: Path, title: Optional[str] = None) -> str:
        """Stage html_path and return the URL it was published to."""

    @abc.abstractmethod
    def purge(self, hash_or_url: str) -> None:
        """Tombstone a previously published hash or URL."""

    @abc.abstractmethod
    def list(self) -> List[Any]:
        """Return published entries (shape is backend-defined)."""

    @abc.abstractmethod
    def gc(self) -> None:
        """Remove tombstoned/orphaned entries."""


def get_publisher(config: Config) -> Publisher:
    """Resolve config.publish.backend to a Publisher instance.

    Raises PublishError if no [publish] section is configured, the
    backend name is unrecognized, or the backend module can't be
    imported (e.g. before Task 2/4 add it).
    """
    if config.publish is None:
        raise PublishError(
            "no [publish] section configured; run `jimemo publish setup` "
            "or add one to ~/.jimemo/config.toml"
        )

    backend = config.publish.backend
    if backend == "command":
        try:
            from .command_backend import CommandPublisher
        except ImportError as e:
            raise PublishError(f'"command" backend is not available: {e}')
        return CommandPublisher(config.publish)

    if backend == "cloudflare":
        try:
            from .cloudflare_backend import CloudflarePublisher
        except ImportError as e:
            raise PublishError(f'"cloudflare" backend is not available: {e}')
        return CloudflarePublisher(config.publish)

    raise PublishError(
        f"unknown publish backend {backend!r} (must be one of: {', '.join(BACKENDS)})"
    )
