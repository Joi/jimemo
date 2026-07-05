"""Template discovery. A template is a directory containing manifest.json.
Repo templates/ is searched first, then ~/.jimemo/templates/; on a name
collision the earlier directory wins."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def default_search_dirs() -> list:
    return [REPO_ROOT / "templates", Path.home() / ".jimemo" / "templates"]


def find_templates(search_dirs) -> list:
    seen = {}
    for root in search_dirs:
        root = Path(root)
        if not root.is_dir():
            continue
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            if (d / "manifest.json").is_file() and d.name not in seen:
                seen[d.name] = d
    return sorted(seen.items())
