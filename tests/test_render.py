import base64
import re
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo import cli, inline
from jimemo.errors import ContentError
from jimemo.inline import assemble_css, inline_images
from jimemo.lint import lint_html
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


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Every test in this file runs with HOME pointed at an empty,
    per-test directory. `assemble_css` (via `personal_themes_dir`)
    checks `~/.jimemo/themes/<theme>.css` before any repo/fake toolkit
    dir a test sets up, so leaving the real HOME in place would let a
    theme file that happens to exist on the machine running the suite
    shadow the fixture under test -- see
    test_assemble_css_print_force_wins_over_theme_root_override, which
    monkeypatches TOOLKIT_DIR but relies on this fixture for the
    personal-dir side of theme resolution."""
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))


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


def test_inline_absolute_image_path_rejected(tmp_path):
    # Content may be untrusted: ![](/etc/passwd) must never read and
    # embed a file from outside the content directory.
    template_dir = make_template_dir(tmp_path, "test-tpl", BASIC_TEMPLATE)
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    content = {"title": "Hello", "body": Markup("<p>World</p>"), "image": "/etc/passwd"}

    with pytest.raises(ContentError, match=r"/etc/passwd.*absolute"):
        render_page(template_dir, content, base_dir=content_dir)


def test_inline_dotdot_escape_rejected(tmp_path):
    template_dir = make_template_dir(tmp_path, "test-tpl", BASIC_TEMPLATE)
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    # The target exists, so this would previously have been embedded.
    (tmp_path / "secret.png").write_bytes(TINY_PNG)
    content = {"title": "Hello", "body": Markup("<p>World</p>"), "image": "../secret.png"}

    with pytest.raises(ContentError, match=r"\.\./secret\.png.*escapes"):
        render_page(template_dir, content, base_dir=content_dir)


def test_inline_non_image_extension_rejected(tmp_path):
    template_dir = make_template_dir(tmp_path, "test-tpl", BASIC_TEMPLATE)
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    (content_dir / "notes.txt").write_text("not an image")
    content = {"title": "Hello", "body": Markup("<p>World</p>"), "image": "notes.txt"}

    with pytest.raises(ContentError, match=r"notes\.txt.*image type"):
        render_page(template_dir, content, base_dir=content_dir)


def test_inline_subdirectory_image_within_base_dir_works(tmp_path):
    template_dir = make_template_dir(tmp_path, "test-tpl", BASIC_TEMPLATE)
    content_dir = tmp_path / "content"
    (content_dir / "img").mkdir(parents=True)
    (content_dir / "img" / "x.png").write_bytes(TINY_PNG)
    content = {"title": "Hello", "body": Markup("<p>World</p>"), "image": "img/x.png"}

    html = render_page(template_dir, content, base_dir=content_dir)
    assert "data:image/png;base64," in html


def test_inline_images_rewrites_srcset_candidates(tmp_path):
    (tmp_path / "a.png").write_bytes(TINY_PNG)
    (tmp_path / "b.png").write_bytes(TINY_PNG)
    html = '<img src="a.png" srcset="a.png 1x, b.png 2x" alt="x">'

    out, warnings = inline_images(html, tmp_path)

    assert out.count("data:image/png;base64,") == 3  # src + both candidates
    assert "a.png" not in out and "b.png" not in out
    assert warnings == []
    # The rewritten page is self-contained: it passes the lint allowlist,
    # including the commas inside each inlined data: URI candidate.
    errors, _ = lint_html(out, {"charts": []})
    assert errors == []


def test_inline_images_rewrites_source_src_and_video_poster(tmp_path):
    (tmp_path / "a.png").write_bytes(TINY_PNG)
    html = (
        '<video poster="a.png"><source srcset="a.png 2x"></video>'
        '<picture><source src="a.png"><img src="a.png"></picture>'
    )

    out, _ = inline_images(html, tmp_path)

    assert out.count("data:image/png;base64,") == 4
    assert "a.png" not in out


def test_inline_images_srcset_missing_candidate_raises(tmp_path):
    (tmp_path / "a.png").write_bytes(TINY_PNG)
    html = '<img src="a.png" srcset="a.png 1x, nope.png 2x">'

    with pytest.raises(ContentError, match="nope.png"):
        inline_images(html, tmp_path)


def test_inline_images_srcset_traversal_and_extension_rejected(tmp_path):
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    (tmp_path / "secret.png").write_bytes(TINY_PNG)

    with pytest.raises(ContentError, match=r"\.\./secret\.png.*escapes"):
        inline_images('<img srcset="../secret.png 2x">', content_dir)

    (content_dir / "notes.txt").write_text("not an image")
    with pytest.raises(ContentError, match=r"notes\.txt.*image type"):
        inline_images('<img srcset="notes.txt 2x">', content_dir)


def test_inline_images_srcset_remote_candidate_left_with_warning(tmp_path):
    (tmp_path / "a.png").write_bytes(TINY_PNG)
    html = '<img srcset="a.png 1x, https://evil.example/x.png 2x">'

    out, warnings = inline_images(html, tmp_path)

    assert "data:image/png;base64," in out
    assert "https://evil.example/x.png 2x" in out  # left for lint to reject
    assert any("https://evil.example/x.png" in w for w in warnings)
    errors, _ = lint_html(out, {"charts": []})
    assert any("https://evil.example/x.png" in e for e in errors)


def test_inline_images_srcset_data_uri_candidate_preserved(tmp_path):
    (tmp_path / "a.png").write_bytes(TINY_PNG)
    html = '<img srcset="data:image/png;base64,AAAA 1x, a.png 2x">'

    out, warnings = inline_images(html, tmp_path)

    assert "data:image/png;base64,AAAA 1x" in out
    assert out.count("data:image/png;base64,") == 2
    assert "a.png" not in out
    errors, _ = lint_html(out, {"charts": []})
    assert errors == []


def test_inline_images_local_svg_rejected(tmp_path):
    # .svg is not inlineable: it could only ever produce the
    # data:image/svg+xml URI that lint rejects (SVG can carry markup),
    # so it is rejected here with the clearer, earlier message.
    (tmp_path / "a.svg").write_text("<svg/>")

    with pytest.raises(ContentError, match=r"a\.svg.*image type"):
        inline_images('<img src="a.svg">', tmp_path)


def test_render_page_inlines_img_srcset_end_to_end(tmp_path):
    srcset_template = BASIC_TEMPLATE.replace(
        '<img src="{{ image }}" alt="a plate">',
        '<img src="{{ image }}" srcset="{{ image }} 1x, {{ image }} 2x" alt="a plate">',
    )
    template_dir = make_template_dir(tmp_path, "srcset-tpl", srcset_template)
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    (content_dir / "img.png").write_bytes(TINY_PNG)
    content = {"title": "Hello", "body": Markup("<p>World</p>"), "image": "img.png"}

    html = render_page(template_dir, content, base_dir=content_dir)

    assert html.count("data:image/png;base64,") == 3
    assert "img.png" not in html


def test_render_page_svg_data_uri_image_slot_fails_closed(tmp_path):
    # A text/data slot value (e.g. photo-catalog's `image` field) goes
    # straight into a macro's <img src>, bypassing markdown sanitization
    # entirely. data:image/svg+xml can itself carry markup/script, so it
    # must never reach the written page.
    template_dir = make_template_dir(tmp_path, "test-tpl", BASIC_TEMPLATE)
    content = {
        "title": "Hello",
        "body": Markup("<p>World</p>"),
        "image": "data:image/svg+xml,<svg onload=alert(1)>",
    }

    with pytest.raises(ContentError):
        render_page(template_dir, content)


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


def test_write_output_unwritable_path_raises_content_error(tmp_path):
    # A regular file where a parent directory is needed: mkdir fails with
    # an OSError, which must surface as a clean ContentError, not a traceback.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory")
    out_path = blocker / "page.html"

    with pytest.raises(ContentError, match="cannot write output"):
        write_output("<html></html>", out_path)


def test_render_prose_with_lint_lookalike_text_still_renders(tmp_path):
    # Regression for the regex-based lint false-failing on escaped TEXT:
    # "one = done" matched the on*= handler pattern and "javascript:"
    # matched the scheme pattern even inside inert prose.
    template_dir = make_template_dir(tmp_path, "test-tpl", BASIC_TEMPLATE)
    content = {
        "title": "Hello",
        "body": Markup(
            "<p>phase one = done</p>"
            "<p>never write onclick = x by hand</p>"
            "<p>javascript: is bad in URLs</p>"
        ),
    }
    html = render_page(template_dir, content)
    assert "phase one = done" in html


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


def test_cli_render_unknown_theme_exits_1_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    make_template_dir(tmp_path / "templates", "test-tpl", BASIC_TEMPLATE)
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [tmp_path / "templates"])

    content_file = tmp_path / "content.md"
    content_file.write_text("---\ntitle: From CLI\n---\nBody text.\n")
    out_path = tmp_path / "out.html"

    rc = cli.main([
        "render", "test-tpl", str(content_file), "-o", str(out_path),
        "--theme", "no-such-theme",
    ])
    assert rc == 1
    assert not out_path.exists()
    err = capsys.readouterr().err
    assert "unknown theme" in err
    assert "Traceback" not in err


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


def test_cli_render_unwritable_output_exits_1_cleanly(tmp_path, monkeypatch, capsys):
    template_dir = make_template_dir(tmp_path / "templates", "test-tpl", BASIC_TEMPLATE)
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [tmp_path / "templates"])

    content_file = tmp_path / "content.md"
    content_file.write_text("---\ntitle: From CLI\n---\nBody text.\n")
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory")

    rc = cli.main(["render", "test-tpl", str(content_file), "-o", str(blocker / "out.html")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "cannot write output" in err
    assert "Traceback" not in err


def test_cli_render_malformed_frontmatter_exits_1_cleanly(tmp_path, monkeypatch, capsys):
    make_template_dir(tmp_path / "templates", "test-tpl", BASIC_TEMPLATE)
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [tmp_path / "templates"])

    content_file = tmp_path / "content.md"
    content_file.write_text("---\ntitle: [unclosed\n---\nBody text.\n")

    rc = cli.main(["render", "test-tpl", str(content_file)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "content.md" in err
    assert "YAML" in err
    assert "Traceback" not in err


def test_cli_render_auto_no_templates_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [tmp_path / "templates"])
    content_file = tmp_path / "content.md"
    content_file.write_text("---\ntitle: X\n---\nbody\n")
    rc = cli.main(["render", "auto", str(content_file)])
    assert rc == 1
    assert "no templates to choose from" in capsys.readouterr().err


def test_cli_render_unknown_template(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "default_search_dirs", lambda: [tmp_path / "templates"])
    content_file = tmp_path / "content.md"
    content_file.write_text("---\ntitle: X\n---\nbody\n")
    rc = cli.main(["render", "nope", str(content_file)])
    assert rc == 1


def test_render_page_missing_template_file_raises_content_error(tmp_path):
    # manifest.json present, but template.html.j2 was never written (or
    # was deleted) -- must surface as a clean ContentError naming the
    # template, not a raw jinja2.TemplateNotFound traceback.
    template_dir = tmp_path / "no-template-tpl"
    template_dir.mkdir()
    (template_dir / "manifest.json").write_text(BASIC_MANIFEST)
    content = {"title": "Hello", "body": Markup("<p>World</p>")}

    with pytest.raises(ContentError, match=r"template\.html\.j2.*could not be loaded"):
        render_page(template_dir, content)


def test_render_page_template_syntax_error_raises_content_error(tmp_path):
    broken_template = BASIC_TEMPLATE.replace(
        "{% block content %}", "{% block content %}{% if %}"
    )
    template_dir = make_template_dir(tmp_path, "broken-tpl", broken_template)
    content = {"title": "Hello", "body": Markup("<p>World</p>")}

    with pytest.raises(ContentError, match=r"template\.html\.j2.*could not be loaded"):
        render_page(template_dir, content)


def test_render_page_undefined_template_value_raises_content_error(tmp_path):
    undefined_template = BASIC_TEMPLATE.replace(
        "{{ ui.stat_tile(\"42\", \"Answer\") }}", "{{ no_such_value }}"
    )
    template_dir = make_template_dir(tmp_path, "undef-tpl", undefined_template)
    content = {"title": "Hello", "body": Markup("<p>World</p>")}

    with pytest.raises(ContentError, match="undefined"):
        render_page(template_dir, content)


def test_assemble_css_unknown_component_raises_manifest_error():
    from jimemo.errors import ManifestError

    with pytest.raises(ManifestError, match="no-such-component"):
        assemble_css({"components": ["no-such-component"]})


# -- theme name validation: --theme must not be a filesystem path -------
#
# `_resolve_theme_path` used to build `<themes_dir>/<theme>.css` from the
# raw `--theme` value with no shape check at all. `Path.__truediv__`
# discards the left side when the right side is absolute, so
# `--theme /etc/passwd` (or, via `..`, any path reachable relative to a
# themes dir) could resolve straight to an arbitrary local file and
# inline its bytes into the page. These confirm every such value is
# rejected -- and the file is never even opened -- before any filesystem
# access, while a normal theme name is unaffected.


def _spy_on_read_text(monkeypatch, forbidden: Path):
    """Fail the test immediately if anything reads `forbidden` via
    Path.read_text, proving a rejected --theme value never reaches the
    file it points at (not just that the end result excludes its
    content) -- same technique as
    test_embed_fonts_rejects_traversal_even_when_target_exists."""
    original_read_text = Path.read_text

    def spying_read_text(self, *args, **kwargs):
        assert self != forbidden, f"traversal theme name must never read {forbidden}"
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spying_read_text)


def test_assemble_css_rejects_dotdot_theme_name(tmp_path, monkeypatch):
    from jimemo.errors import ManifestError

    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    outside = tmp_path / "secret.css"
    outside.write_text(":root { --leaked: yes; }\n", encoding="utf-8")
    _spy_on_read_text(monkeypatch, outside)

    with pytest.raises(ManifestError, match=re.escape("../../secret")):
        assemble_css({"components": []}, theme="../../secret")


def test_assemble_css_rejects_absolute_path_theme_name(tmp_path, monkeypatch):
    from jimemo.errors import ManifestError

    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    outside = tmp_path / "secret.css"
    outside.write_text(":root { --leaked: yes; }\n", encoding="utf-8")
    theme_value = str(outside.with_suffix(""))  # --theme <abs path, no .css>
    _spy_on_read_text(monkeypatch, outside)

    with pytest.raises(ManifestError, match=re.escape(theme_value)):
        assemble_css({"components": []}, theme=theme_value)


def test_assemble_css_rejects_theme_name_with_slash(tmp_path, monkeypatch):
    from jimemo.errors import ManifestError

    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))

    with pytest.raises(ManifestError, match="has/slash"):
        assemble_css({"components": []}, theme="has/slash")


def test_assemble_css_valid_theme_name_still_resolves(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    fake_toolkit = tmp_path / "toolkit"
    shutil.copytree(inline.TOOLKIT_DIR, fake_toolkit)
    (fake_toolkit / "themes").mkdir(exist_ok=True)
    (fake_toolkit / "themes" / "chiba.css").write_text(
        ":root { --jm-accent: #4c4499; }\n", encoding="utf-8"
    )
    monkeypatch.setattr(inline, "TOOLKIT_DIR", fake_toolkit)

    css = assemble_css({"components": []}, theme="chiba")
    assert "#4c4499" in css


# -- unknown --theme: error, not a silent unthemed render ----------------
#
# `_resolve_theme_path` returning None used to mean "skip the override
# quietly" for every well-formed name, which is correct for "light"/"dark"
# (no file by design) but wrong for a typo'd or never-imported theme: the
# page rendered anyway, with a `data-theme` attribute matching nothing and
# no error to explain why it looks unthemed.


def test_assemble_css_unknown_theme_raises_manifest_error(tmp_path, monkeypatch):
    from jimemo.errors import ManifestError

    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))

    with pytest.raises(ManifestError, match=re.escape("unknown theme 'no-such-theme'")):
        assemble_css({"components": []}, theme="no-such-theme")


def test_assemble_css_builtin_light_mode_does_not_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))

    css = assemble_css({"components": []}, theme="light")
    assert isinstance(css, str) and css  # no exception; base CSS still assembled


def test_assemble_css_builtin_dark_mode_does_not_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))

    css = assemble_css({"components": []}, theme="dark")
    assert isinstance(css, str) and css  # no exception; base CSS still assembled


def test_assemble_css_builtin_light_mode_ignores_planted_theme_file(tmp_path, monkeypatch):
    """A stray/legacy `light.css` sitting in the personal themes dir must
    never be applied for `--theme light`: `light` is a built-in mode name,
    and `_resolve_theme_path` short-circuits on `_BUILTIN_THEME_MODES`
    before it ever looks at the filesystem. `design.importer` already
    refuses to create a theme file under a reserved name, so this
    exercises the defense-in-depth path, not the normal one."""
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    themes_dir = inline.personal_themes_dir()
    themes_dir.mkdir(parents=True)
    marker = "/* PLANTED-LIGHT-THEME-MARKER */"
    (themes_dir / "light.css").write_text(marker, encoding="utf-8")

    css = assemble_css({"components": []}, theme="light")
    assert marker not in css


def test_assemble_css_builtin_dark_mode_ignores_planted_theme_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    themes_dir = inline.personal_themes_dir()
    themes_dir.mkdir(parents=True)
    marker = "/* PLANTED-DARK-THEME-MARKER */"
    (themes_dir / "dark.css").write_text(marker, encoding="utf-8")

    css = assemble_css({"components": []}, theme="dark")
    assert marker not in css


def test_render_page_unknown_theme_raises_manifest_error(tmp_path, monkeypatch):
    from jimemo.errors import ManifestError

    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    template_dir = make_template_dir(tmp_path, "test-tpl", BASIC_TEMPLATE)
    content = {"title": "Hello", "body": Markup("<p>World</p>")}

    with pytest.raises(ManifestError, match="unknown theme"):
        render_page(template_dir, content, theme="no-such-theme")
