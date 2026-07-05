import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo import cli
from jimemo.errors import ContentError, ManifestError
from jimemo.suggest import is_stale_labels, score_templates

# Not a real toolkit template: score_templates never renders, only hashes
# this file and reads the manifest, so any bytes will do for scorer tests.
FAKE_TEMPLATE_SOURCE = "<html>{{ title }}</html>\n"

# A real, renderable template (extends the toolkit) for CLI integration
# tests that actually call `render auto`.
RENDERABLE_TEMPLATE_SOURCE = (
    '{% extends "page.html.j2" %}\n'
    '{% import "macros.html.j2" as ui %}\n'
    "{% block content %}{{ ui.page_header(title) }}"
    '<div class="jm-prose">{{ body }}</div>{% endblock %}\n'
)

# A valid 1x1 transparent PNG, small enough to inline as a literal.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAA"
    "AAYAAjCB0C8AAAAASUVORK5CYII="
)


def make_template(
    templates_root: Path,
    name: str,
    *,
    keywords=None,
    content_kinds=None,
    stale: bool = False,
    source: str = FAKE_TEMPLATE_SOURCE,
) -> Path:
    template_dir = templates_root / name
    template_dir.mkdir(parents=True)
    (template_dir / "template.html.j2").write_text(source, encoding="utf-8")

    actual_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    manifest = {
        "name": name,
        "version": 1,
        "title": name.replace("-", " ").title(),
        "slots": {
            "title": {"type": "text", "required": True},
            "body": {"type": "markdown", "required": True},
        },
        "components": [],
        "charts": [],
        "suitability": {
            "keywords": keywords or [],
            "content_kinds": content_kinds or [],
            "good_for": "testing",
            "labeled_hash": ("0" * 64) if stale else actual_hash,
        },
    }
    (template_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return template_dir


def write_content(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


PHOTO_CONTENT = """\
---
title: Garden Catalog
---
![Rose](rose.png)
![Tulip](tulip.png)
![Daisy](daisy.png)
![Lily](lily.png)
![Iris](iris.png)
A short caption for each plant in the garden.
"""

CHRONOLOGICAL_CONTENT = """\
---
title: Project Timeline
---
On 2026-01-01 the project kicked off. On 2026-02-15 the first milestone
shipped. Finally, on 2026-03-20 the project concluded.
"""

TABULAR_CONTENT_JSON = json.dumps({
    "title": "Inventory",
    "rows": [
        {"item": "Widget", "count": 3},
        {"item": "Gadget", "count": 7},
        {"item": "Gizmo", "count": 1},
    ],
})

HIERARCHICAL_CONTENT_JSON = json.dumps({
    "title": "Family Tree",
    "root": {
        "name": "Grandparent",
        "children": [
            {"name": "Parent", "children": [{"name": "Child"}]},
        ],
    },
})

NARRATIVE_CONTENT = """\
---
title: A Quiet Afternoon
---
""" + " ".join(["word"] * 130) + "\n"


# --- score_templates: unit tests ---

def test_score_templates_is_deterministic(tmp_path):
    templates_root = tmp_path / "templates"
    make_template(templates_root, "photo-tpl", content_kinds=["photo-heavy"])
    make_template(templates_root, "narrative-tpl", content_kinds=["narrative"])
    templates = [
        ("photo-tpl", templates_root / "photo-tpl"),
        ("narrative-tpl", templates_root / "narrative-tpl"),
    ]
    content = write_content(tmp_path, "content.md", PHOTO_CONTENT)

    first = score_templates(content, templates)
    second = score_templates(content, templates)
    assert first == second


def test_photo_heavy_content_ranks_photo_template_above_narrative(tmp_path):
    templates_root = tmp_path / "templates"
    make_template(templates_root, "photo-tpl", keywords=["garden"], content_kinds=["photo-heavy"])
    make_template(templates_root, "narrative-tpl", keywords=["memo"], content_kinds=["narrative"])
    templates = [
        ("photo-tpl", templates_root / "photo-tpl"),
        ("narrative-tpl", templates_root / "narrative-tpl"),
    ]
    content = write_content(tmp_path, "content.md", PHOTO_CONTENT)

    ranked = score_templates(content, templates)
    by_name = {r["name"]: r for r in ranked}

    assert by_name["photo-tpl"]["score"] > by_name["narrative-tpl"]["score"]
    assert ranked[0]["name"] == "photo-tpl"
    assert any("photo-heavy" in reason for reason in by_name["photo-tpl"]["reasons"])


def test_chronological_content_scores_chronological_template_higher(tmp_path):
    templates_root = tmp_path / "templates"
    make_template(templates_root, "timeline-tpl", content_kinds=["chronological"])
    make_template(templates_root, "table-tpl", content_kinds=["tabular-data"])
    templates = [
        ("timeline-tpl", templates_root / "timeline-tpl"),
        ("table-tpl", templates_root / "table-tpl"),
    ]
    content = write_content(tmp_path, "content.md", CHRONOLOGICAL_CONTENT)

    ranked = score_templates(content, templates)
    by_name = {r["name"]: r for r in ranked}

    assert by_name["timeline-tpl"]["score"] > by_name["table-tpl"]["score"]
    assert any("chronological" in reason for reason in by_name["timeline-tpl"]["reasons"])


def test_tabular_content_scores_tabular_template_higher(tmp_path):
    templates_root = tmp_path / "templates"
    make_template(templates_root, "table-tpl", content_kinds=["tabular-data"])
    make_template(templates_root, "hierarchy-tpl", content_kinds=["hierarchical"])
    templates = [
        ("table-tpl", templates_root / "table-tpl"),
        ("hierarchy-tpl", templates_root / "hierarchy-tpl"),
    ]
    content = write_content(tmp_path, "content.json", TABULAR_CONTENT_JSON)

    ranked = score_templates(content, templates)
    by_name = {r["name"]: r for r in ranked}

    # A flat table of scalar-valued records should NOT also trip the
    # hierarchical threshold (dict-of-slots -> list -> record-dict is
    # always 3 raw levels, but that's the definition of tabular, not
    # meaningfully "hierarchical").
    assert by_name["table-tpl"]["score"] > by_name["hierarchy-tpl"]["score"]
    assert by_name["hierarchy-tpl"]["score"] == 0
    assert any("tabular-data" in reason for reason in by_name["table-tpl"]["reasons"])


def test_hierarchical_content_scores_hierarchical_template_higher(tmp_path):
    templates_root = tmp_path / "templates"
    make_template(templates_root, "hierarchy-tpl", content_kinds=["hierarchical"])
    make_template(templates_root, "table-tpl", content_kinds=["tabular-data"])
    templates = [
        ("hierarchy-tpl", templates_root / "hierarchy-tpl"),
        ("table-tpl", templates_root / "table-tpl"),
    ]
    content = write_content(tmp_path, "content.json", HIERARCHICAL_CONTENT_JSON)

    ranked = score_templates(content, templates)
    by_name = {r["name"]: r for r in ranked}

    assert by_name["hierarchy-tpl"]["score"] > by_name["table-tpl"]["score"]
    assert any("hierarchical" in reason for reason in by_name["hierarchy-tpl"]["reasons"])


def test_narrative_baseline_only_fires_without_structural_signal(tmp_path):
    templates_root = tmp_path / "templates"
    make_template(templates_root, "narrative-tpl", content_kinds=["narrative"])
    make_template(templates_root, "photo-tpl", content_kinds=["photo-heavy"])
    templates = [
        ("narrative-tpl", templates_root / "narrative-tpl"),
        ("photo-tpl", templates_root / "photo-tpl"),
    ]
    content = write_content(tmp_path, "content.md", NARRATIVE_CONTENT)

    ranked = score_templates(content, templates)
    by_name = {r["name"]: r for r in ranked}

    assert by_name["narrative-tpl"]["score"] > 0
    assert by_name["photo-tpl"]["score"] == 0
    assert ranked[0]["name"] == "narrative-tpl"


def test_keyword_match_adds_score_and_reason(tmp_path):
    templates_root = tmp_path / "templates"
    make_template(templates_root, "garden-tpl", keywords=["garden", "catalog"])
    templates = [("garden-tpl", templates_root / "garden-tpl")]
    content = write_content(tmp_path, "content.md", PHOTO_CONTENT)

    ranked = score_templates(content, templates)
    assert ranked[0]["score"] > 0
    assert any("garden" in reason for reason in ranked[0]["reasons"])


def test_stale_labels_flip_flag_and_apply_penalty(tmp_path):
    templates_root = tmp_path / "templates"
    make_template(templates_root, "fresh-tpl", keywords=["garden"], content_kinds=["photo-heavy"])
    make_template(
        templates_root, "stale-tpl", keywords=["garden"], content_kinds=["photo-heavy"], stale=True
    )
    templates = [
        ("fresh-tpl", templates_root / "fresh-tpl"),
        ("stale-tpl", templates_root / "stale-tpl"),
    ]
    content = write_content(tmp_path, "content.md", PHOTO_CONTENT)

    ranked = score_templates(content, templates)
    by_name = {r["name"]: r for r in ranked}

    assert by_name["fresh-tpl"]["stale_labels"] is False
    assert by_name["stale-tpl"]["stale_labels"] is True
    assert by_name["fresh-tpl"]["score"] > 0
    assert by_name["stale-tpl"]["score"] == pytest.approx(by_name["fresh-tpl"]["score"] * 0.8)
    assert any("stale" in reason for reason in by_name["stale-tpl"]["reasons"])


def test_is_stale_labels_true_for_mismatched_hash(tmp_path):
    template_dir = make_template(tmp_path / "templates", "t", stale=True)
    manifest = json.loads((template_dir / "manifest.json").read_text())
    assert is_stale_labels(manifest, template_dir) is True


def test_is_stale_labels_false_for_matching_hash(tmp_path):
    template_dir = make_template(tmp_path / "templates", "t", stale=False)
    manifest = json.loads((template_dir / "manifest.json").read_text())
    assert is_stale_labels(manifest, template_dir) is False


def test_is_stale_labels_false_when_no_labeled_hash_recorded(tmp_path):
    template_dir = make_template(tmp_path / "templates", "t")
    manifest = json.loads((template_dir / "manifest.json").read_text())
    manifest["suitability"]["labeled_hash"] = ""
    assert is_stale_labels(manifest, template_dir) is False


def test_tie_break_is_alphabetical(tmp_path):
    templates_root = tmp_path / "templates"
    make_template(templates_root, "zebra-tpl")
    make_template(templates_root, "alpha-tpl")
    templates = [
        ("zebra-tpl", templates_root / "zebra-tpl"),
        ("alpha-tpl", templates_root / "alpha-tpl"),
    ]
    content = write_content(tmp_path, "content.md", "---\ntitle: X\n---\nbody\n")

    ranked = score_templates(content, templates)
    assert [r["name"] for r in ranked] == ["alpha-tpl", "zebra-tpl"]
    assert ranked[0]["score"] == ranked[1]["score"] == 0


def test_score_templates_missing_content_file_raises_content_error(tmp_path):
    templates_root = tmp_path / "templates"
    make_template(templates_root, "t")
    with pytest.raises(ContentError):
        score_templates(tmp_path / "nope.md", [("t", templates_root / "t")])


def test_score_templates_unsupported_content_type_raises(tmp_path):
    templates_root = tmp_path / "templates"
    make_template(templates_root, "t")
    content = write_content(tmp_path, "content.txt", "hello")
    with pytest.raises(ContentError):
        score_templates(content, [("t", templates_root / "t")])


def test_score_templates_bad_manifest_raises_manifest_error(tmp_path):
    templates_root = tmp_path / "templates"
    template_dir = templates_root / "broken"
    template_dir.mkdir(parents=True)
    (template_dir / "template.html.j2").write_text(FAKE_TEMPLATE_SOURCE)
    (template_dir / "manifest.json").write_text("{}")
    content = write_content(tmp_path, "content.md", "---\ntitle: X\n---\nbody\n")

    with pytest.raises(ManifestError):
        score_templates(content, [("broken", template_dir)])


# --- CLI integration ---

def test_cli_suggest_json_shape(tmp_path, monkeypatch, capsys):
    templates_root = tmp_path / "templates"
    make_template(templates_root, "photo-tpl", content_kinds=["photo-heavy"])
    make_template(templates_root, "narrative-tpl", content_kinds=["narrative"])
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [templates_root])

    content = write_content(tmp_path, "content.md", PHOTO_CONTENT)
    rc = cli.main(["suggest", str(content), "--json"])
    assert rc == 0

    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list)
    assert {"name", "score", "reasons", "stale_labels"} <= data[0].keys()
    assert data[0]["name"] == "photo-tpl"
    scores = [entry["score"] for entry in data]
    assert scores == sorted(scores, reverse=True)


