"""Load and validate a template's manifest.json (Manifest v1).

See docs/superpowers/plans/2026-07-05-jimemo-phase3-core.md, "Binding
contracts", for the schema enforced here.
"""
import json
from pathlib import Path
from typing import Any, Dict

from .errors import ManifestError

SLOT_TYPES = ("text", "markdown", "data")
# Names render_page injects into the template context itself; a slot
# with one of these names would be silently shadowed at render time.
RESERVED_SLOT_NAMES = ("manifest", "styles", "theme")
ITEM_TYPES = ("text", "markdown")
CONTENT_KINDS = (
    "narrative",
    "photo-heavy",
    "tabular-data",
    "chronological",
    "hierarchical",
)


def load_manifest(template_dir: Path) -> Dict[str, Any]:
    template_dir = Path(template_dir)
    path = template_dir / "manifest.json"
    if not path.is_file():
        raise ManifestError(f"manifest not found: {path}")

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ManifestError(f"cannot read manifest {path}: {e}") from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ManifestError(f"manifest {path} is not valid JSON: {e}") from e

    if not isinstance(data, dict):
        raise ManifestError(f"manifest {path} must be a JSON object")

    for field in ("name", "version", "title", "slots"):
        if field not in data:
            raise ManifestError(f"manifest missing required field: {field!r}")

    if not isinstance(data["name"], str) or not data["name"]:
        raise ManifestError("manifest field 'name' must be a non-empty string")

    if data["version"] != 1:
        raise ManifestError(
            f"manifest field 'version' must be 1, got {data['version']!r}"
        )

    if not isinstance(data["title"], str) or not data["title"]:
        raise ManifestError("manifest field 'title' must be a non-empty string")

    slots = data["slots"]
    if not isinstance(slots, dict) or not slots:
        raise ManifestError("manifest field 'slots' must be a non-empty object")

    for slot_name, slot in slots.items():
        if slot_name in RESERVED_SLOT_NAMES:
            raise ManifestError(
                f"slot name {slot_name!r} collides with a reserved render "
                f"context name (reserved: {list(RESERVED_SLOT_NAMES)})"
            )
        if not isinstance(slot, dict):
            raise ManifestError(f"slot {slot_name!r} must be an object")
        slot_type = slot.get("type")
        if slot_type not in SLOT_TYPES:
            raise ManifestError(
                f"slot {slot_name!r} has invalid type {slot_type!r} "
                f"(must be one of {list(SLOT_TYPES)})"
            )
        if slot_type == "data":
            items = slot.get("items")
            if items is not None:
                if not isinstance(items, dict) or not items:
                    raise ManifestError(
                        f"slot {slot_name!r} 'items' must be a non-empty object"
                    )
                for item_key, item_type in items.items():
                    if item_type not in ITEM_TYPES:
                        raise ManifestError(
                            f"slot {slot_name!r} item {item_key!r} has invalid "
                            f"type {item_type!r} (must be one of {list(ITEM_TYPES)})"
                        )

    data.setdefault("description", "")
    if not isinstance(data["description"], str):
        raise ManifestError("manifest field 'description' must be a string")

    data.setdefault("components", [])
    if not isinstance(data["components"], list):
        raise ManifestError("manifest field 'components' must be a list")
    for item in data["components"]:
        if not isinstance(item, str):
            raise ManifestError(
                f"manifest field 'components' must be a list of strings, got {item!r}"
            )

    data.setdefault("charts", [])
    if not isinstance(data["charts"], list):
        raise ManifestError("manifest field 'charts' must be a list")
    for item in data["charts"]:
        if not isinstance(item, str):
            raise ManifestError(
                f"manifest field 'charts' must be a list of strings, got {item!r}"
            )

    suitability = data.get("suitability", {})
    if not isinstance(suitability, dict):
        raise ManifestError("manifest field 'suitability' must be an object")

    if "keywords" in suitability:
        keywords = suitability["keywords"]
        if not isinstance(keywords, list):
            raise ManifestError("suitability.keywords must be a list of strings")
        for kw in keywords:
            if not isinstance(kw, str):
                raise ManifestError(
                    f"suitability.keywords must be a list of strings, got {kw!r}"
                )

    if "content_kinds" in suitability:
        content_kinds = suitability["content_kinds"]
        if not isinstance(content_kinds, list):
            raise ManifestError("suitability.content_kinds must be a list of strings")
        for kind in content_kinds:
            if not isinstance(kind, str):
                raise ManifestError(
                    f"suitability.content_kinds must be a list of strings, got {kind!r}"
                )
            if kind not in CONTENT_KINDS:
                raise ManifestError(
                    f"suitability.content_kinds has invalid kind {kind!r} "
                    f"(must be one of {list(CONTENT_KINDS)})"
                )

    if "good_for" in suitability and not isinstance(suitability["good_for"], str):
        raise ManifestError(
            f"suitability.good_for must be a string, got {suitability['good_for']!r}"
        )

    if "labeled_hash" in suitability and not isinstance(suitability["labeled_hash"], str):
        raise ManifestError(
            f"suitability.labeled_hash must be a string, got {suitability['labeled_hash']!r}"
        )

    data["suitability"] = suitability

    return data
