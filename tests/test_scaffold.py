import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo import cli, scaffold
from jimemo.content import load_content
from jimemo.errors import ScaffoldError
from jimemo.manifest import load_manifest
from jimemo.render import render_page


def test_create_template_scaffolds_expected_files(tmp_path):
    template_dir = scaffold.create_template("zine", dest_root=tmp_path)
    assert template_dir == tmp_path / "zine"
    assert (template_dir / "manifest.json").is_file()
    assert (template_dir / "template.html.j2").is_file()
    assert (template_dir / "sample" / "content.md").is_file()


def test_create_template_manifest_is_valid_v1(tmp_path):
    template_dir = scaffold.create_template("zine", dest_root=tmp_path)
    manifest = load_manifest(template_dir)  # raises ManifestError if invalid
    assert manifest["name"] == "zine"
    assert manifest["suitability"]["good_for"] == "TODO"
    assert manifest["suitability"]["keywords"] == []
    assert manifest["slots"]["title"]["type"] == "text"
    assert manifest["slots"]["body"]["type"] == "markdown"
    assert manifest["components"] == ["page-header"]


def test_create_template_labeled_hash_matches_scaffolded_template(tmp_path):
    template_dir = scaffold.create_template("zine", dest_root=tmp_path)
    manifest = load_manifest(template_dir)
    actual = hashlib.sha256(
        (template_dir / "template.html.j2").read_bytes()
    ).hexdigest()
    assert manifest["suitability"]["labeled_hash"] == actual


def test_create_template_uses_page_header_and_extends_page(tmp_path):
    template_dir = scaffold.create_template("zine", dest_root=tmp_path)
    source = (template_dir / "template.html.j2").read_text(encoding="utf-8")
    assert 'extends "page.html.j2"' in source
    assert 'import "macros.html.j2"' in source
    assert "page_header" in source


@pytest.mark.parametrize(
    "name", ["Zine", "-zine", "zine_thing", "", "zine!", "zine thing", "ZINE"]
)
def test_create_template_rejects_invalid_names(tmp_path, name):
    with pytest.raises(ScaffoldError):
        scaffold.create_template(name, dest_root=tmp_path)
    assert not (tmp_path / name).exists() if name else True


def test_create_template_accepts_single_char_and_digits(tmp_path):
    template_dir = scaffold.create_template("a1", dest_root=tmp_path)
    assert template_dir.is_dir()


def test_create_template_refuses_to_overwrite(tmp_path):
    scaffold.create_template("zine", dest_root=tmp_path)
    with pytest.raises(ScaffoldError, match="zine"):
        scaffold.create_template("zine", dest_root=tmp_path)


def test_create_template_default_dest_root_is_jimemo_templates():
    assert scaffold.DEFAULT_DEST_ROOT == Path.home() / ".jimemo" / "templates"


def test_scaffold_renders_out_of_the_box(tmp_path):
    template_dir = scaffold.create_template("zine", dest_root=tmp_path)
    manifest = load_manifest(template_dir)
    sample_files = sorted((template_dir / "sample").glob("*.md"))
    assert sample_files

    content = load_content(sample_files[0], manifest)
    html = render_page(template_dir, content, base_dir=sample_files[0].parent)

    assert html.startswith("<!doctype html>")
    assert "<style>" in html


# --- CLI integration ---

def test_cli_new_template_creates_under_default_dest(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(scaffold, "DEFAULT_DEST_ROOT", tmp_path / "personal")

    rc = cli.main(["new-template", "zine"])
    assert rc == 0
    assert (tmp_path / "personal" / "zine" / "manifest.json").is_file()
    assert "zine" in capsys.readouterr().out


def test_cli_new_template_invalid_name_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(scaffold, "DEFAULT_DEST_ROOT", tmp_path / "personal")

    rc = cli.main(["new-template", "Bad Name"])
    assert rc == 1
    assert "Bad Name" in capsys.readouterr().err


def test_cli_new_template_refuses_overwrite_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(scaffold, "DEFAULT_DEST_ROOT", tmp_path / "personal")

    assert cli.main(["new-template", "zine"]) == 0
    rc = cli.main(["new-template", "zine"])
    assert rc == 1
    assert "zine" in capsys.readouterr().err


def test_cli_new_template_then_render_its_sample_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(scaffold, "DEFAULT_DEST_ROOT", tmp_path / "personal")
    monkeypatch.setattr(
        cli, "default_search_dirs", lambda: [tmp_path / "personal"]
    )

    assert cli.main(["new-template", "zine"]) == 0
    sample = tmp_path / "personal" / "zine" / "sample" / "content.md"
    assert sample.is_file()

    out_path = tmp_path / "z.html"
    rc = cli.main(["render", "zine", str(sample), "-o", str(out_path)])
    assert rc == 0
    assert out_path.is_file()


# ---------------------------------------------------------------------------
# scaffold (content skeletons)
# ---------------------------------------------------------------------------

def test_scaffold_briefing_is_md_with_frontmatter():
    from jimemo.discovery import default_search_dirs, find_templates
    from jimemo.manifest import load_manifest
    from jimemo.scaffold import scaffold_content

    templates = dict(find_templates(default_search_dirs()))
    text, kind = scaffold_content(load_manifest(templates["briefing"]), templates["briefing"])
    assert kind == "md"
    assert text.startswith("---\n")
    assert 'title: ""' in text
    assert "required" in text  # required slots are annotated
    assert text.rstrip().endswith("-- required)") or "body markdown goes here" in text


def test_scaffold_bodyless_template_is_yaml():
    from jimemo.discovery import default_search_dirs, find_templates
    from jimemo.manifest import load_manifest
    from jimemo.scaffold import scaffold_content

    templates = dict(find_templates(default_search_dirs()))
    text, kind = scaffold_content(load_manifest(templates["ops-board"]), templates["ops-board"])
    assert kind == "yaml"
    assert not text.startswith("---")
    assert "sections:" in text


def test_scaffold_output_parses_for_every_seed_template(tmp_path):
    # The whole point: nobody should reverse-engineer samples again.
    # Every skeleton must round-trip through load_content unchanged --
    # correct slot names, required slots present, data shapes accepted.
    from jimemo._paths import REPO_ROOT
    from jimemo.content import load_content
    from jimemo.discovery import find_templates
    from jimemo.manifest import load_manifest
    from jimemo.scaffold import scaffold_content

    for name, template_dir in find_templates([REPO_ROOT / "templates"]):
        manifest = load_manifest(template_dir)
        text, kind = scaffold_content(manifest, template_dir)
        out = tmp_path / f"{name}-skeleton.{kind}"
        out.write_text(text)
        content = load_content(out, manifest)  # must not raise
        for slot_name, spec in manifest["slots"].items():
            if spec.get("required"):
                assert slot_name in content, f"{name}: required {slot_name!r} missing"
