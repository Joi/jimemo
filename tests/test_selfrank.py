"""Locks the suggest scorer (suggest.py) together with the seed templates'
hand-written samples: each seed template's own sample content must rank
that template #1 (strict unique max) among all seed templates.

Uses the REPO templates/ dir explicitly (via jimemo._paths.REPO_ROOT), not
jimemo.discovery.default_search_dirs() -- this must stay independent of
whatever templates happen to exist under ~/.jimemo on the machine running
the suite.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo._paths import REPO_ROOT
from jimemo.discovery import find_templates
from jimemo.suggest import score_templates

TEMPLATES_ROOT = REPO_ROOT / "templates"
CONTENT_SUFFIXES = (".md", ".json", ".yaml", ".yml")

REPO_TEMPLATES = find_templates([TEMPLATES_ROOT])
SEED_NAMES = [name for name, _ in REPO_TEMPLATES]


def test_seed_templates_present():
    # Guards the parametrization below against silently collecting fewer
    # cases than intended (e.g. a template dir missing manifest.json).
    assert sorted(SEED_NAMES) == [
        "briefing", "chart-dashboard", "data-dashboard", "genealogy",
        "ops-board", "photo-catalog", "timeline",
    ]


@pytest.mark.parametrize("name", SEED_NAMES)
def test_seed_template_sample_self_ranks_first(name):
    template_dir = dict(REPO_TEMPLATES)[name]
    sample_dir = template_dir / "sample"
    content_files = sorted(
        p for p in sample_dir.iterdir() if p.suffix.lower() in CONTENT_SUFFIXES
    )
    assert content_files, f"{name}: sample/ has no content file"
    content_path = content_files[0]

    ranked, warnings = score_templates(content_path, REPO_TEMPLATES)
    assert warnings == []

    scores = {r["name"]: r["score"] for r in ranked}
    top_score = max(scores.values())
    top_names = sorted(n for n, s in scores.items() if s == top_score)

    assert top_names == [name], (
        f"{name}'s own sample must rank it #1 as a strict unique max; "
        f"tied/ahead at top score {top_score}: {top_names}; all scores={scores}"
    )
