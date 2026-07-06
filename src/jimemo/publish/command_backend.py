"""The ``command`` publish backend: shells out to a configured CLI
(``config.publish.command``, e.g. ``notes-publish``) instead of
reimplementing hosting. This is the backend Joi uses day to day -- it
keeps notes-publish/notes.ito.com authoritative, and jimemo just wraps
its ``publish``/``purge``/``list``/``gc`` subcommands.

Hashing and file staging (staging.py) are NOT used here: the configured
command owns hash generation, staging, and deploy end-to-end, which is
the whole point of a passthrough. staging.py is for the ``cloudflare``
backend (Task 4) instead.

Every subprocess call goes through the module-level ``_run`` (or an
injected replacement) so tests never invoke a real command.
"""
import subprocess
from pathlib import Path
from typing import Any, List, Optional

from ..config import PublishConfig
from ..errors import PublishError
from . import Publisher


def _run(argv: List[str]):
    """Default runner: a real subprocess, argv only (never shell=True)."""
    return subprocess.run(argv, capture_output=True, text=True)


def _last_url_line(stdout: str, command: str) -> str:
    """Parse the notes-publish contract: the published URL is the last
    non-empty stdout line, and it looks like a URL. Progress/diagnostic
    output (staging, deploy progress, etc.) goes to stderr, never
    stdout -- so this only has to trust the final line."""
    non_empty = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not non_empty:
        raise PublishError(f"{command}: expected a URL on stdout, got no output")
    url = non_empty[-1]
    if not url.startswith("https://"):
        raise PublishError(f"{command}: expected a URL on stdout, got: {url!r}")
    return url


class CommandPublisher(Publisher):
    """Publisher that delegates every operation to a configured CLI."""

    def __init__(self, publish_config: PublishConfig, runner=_run):
        self._command = publish_config.command
        self._run = runner

    def _invoke(self, args: List[str], action: str):
        argv = [self._command] + args
        try:
            result = self._run(argv)
        except FileNotFoundError:
            raise PublishError(
                f"publish command not found: {argv[0]} (check [publish] "
                "command in your config)"
            )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise PublishError(
                f"{self._command} {action} failed (exit {result.returncode}): {stderr}"
            )
        return result

    def publish(self, html_path: Path, title: Optional[str] = None) -> str:
        args = [str(html_path)]
        if title:
            args += ["--title", title]
        result = self._invoke(args, "publish")
        return _last_url_line(result.stdout, self._command)

    def purge(self, hash_or_url: str) -> None:
        self._invoke(["purge", hash_or_url], "purge")

    def list(self) -> List[Any]:
        result = self._invoke(["list"], "list")
        return result.stdout.splitlines()

    def gc(self) -> None:
        self._invoke(["gc"], "gc")
