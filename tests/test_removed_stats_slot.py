"""The `stats` slot (a row of stat tiles) was removed from every built-in
template in 0.0.3. Content files that still carry a `stats:` key must fail
with the loader's named unknown-slot error against the REAL manifests, not
a synthetic one, so a template that quietly grows the slot back is caught."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.content import load_content
from jimemo.errors import ContentError

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
FORMERLY_HAD_STATS = ["briefing", "chart-dashboard", "data-dashboard", "ops-board", "research-bible"]


@pytest.mark.parametrize("name", FORMERLY_HAD_STATS)
def test_stats_key_is_rejected_by_real_manifest(tmp_path, name):
    manifest = json.loads((TEMPLATES / name / "manifest.json").read_text())
    assert "stats" not in manifest["slots"]
    assert "stat-tile" not in manifest["components"]
    f = tmp_path / "content.yaml"
    f.write_text(
        'title: "T"\n'
        "stats:\n"
        '  - label: "Reviews migrated"\n'
        '    value: "12 of 20"\n'
    )
    with pytest.raises(ContentError, match=r"unknown slot 'stats' \(not declared in manifest\)"):
        load_content(f, manifest)


def test_no_template_ships_stat_tile():
    for manifest_path in TEMPLATES.glob("*/manifest.json"):
        manifest = json.loads(manifest_path.read_text())
        assert "stats" not in manifest["slots"], manifest_path
        assert "stat-tile" not in manifest["components"], manifest_path
