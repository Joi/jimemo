import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.content import load_content
from jimemo.errors import ContentError

MANIFEST = {
    "slots": {
        "title": {"type": "text", "required": True},
        "date": {"type": "text"},
        "body": {"type": "markdown", "required": True},
        "sections": {"type": "data", "items": {"heading": "text", "body": "markdown"}},
        "tags": {"type": "data"},
    }
}


def test_md_frontmatter_and_body(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text(
        "---\n"
        "title: Weekly update\n"
        "date: 2026-07-05\n"
        "---\n"
        "# Heading\n"
        "\n"
        "Some **prose**.\n"
    )
    content = load_content(f, MANIFEST)
    assert content["title"] == "Weekly update"
    assert content["date"] == "2026-07-05"
    assert "<h1>Heading</h1>" in content["body"]
    assert "<strong>prose</strong>" in content["body"]


def test_markdown_table_extension(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text(
        "---\ntitle: T\n---\n"
        "| A | B |\n"
        "|---|---|\n"
        "| 1 | 2 |\n"
    )
    content = load_content(f, MANIFEST)
    assert "<table>" in content["body"]


def test_markdown_fenced_code_extension(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text("---\ntitle: T\n---\n```\ncode here\n```\n")
    content = load_content(f, MANIFEST)
    assert "<pre>" in content["body"]
    assert "<code>" in content["body"]


def test_yaml_content_file(tmp_path):
    f = tmp_path / "brief.yaml"
    f.write_text("title: From YAML\nbody: 'plain **text**'\n")
    content = load_content(f, MANIFEST)
    assert content["title"] == "From YAML"
    assert "<strong>text</strong>" in content["body"]


def test_json_content_file(tmp_path):
    f = tmp_path / "brief.json"
    f.write_text(json.dumps({"title": "From JSON", "body": "hello"}))
    content = load_content(f, MANIFEST)
    assert content["title"] == "From JSON"
    assert "hello" in content["body"]


def test_missing_required_slot_names_it(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text("---\ndate: 2026-07-05\n---\n")
    with pytest.raises(ContentError, match="title"):
        load_content(f, MANIFEST)


def test_unknown_key_named(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text("---\ntitle: T\nbogus: 1\n---\nbody text\n")
    with pytest.raises(ContentError, match="bogus"):
        load_content(f, MANIFEST)


def test_unknown_key_in_json(tmp_path):
    f = tmp_path / "brief.json"
    f.write_text(json.dumps({"title": "T", "body": "x", "nope": 1}))
    with pytest.raises(ContentError, match="nope"):
        load_content(f, MANIFEST)


def test_body_reserved_in_frontmatter(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text("---\ntitle: T\nbody: not allowed here\n---\nreal body\n")
    with pytest.raises(ContentError, match="reserved"):
        load_content(f, MANIFEST)


def test_unterminated_frontmatter(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text("---\ntitle: T\nno closing delimiter\n")
    with pytest.raises(ContentError, match="unterminated"):
        load_content(f, MANIFEST)


def test_malformed_yaml_frontmatter_raises_content_error(tmp_path):
    # yaml.YAMLError must surface as a clean ContentError naming the
    # file, not escape as a raw pyyaml traceback.
    f = tmp_path / "brief.md"
    f.write_text("---\ntitle: [unclosed\n---\nbody text\n")
    with pytest.raises(ContentError, match=r"brief\.md.*YAML"):
        load_content(f, MANIFEST)


def test_malformed_yaml_file_raises_content_error(tmp_path):
    f = tmp_path / "brief.yaml"
    f.write_text("title: [unclosed\n")
    with pytest.raises(ContentError, match=r"brief\.yaml.*YAML"):
        load_content(f, MANIFEST)


def test_data_slot_item_markdown_rendered(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text(
        "---\n"
        "title: T\n"
        "sections:\n"
        "  - heading: First\n"
        "    body: '**bold**'\n"
        "---\n"
        "body text\n"
    )
    content = load_content(f, MANIFEST)
    assert content["sections"][0]["heading"] == "First"
    assert "<strong>bold</strong>" in content["sections"][0]["body"]


def test_data_slot_unknown_item_key_named(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text(
        "---\n"
        "title: T\n"
        "sections:\n"
        "  - heading: First\n"
        "    surprise: oops\n"
        "---\n"
        "body text\n"
    )
    with pytest.raises(ContentError, match="surprise"):
        load_content(f, MANIFEST)


def test_data_slot_without_items_schema_passthrough(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text("---\ntitle: T\ntags: [alpha, beta]\n---\nbody text\n")
    content = load_content(f, MANIFEST)
    assert content["tags"] == ["alpha", "beta"]


def test_data_slot_must_be_list(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text("---\ntitle: T\ntags: not-a-list\n---\nbody text\n")
    with pytest.raises(ContentError, match="tags"):
        load_content(f, MANIFEST)


def test_unsupported_extension(tmp_path):
    f = tmp_path / "brief.txt"
    f.write_text("title: T\n")
    with pytest.raises(ContentError, match="unsupported"):
        load_content(f, MANIFEST)


# --- sanitization of markdown-rendered slots (see sanitize.py) ---

def test_markdown_body_raw_html_payloads_sanitized(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text(
        "---\ntitle: T\n---\n"
        "before\n\n"
        '<img src=x onerror=alert(1)>\n\n'
        "<svg onload=alert(1)><circle/></svg>\n\n"
        '<a href="javascript:alert(1)">click</a>\n\n'
        "after\n"
    )
    content = load_content(f, MANIFEST)
    body = str(content["body"])
    assert "onerror" not in body
    assert "onload" not in body
    assert "<svg" not in body
    assert "javascript:" not in body
    assert "<a>click</a>" in body  # tag survives, href dropped
    assert "before" in body and "after" in body


def test_markdown_body_script_content_removed(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text("---\ntitle: T\n---\n<script>document.write('pwn')</script>\n\nok\n")
    content = load_content(f, MANIFEST)
    body = str(content["body"])
    assert "script" not in body
    assert "document.write" not in body
    assert "ok" in body


def test_data_slot_markdown_item_sanitized(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text(
        "---\n"
        "title: T\n"
        "sections:\n"
        "  - heading: First\n"
        "    body: '<img src=x onerror=alert(1)> fine **bold**'\n"
        "---\n"
        "body text\n"
    )
    content = load_content(f, MANIFEST)
    section_body = str(content["sections"][0]["body"])
    assert "onerror" not in section_body
    assert '<img src="x" />' in section_body
    assert "<strong>bold</strong>" in section_body
