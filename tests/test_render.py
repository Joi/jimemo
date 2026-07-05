import base64
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo import cli, inline
from jimemo.errors import ContentError
from jimemo.inline import assemble_css
from jimemo.render import render_page, write_output
from markupsafe import Markup

# A valid 1x1 transparent PNG, small enough to inline as a literal.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAA"
    "AAYAAjCB0C8AAAAASUVORK5CYII="
)

BASIC_MANIFEST = """\
{
  "name": "test-tpl",
  "version": 1,
  "title": "Test Template",
  "slots": {
    "title": {"type": "text", "required": true},
    "body": {"type": "markdown", "required": true},
    "image": {"type": "text"}
  },
  "components": ["page-header", "stat-tile", "badge"],
  "charts": []
}
"""

BASIC_TEMPLATE = """\
{% extends "page.html.j2" %}
{% import "macros.html.j2" as ui %}
{% block title %}{{ title }}{% endblock %}
{% block content %}
{{ ui.page_header(title) }}
<div class="jm-prose">{{ body }}</div>
{{ ui.stat_tile("42", "Answer") }}
{% if image|default(none) %}<img src="{{ image }}" alt="a plate">{% endif %}
{% endblock %}
"""

SCRIPT_TEMPLATE = """\
{% extends "page.html.j2" %}
{% block title %}Bad{% endblock %}
{% block content %}
<script>alert("should never render");</script>
{% endblock %}
"""


def make_template_dir(tmp_path: Path, name: str, template_source: str, manifest_source: str = BASIC_MANIFEST) -> Path:
    template_dir = tmp_path / name
    template_dir.mkdir(parents=True)
    (template_dir / "manifest.json").write_text(manifest_source)
    (template_dir / "template.html.j2").write_text(template_source)
    return template_dir


def test_render_page_basic(tmp_path):
    template_dir = make_template_dir(tmp_path, "test-tpl", BASIC_TEMPLATE)
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    (content_dir / "img.png").write_bytes(TINY_PNG)

    content = {"title": "Hello", "body": Markup("<p>World</p>"), "image": "img.png"}
    html = render_page(template_dir, content, base_dir=content_dir)

    assert html.startswith("<!doctype html>")
    assert "<style>" in html
    assert "Hello" in html
    assert "<p>World</p>" in html
    assert "data:image/png;base64," in html
    assert "http://" not in html and "https://" not in html


def test_render_page_theme_sets_data_theme_attribute(tmp_path):
    template_dir = make_template_dir(tmp_path, "test-tpl", BASIC_TEMPLATE)
    content = {"title": "Hello", "body": Markup("<p>World</p>")}
    html = render_page(template_dir, content, theme="dark")
    assert 'data-theme="dark"' in html


def test_render_page_no_theme_omits_data_theme_attribute(tmp_path):
    # tokens.css legitimately mentions "data-theme" (its override selectors
    # and comments), so check the <html> opening tag specifically rather
    # than the whole document.
    template_dir = make_template_dir(tmp_path, "test-tpl", BASIC_TEMPLATE)
    content = {"title": "Hello", "body": Markup("<p>World</p>")}
    html = render_page(template_dir, content)
    html_tag = re.search(r"<html\b[^>]*>", html).group(0)
    assert "data-theme" not in html_tag


