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


# ---------------------------------------------------------------------------
# scaffold (content skeletons) -- `jimemo scaffold <template>`
# ---------------------------------------------------------------------------

def _blank(value):
    """The same shape as `value`, values emptied: strings -> "", numbers
    -> 0, bools -> False, lists -> one blanked exemplar element, dicts ->
    every key blanked. Used to turn a template's real sample data into a
    fill-in skeleton without inventing a second schema language."""
    if isinstance(value, str):
        return ""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return 0
    if isinstance(value, list):
        return [_blank(value[0])] if value else []
    if isinstance(value, dict):
        return {k: _blank(v) for k, v in value.items()}
    return None


def _sample_shape(template_dir: Path, slot_name: str):
    """Blanked shape of `slot_name` taken from the template's first
    sample content file that provides it, or None. The sample is the
    template's own worked example, so its shape is authoritative for
    schema-free data slots."""
    sample_dir = Path(template_dir) / "sample"
    if not sample_dir.is_dir():
        return None
    from .content import _load_raw

    for p in sorted(sample_dir.iterdir()):
        if p.suffix.lower() not in (".md", ".json", ".yaml", ".yml"):
            continue
        try:
            raw = _load_raw(p)
        except Exception:
            continue
        if isinstance(raw, dict) and slot_name in raw:
            return _blank(raw[slot_name])
    return None


def scaffold_content(manifest: dict, template_dir) -> "tuple[str, str]":
    """A fill-in content skeleton for `manifest`'s slots; returns
    ``(text, kind)`` where kind is ``"md"`` (frontmatter + body
    placeholder, for templates with a ``body`` slot) or ``"yaml"``.

    Every slot appears with an emptied value and a ``required``/
    ``optional`` + type annotation. Data slots use their manifest
    ``items`` spec when declared, else a blanked copy of the template's
    sample data; the skeleton always parses through load_content as-is.
    """
    import textwrap

    from ._vendor import add_vendor_to_path

    add_vendor_to_path()
    import yaml

    slots = manifest["slots"]
    has_body = "body" in slots
    lines = []
    for name, spec in slots.items():
        if name == "body":
            continue
        req = "required" if spec.get("required") else "optional"
        stype = spec["type"]
        comment = f"# {req} · {stype}"
        if stype in ("text", "markdown"):
            lines.append(f'{name}: ""  {comment}')
            continue
        items = spec.get("items")
        shape = [{k: "" for k in items}] if items else _sample_shape(template_dir, name)
        if shape is None:
            lines.append(f"{name}: []  {comment} -- see the template's sample/ for the shape")
            continue
        lines.append(f"{name}:  {comment}")
        dumped = yaml.safe_dump(shape, default_flow_style=False, sort_keys=False)
        lines.append(textwrap.indent(dumped.rstrip("\n"), "  "))
    yaml_block = "\n".join(lines) + "\n"

    if has_body:
        req = "required" if slots["body"].get("required") else "optional"
        return (
            f"---\n{yaml_block}---\n\n(body markdown goes here -- {req})\n",
            "md",
        )
    return yaml_block, "yaml"
