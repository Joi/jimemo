"""Template discovery. A template is a directory containing manifest.json.
Repo templates/ is searched first, then ~/.jimemo/templates/; on a name
collision the earlier directory wins."""
import sys
from pathlib import Path
from typing import Iterable

from ._paths import REPO_ROOT


def default_search_dirs() -> list[Path]:
    return [REPO_ROOT / "templates", Path.home() / ".jimemo" / "templates"]


def find_templates(search_dirs: Iterable[Path]) -> list[tuple[str, Path]]:
    seen: dict[str, Path] = {}
    for root in search_dirs:
        root = Path(root)
        if not root.is_dir():
            continue
        try:
            entries = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError as e:
            print(f"warning: cannot read template directory {root}: {e}",
                  file=sys.stderr)
            continue
        for d in entries:
            if (d / "manifest.json").is_file() and d.name not in seen:
                seen[d.name] = d
    return sorted(seen.items())
