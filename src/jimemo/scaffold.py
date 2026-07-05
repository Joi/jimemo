"""`new-template`: scaffold a fresh, self-rendering template directory
under ~/.jimemo/templates/ (personal templates, discovered alongside the
repo's own templates/ by jimemo.discovery).
"""
import hashlib
import json
import re
from pathlib import Path
from typing import Optional

from .errors import ScaffoldError

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

DEFAULT_DEST_ROOT = Path.home() / ".jimemo" / "templates"

TEMPLATE_J2 = """\
{% extends "page.html.j2" %}
{% import "macros.html.j2" as ui %}

{% block title %}{{ title }}{% endblock %}

{% block content %}
{{ ui.page_header(title) }}
<div class="jm-prose">
{{ body }}
</div>
{% endblock %}
"""

SAMPLE_CONTENT_MD = """\
---
title: {title}
---
Replace this with your content. **Markdown** is supported, including
tables and fenced code blocks.
"""


def create_template(name: str, dest_root: Optional[Path] = None) -> Path:
    """Scaffold templates/<name>/ (manifest.json, template.html.j2,
    sample/content.md) under `dest_root` (default ~/.jimemo/templates).
    Raises ScaffoldError if `name` is invalid or the template already
    exists. The scaffold renders out of the box: render_page(template_dir,
    load_content(sample/content.md, manifest)) succeeds unmodified."""
    if not NAME_RE.match(name):
        raise ScaffoldError(
            f"invalid template name {name!r}: must match ^[a-z0-9][a-z0-9-]*$ "
            "(lowercase letters, digits, hyphens; cannot start with a hyphen)"
        )

    root = Path(dest_root) if dest_root is not None else DEFAULT_DEST_ROOT
    template_dir = root / name
    if template_dir.exists():
        raise ScaffoldError(f"template already exists, refusing to overwrite: {template_dir}")

    template_dir.mkdir(parents=True)
    (template_dir / "template.html.j2").write_text(TEMPLATE_J2, encoding="utf-8")

    labeled_hash = hashlib.sha256(TEMPLATE_J2.encode("utf-8")).hexdigest()
    title = name.replace("-", " ").replace("_", " ").title()

    manifest = {
        "name": name,
        "version": 1,
        "title": title,
        "description": "TODO",
        "slots": {
            "title": {"type": "text", "required": True},
            "body": {"type": "markdown", "required": True},
        },
        "components": ["page-header"],
        "charts": [],
        "suitability": {
            "keywords": [],
            "content_kinds": [],
            "good_for": "TODO",
            "labeled_hash": labeled_hash,
        },
    }
    (template_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    sample_dir = template_dir / "sample"
    sample_dir.mkdir()
    (sample_dir / "content.md").write_text(
        SAMPLE_CONTENT_MD.format(title=title), encoding="utf-8"
    )

    return template_dir
