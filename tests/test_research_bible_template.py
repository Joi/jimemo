"""research-bible template: manifest-valid sparse data rows must render.

The Manifest v1 item schema types fields without requiring them, and the
Jinja environment renders with StrictUndefined, so the template has to
normalize data-slot rows before handing them to macros or emitting them.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo._paths import REPO_ROOT
from jimemo.content import load_content
from jimemo.manifest import load_manifest
from jimemo.render import render_page

TEMPLATE_DIR = REPO_ROOT / "templates" / "research-bible"

SPARSE_CONTENT = """\
title: "Sparse rows"
provenance:
  - label: "Sources"
  - value: "12"
legend:
  - tag: "FLAG"
  - meaning: "Meaning only"
sections:
  - heading: "Only a heading"
  - body: "Only a body."
  - {}
"""


def test_sparse_data_rows_render(tmp_path):
    content_path = tmp_path / "sparse.yaml"
    content_path.write_text(SPARSE_CONTENT, encoding="utf-8")
    manifest = load_manifest(TEMPLATE_DIR)
    content = load_content(content_path, manifest)

    html = render_page(TEMPLATE_DIR, content, base_dir=tmp_path)

    assert "Only a heading" in html
    assert "Only a body." in html
    # Rows with a missing heading fall back to a positional label, in
    # both the table of contents and the section itself.
    assert html.count("Section 2") == 2
    assert html.count("Section 3") == 2