def test_cli_suggest_human_shows_top_3(tmp_path, monkeypatch, capsys):
    templates_root = tmp_path / "templates"
    for i in range(5):
        make_template(templates_root, f"tpl-{i}")
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [templates_root])

    content = write_content(tmp_path, "content.md", "---\ntitle: X\n---\nbody\n")
    rc = cli.main(["suggest", str(content)])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.count("tpl-") == 3


def test_cli_suggest_content_not_found(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [tmp_path / "templates"])
    rc = cli.main(["suggest", str(tmp_path / "nope.md")])
    assert rc == 1
    assert "not found" in capsys.readouterr().err


def test_cli_render_auto_selects_and_renders(tmp_path, monkeypatch, capsys):
    templates_root = tmp_path / "templates"
    make_template(
        templates_root, "photo-tpl", content_kinds=["photo-heavy"], source=RENDERABLE_TEMPLATE_SOURCE
    )
    make_template(
        templates_root, "narrative-tpl", content_kinds=["narrative"], source=RENDERABLE_TEMPLATE_SOURCE
    )
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [templates_root])

    content = write_content(tmp_path, "content.md", PHOTO_CONTENT)
    for name in ("rose.png", "tulip.png", "daisy.png", "lily.png", "iris.png"):
        (tmp_path / name).write_bytes(TINY_PNG)
    out_path = tmp_path / "out.html"
    rc = cli.main(["render", "auto", str(content), "-o", str(out_path)])

    assert rc == 0
    assert out_path.is_file()
    html = out_path.read_text(encoding="utf-8")
    assert "Garden Catalog" in html
    err = capsys.readouterr().err
    assert "auto-selected photo-tpl" in err


def test_cli_doctor_reports_stale_labels(tmp_path, monkeypatch, capsys):
    templates_root = tmp_path / "templates"
    make_template(templates_root, "stale-tpl", stale=True)
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [templates_root])

    rc = cli.main(["doctor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "stale-tpl" in out


def test_cli_doctor_no_stale_labels_reports_ok(tmp_path, monkeypatch, capsys):
    templates_root = tmp_path / "templates"
    make_template(templates_root, "fresh-tpl", stale=False)
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [templates_root])

    rc = cli.main(["doctor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "WARNING" not in out
    assert "suitability labels fresh" in out
