"""Parse and validate a content file against a template's manifest.

Content file formats:
    .md          YAML frontmatter (``---`` ... ``---``) holds every slot
                 value except the markdown body, which is everything
                 after the closing delimiter and fills the slot named
                 ``body``.
    .json        a single JSON object keyed by slot name.
    .yaml/.yml   a single YAML mapping keyed by slot name.

Unknown keys and missing required slots are reported by name so authors
can fix a typo without guessing. Markdown-typed slot values are rendered
to HTML here, sanitized (allowlist — see sanitize.py; python-markdown
passes raw HTML through verbatim, and content may come from untrusted
sources), and returned as ``markupsafe.Markup`` so the (autoescaping)
render step passes them through unescaped; every other slot value is
returned as parsed/raw and relies on Jinja2 autoescape for safety.
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ._vendor import add_vendor_to_path
from .errors import ContentError
from .sanitize import sanitize_html

add_vendor_to_path()
import markdown  # noqa: E402
import yaml  # noqa: E402
from markupsafe import Markup  # noqa: E402

# Fully-qualified dotted paths, not the short names ("tables"): Markdown's
# extension loader resolves short names via installed-package entry-point
# metadata, which a vendored (not pip-installed) copy never has. The dotted
# path is Markdown's own documented fallback and works either way.
MARKDOWN_EXTENSIONS = ["markdown.extensions.tables", "markdown.extensions.fenced_code"]


def _render_markdown(text: str) -> Markup:
    # Sole markdown->HTML path (top-level markdown slots AND markdown
    # items in data slots), so sanitizing here covers both. Runs before
    # inline_images, so authored img src are still paths/URLs.
    return Markup(sanitize_html(markdown.markdown(text, extensions=MARKDOWN_EXTENSIONS)))


def _coerce_text(path: Path, slot_name: str, value: Any) -> str:
    """YAML's safe_load auto-parses unquoted ISO-date-looking scalars
    (e.g. `date: 2026-07-05`) into datetime.date/datetime objects, and
    bare true/false/numbers into bool/int/float. A manifest "text" slot
    should reliably be a string regardless, so coerce any scalar and
    reject structured values outright."""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, dict)):
        raise ContentError(
            f"{path}: slot {slot_name!r} must be text, got {type(value).__name__}"
        )
    return str(value)


def _parse_frontmatter(path: Path, text: str) -> Tuple[Dict[str, Any], str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text

    closing = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing = i
            break
    if closing is None:
        raise ContentError(
            f"{path}: unterminated frontmatter block (missing closing '---')"
        )

    fm_text = "".join(lines[1:closing])
    body = "".join(lines[closing + 1:])

    try:
        parsed = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        # str(e) on a MarkedYAMLError includes the line/column and problem.
        raise ContentError(f"{path}: invalid YAML frontmatter: {e}") from e
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise ContentError(f"{path}: frontmatter must be a YAML mapping")

    return parsed, body


def _load_raw(path: Path) -> Dict[str, Any]:
    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ContentError(f"cannot read content file {path}: {e}") from e

    if suffix == ".md":
        values, body_md = _parse_frontmatter(path, text)
        if "body" in values:
            raise ContentError(
                f"{path}: 'body' is reserved for the markdown body below the "
                "frontmatter in .md content files and cannot also be set "
                "as a frontmatter key"
            )
        body_md = body_md.strip("\n")
        if body_md:
            values["body"] = body_md
        return values

    if suffix in (".yaml", ".yml"):
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ContentError(f"{path}: invalid YAML: {e}") from e
    elif suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ContentError(f"{path} is not valid JSON: {e}") from e
    else:
        raise ContentError(f"unsupported content file type: {path.suffix!r} ({path})")

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ContentError(f"{path}: content must be an object keyed by slot name")
    return data


def _load_data_slot(
    path: Path, slot_name: str, slot_spec: Dict[str, Any], value: Any
) -> List[Any]:
    if not isinstance(value, list):
        raise ContentError(f"{path}: slot {slot_name!r} must be a list of items")

    items_schema = slot_spec.get("items")
    if not items_schema:
        return value

    rendered: List[Any] = []
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            raise ContentError(f"{path}: slot {slot_name!r} item {idx} must be an object")
        for key in item:
            if key not in items_schema:
                raise ContentError(
                    f"{path}: slot {slot_name!r} item {idx} has unknown key {key!r}"
                )
        new_item: Dict[str, Any] = {}
        for key, val in item.items():
            if val is None:
                continue
            if items_schema[key] == "markdown":
                if not isinstance(val, str):
                    raise ContentError(
                        f"{path}: slot {slot_name!r} item {idx} key {key!r} "
                        "must be text"
                    )
                new_item[key] = _render_markdown(val)
            else:
                new_item[key] = _coerce_text(path, f"{slot_name}[{idx}].{key}", val)
        rendered.append(new_item)
    return rendered


def load_content(path: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    path = Path(path)
    raw = _load_raw(path)
    slots: Dict[str, Any] = manifest.get("slots", {})

    for key in raw:
        if key not in slots:
            raise ContentError(f"{path}: unknown slot {key!r} (not declared in manifest)")

    for slot_name, slot_spec in slots.items():
        if slot_spec.get("required") and (slot_name not in raw or raw[slot_name] is None):
            raise ContentError(f"{path}: missing required slot {slot_name!r}")

    result: Dict[str, Any] = {}
    for slot_name, value in raw.items():
        if value is None:
            continue
        slot_spec = slots[slot_name]
        slot_type = slot_spec["type"]
        if slot_type == "markdown":
            if not isinstance(value, str):
                raise ContentError(f"{path}: slot {slot_name!r} must be text (markdown)")
            result[slot_name] = _render_markdown(value)
        elif slot_type == "data":
            result[slot_name] = _load_data_slot(path, slot_name, slot_spec, value)
        else:  # text
            result[slot_name] = _coerce_text(path, slot_name, value)

    return result