def test_assemble_css_print_force_wins_over_theme_root_override(tmp_path, monkeypatch):
    # A theme file is free to redefine `:root` at the same specificity as
    # base.css's print block. assemble_css must re-append print-force.css
    # after the theme so the print force is always the last occurrence in
    # source order (see inline.assemble_css's docstring / toolkit/print-force.css).
    fake_toolkit = tmp_path / "toolkit"
    (fake_toolkit / "components").mkdir(parents=True)
    (fake_toolkit / "themes").mkdir(parents=True)
    (fake_toolkit / "tokens.css").write_text("/* TOKENS-MARKER */\n:root { --jm-bg: white; }\n")
    (fake_toolkit / "base.css").write_text("/* BASE-MARKER */\n")
    (fake_toolkit / "themes" / "dark-brand.css").write_text(
        "/* THEME-MARKER */\n:root { --jm-bg: black; }\n"
    )
    (fake_toolkit / "print-force.css").write_text(
        "/* PRINT-FORCE-MARKER */\n@media print { :root { --jm-bg: white; } }\n"
    )

    monkeypatch.setattr(inline, "TOOLKIT_DIR", fake_toolkit)

    css = assemble_css({"components": []}, theme="dark-brand")

    assert "THEME-MARKER" in css
    assert "PRINT-FORCE-MARKER" in css
    assert css.index("PRINT-FORCE-MARKER") > css.index("THEME-MARKER")


def test_render_page_missing_local_image_raises(tmp_path):
    template_dir = make_template_dir(tmp_path, "test-tpl", BASIC_TEMPLATE)
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    content = {"title": "Hello", "body": Markup("<p>World</p>"), "image": "missing.png"}

    with pytest.raises(ContentError, match="missing.png"):
        render_page(template_dir, content, base_dir=content_dir)


def test_render_page_script_without_charts_raises_and_writes_nothing(tmp_path):
    template_dir = make_template_dir(
        tmp_path,
        "script-tpl",
        SCRIPT_TEMPLATE,
        manifest_source=BASIC_MANIFEST.replace('"components": ["page-header", "stat-tile", "badge"]', '"components": []'),
    )
    content = {"title": "Hello", "body": Markup("<p>World</p>")}

    with pytest.raises(ContentError, match="script"):
        render_page(template_dir, content)


def test_write_output_creates_parent_dirs(tmp_path):
    out_path = tmp_path / "nested" / "dir" / "page.html"
    write_output("<html></html>", out_path)
    assert out_path.read_text(encoding="utf-8") == "<html></html>"


# --- CLI integration ---

def test_cli_render_writes_file(tmp_path, monkeypatch):
    template_dir = make_template_dir(tmp_path / "templates", "test-tpl", BASIC_TEMPLATE)
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [tmp_path / "templates"])

    content_file = tmp_path / "content.md"
    content_file.write_text("---\ntitle: From CLI\n---\nBody **text**.\n")
    out_path = tmp_path / "out.html"

    rc = cli.main(["render", "test-tpl", str(content_file), "-o", str(out_path)])
    assert rc == 0
    assert out_path.is_file()
    html = out_path.read_text(encoding="utf-8")
    assert "From CLI" in html
    assert "<strong>text</strong>" in html


def test_cli_render_lint_error_writes_no_file(tmp_path, monkeypatch, capsys):
    make_template_dir(
        tmp_path / "templates",
        "script-tpl",
        SCRIPT_TEMPLATE,
        manifest_source=BASIC_MANIFEST.replace(
            '"name": "test-tpl"', '"name": "script-tpl"'
        ).replace('"components": ["page-header", "stat-tile", "badge"]', '"components": []'),
    )
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [tmp_path / "templates"])

    content_file = tmp_path / "content.md"
    content_file.write_text("---\ntitle: From CLI\n---\nBody text.\n")
    out_path = tmp_path / "out.html"

    rc = cli.main(["render", "script-tpl", str(content_file), "-o", str(out_path)])
    assert rc == 1
    assert not out_path.exists()
    assert "script" in capsys.readouterr().err


def test_cli_render_auto_errors_exit_2(tmp_path, capsys):
    content_file = tmp_path / "content.md"
    content_file.write_text("---\ntitle: X\n---\nbody\n")
    rc = cli.main(["render", "auto", str(content_file)])
    assert rc == 2
    assert "coming in this phase" in capsys.readouterr().err


def test_cli_render_unknown_template(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [tmp_path / "templates"])
    content_file = tmp_path / "content.md"
    content_file.write_text("---\ntitle: X\n---\nbody\n")
    rc = cli.main(["render", "nope", str(content_file)])
    assert rc == 1
