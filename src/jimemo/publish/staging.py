"""Hash-based staging: copy a rendered HTML file into a ``<hash>/index.html``
directory, mirroring notes-ito-com's ``public/<hash>/`` layout.

The 24-hex-char hash IS the access control (``secrets.token_hex(12)`` = 96
bits of entropy) -- unguessable, and symmetric between read and purge.

This module is for the ``cloudflare`` backend (Task 4), which hosts pages
itself and needs to lay out its own hash directories before deploying.
The ``command`` backend (command_backend.py) does NOT use this: it
delegates hashing/staging/deploy to the configured external command
end-to-end, which is the whole point of a passthrough.
"""
import shutil
from pathlib import Path
from secrets import token_hex
from typing import Callable, Tuple

from ..errors import PublishError

TokenSource = Callable[[int], str]


def stage_page(
    html_path: Path,
    work_dir: Path,
    token_source: TokenSource = token_hex,
) -> Tuple[str, Path]:
    """Stage ``html_path`` as ``<work_dir>/<hash>/index.html``.

    Returns ``(hash, staged_dir)``. ``token_source`` defaults to
    ``secrets.token_hex``; tests inject a deterministic stand-in (e.g.
    ``lambda n: "ab" * n``) to get a reproducible hash. It is called the
    same way ``token_hex`` is: with the byte count, returning ``2 * n``
    hex characters -- so ``token_source(12)`` yields the 24-hex-char hash.

    Raises PublishError if html_path does not exist.
    """
    html_path = Path(html_path)
    if not html_path.is_file():
        raise PublishError(f"cannot stage {html_path}: file does not exist")

    page_hash = token_source(12)
    staged_dir = Path(work_dir) / page_hash
    staged_dir.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(html_path, staged_dir / "index.html")
    return page_hash, staged_dir
