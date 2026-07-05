import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import jimemo


def test_version_is_semver_string():
    parts = jimemo.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
