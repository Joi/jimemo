import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.discovery import default_search_dirs, find_templates


def make_template(root: Path, name: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "manifest.json").write_text("{}")


def test_finds_templates_sorted(tmp_path):
    make_template(tmp_path, "timeline")
    make_template(tmp_path, "briefing")
    (tmp_path / "not-a-template").mkdir()  # no manifest.json
    found = find_templates([tmp_path])
    assert [name for name, _ in found] == ["briefing", "timeline"]


def test_personal_dir_shadows_nothing_but_merges(tmp_path):
    repo = tmp_path / "repo"
    personal = tmp_path / "personal"
    make_template(repo, "briefing")
    make_template(personal, "my-zine")
    found = find_templates([repo, personal])
    assert [name for name, _ in found] == ["briefing", "my-zine"]


def test_duplicate_name_first_dir_wins(tmp_path):
    repo = tmp_path / "repo"
    personal = tmp_path / "personal"
    make_template(repo, "briefing")
    make_template(personal, "briefing")
    found = find_templates([repo, personal])
    assert len(found) == 1
    assert found[0][1] == repo / "briefing"


def test_missing_dirs_are_ignored(tmp_path):
    assert find_templates([tmp_path / "nope"]) == []


def test_default_search_dirs():
    dirs = default_search_dirs()
    assert dirs[0].name == "templates"
    assert dirs[1] == Path.home() / ".jimemo" / "templates"
