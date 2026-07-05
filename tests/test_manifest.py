import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.errors import ManifestError
from jimemo.manifest import load_manifest


def write_manifest(tmp_path: Path, data: dict) -> Path:
    template_dir = tmp_path / "briefing"
    template_dir.mkdir(exist_ok=True)
    (template_dir / "manifest.json").write_text(json.dumps(data))
    return template_dir


VALID = {
    "name": "briefing",
    "version": 1,
    "title": "Briefing / memo",
    "description": "one line",
    "slots": {
        "title": {"type": "text", "required": True},
        "date": {"type": "text"},
        "body": {"type": "markdown", "required": True},
        "sections": {"type": "data", "items": {"heading": "text", "body": "markdown"}},
    },
    "components": ["stat-tile", "card-grid"],
    "charts": [],
    "suitability": {
        "keywords": ["briefing", "memo", "report"],
        "content_kinds": ["narrative"],
        "good_for": "one line",
        "labeled_hash": "deadbeef",
    },
}


def test_loads_valid_manifest(tmp_path):
    template_dir = write_manifest(tmp_path, VALID)
    manifest = load_manifest(template_dir)
    assert manifest["name"] == "briefing"
    assert manifest["slots"]["title"]["required"] is True
    assert manifest["components"] == ["stat-tile", "card-grid"]


def test_defaults_filled_when_absent(tmp_path):
    minimal = {
        "name": "zine",
        "version": 1,
        "title": "Zine",
        "slots": {"body": {"type": "markdown", "required": True}},
    }
    template_dir = write_manifest(tmp_path, minimal)
    manifest = load_manifest(template_dir)
    assert manifest["components"] == []
    assert manifest["charts"] == []
    assert manifest["suitability"] == {}
    assert manifest["description"] == ""


def test_missing_manifest_file(tmp_path):
    template_dir = tmp_path / "nope"
    template_dir.mkdir()
    with pytest.raises(ManifestError, match="manifest not found"):
        load_manifest(template_dir)


def test_invalid_json(tmp_path):
    template_dir = tmp_path / "broken"
    template_dir.mkdir()
    (template_dir / "manifest.json").write_text("{not json")
    with pytest.raises(ManifestError, match="not valid JSON"):
        load_manifest(template_dir)


@pytest.mark.parametrize("field", ["name", "version", "title", "slots"])
def test_missing_required_field_names_it(tmp_path, field):
    data = dict(VALID)
    del data[field]
    template_dir = write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match=field):
        load_manifest(template_dir)


def test_wrong_version_named(tmp_path):
    data = dict(VALID)
    data["version"] = 2
    template_dir = write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="version"):
        load_manifest(template_dir)


def test_empty_slots_rejected(tmp_path):
    data = dict(VALID)
    data["slots"] = {}
    template_dir = write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="slots"):
        load_manifest(template_dir)


def test_invalid_slot_type_named(tmp_path):
    data = dict(VALID)
    data["slots"] = dict(VALID["slots"])
    data["slots"]["weird"] = {"type": "video"}
    template_dir = write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="weird"):
        load_manifest(template_dir)


def test_invalid_data_item_type_named(tmp_path):
    data = dict(VALID)
    data["slots"] = dict(VALID["slots"])
    data["slots"]["sections"] = {
        "type": "data",
        "items": {"heading": "text", "nested": "data"},
    }
    template_dir = write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="nested"):
        load_manifest(template_dir)


def test_invalid_content_kind_named(tmp_path):
    data = dict(VALID)
    data["suitability"] = dict(VALID["suitability"])
    data["suitability"]["content_kinds"] = ["space-opera"]
    template_dir = write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="space-opera"):
        load_manifest(template_dir)


def test_manifest_must_be_object(tmp_path):
    template_dir = tmp_path / "listy"
    template_dir.mkdir()
    (template_dir / "manifest.json").write_text("[1, 2, 3]")
    with pytest.raises(ManifestError, match="must be a JSON object"):
        load_manifest(template_dir)
