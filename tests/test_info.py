import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo import cli

TEMPLATE_SOURCE = (
    '{% extends "page.html.j2" %}\n'
    '{% import "macros.html.j2" as ui %}\n'
    "{% block content %}{{ ui.page_header(title) }}"
    '<div class="jm-prose">{{ body }}</div>{% endblock %}\n'
)


def make_template_dir(tmp_path: Path, name: str = "test-tpl", *, stale: bool = False,
                       with_sample: bool = True) -> Path:
    """Builds tmp_path/templates/<name>/ with a real template.html.j2 (so it
    renders through the actual toolkit) and returns the templates/ root
    (suitable for default_search_dirs monkeypatching)."""
    templates_root = tmp_path / "templates"
    template_dir = templates_root / name
    template_dir.mkdir(parents=True)
    (template_dir / "template.html.j2").write_text(TEMPLATE_SOURCE, encoding="utf-8")

    actual_hash = hashlib.sha256(TEMPLATE_SOURCE.encode("utf-8")).hexdigest()
    manifest = {
        "name": name,
        "version": 1,
        "title": "Test Template",
        "description": "A template for testing info output.",
        "slots": {
            "title": {"type": "text", "required": True},
            "body": {"type": "markdown", "required": True},
        },
        "components": ["page-header"],
        "charts": [],
        "suitability": {
            "keywords": ["test", "demo"],
            "content_kinds": ["narrative"],
            "good_for": "testing info output",
            "labeled_hash": ("0" * 64) if stale else actual_hash,
        },
    }
    (template_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    if with_sample:
        sample_dir = template_dir / "sample"
        sample_dir.mkdir()
        (sample_dir / "content.md").write_text(
            "---\ntitle: X\n---\nBody\n", encoding="utf-8"
        )

    return templates_root


def test_info_json_shape(tmp_path, monkeypatch, capsys):
    templates_root = make_template_dir(tmp_path)
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [templates_root])

    rc = cli.main(["info", "test-tpl", "--json"])
    assert rc == 0

    data = json.loads(capsys.readouterr().out)
    assert data["name"] == "test-tpl"
    assert data["title"] == "Test Template"
    assert data["template_dir"] == str(templates_root / "test-tpl")
    assert data["sample_files"] == ["sample/content.md"]
    assert data["suitability"]["good_for"] == "testing info output"
    assert data["components"] == ["page-header"]


def test_info_json_is_clean_json(tmp_path, monkeypatch, capsys):
    templates_root = make_template_dir(tmp_path)
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [templates_root])

    rc = cli.main(["info", "test-tpl", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    json.loads(out)  # must not raise


def test_info_not_found_exits_1_and_lists_available(tmp_path, monkeypatch, capsys):
    templates_root = make_template_dir(tmp_path)
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [templates_root])

    rc = cli.main(["info", "nope"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "nope" in err
    assert "test-tpl" in err


def test_info_not_found_no_templates_installed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [tmp_path / "templates"])

    rc = cli.main(["info", "nope"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "nope" in err


def test_info_human_view_shows_title_slots_components_sample(tmp_path, monkeypatch, capsys):
    templates_root = make_template_dir(tmp_path)
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [templates_root])

    rc = cli.main(["info", "test-tpl"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Test Template" in out
    assert "title" in out
    assert "required" in out
    assert "page-header" in out
    assert "sample/content.md" in out
    assert "testing info output" in out


def test_info_reports_stale_labels(tmp_path, monkeypatch, capsys):
    templates_root = make_template_dir(tmp_path, stale=True)
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [templates_root])

    rc = cli.main(["info", "test-tpl"])
    assert rc == 0
    assert "stale" in capsys.readouterr().out.lower()


def test_info_fresh_labels_not_reported_stale(tmp_path, monkeypatch, capsys):
    templates_root = make_template_dir(tmp_path, stale=False)
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [templates_root])

    rc = cli.main(["info", "test-tpl"])
    out = capsys.readouterr().out
    assert "fresh" in out.lower()
    assert "stale" not in out.lower()


def test_info_no_sample_dir_reports_empty(tmp_path, monkeypatch, capsys):
    templates_root = make_template_dir(tmp_path, with_sample=False)
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [templates_root])

    rc = cli.main(["info", "test-tpl", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["sample_files"] == []
