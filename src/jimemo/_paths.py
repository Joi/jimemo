"""Single source of truth for repo-root-relative paths."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
