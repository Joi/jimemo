"""Deterministic, LLM-free template suitability scoring.

Given a content file and the list of discovered templates, `score_templates`
ranks templates by how well their declared `suitability` (manifest.json)
matches signals read straight off the content file's raw structure -- no
rendering, no network, no model calls. Used by the `suggest` and
`render auto` CLI commands.

See docs/superpowers/plans/2026-07-05-jimemo-phase3-core.md, "Task 6", for
the design this module implements.
"""
import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ._vendor import add_vendor_to_path
from .errors import ContentError
from .manifest import load_manifest

add_vendor_to_path()
import yaml  # noqa: E402

# --- Weights -------------------------------------------------------------
# Points added to a template's score when a content signal fires and the
# template's suitability.content_kinds declares the matching kind. The
# four structural signals are independent and may co-fire (a dated photo
# timeline is both photo-heavy and chronological); `narrative` alone is a
# fallback that only applies when none of the structural signals fired.
# content_kind values match the vocabulary in jimemo.manifest.CONTENT_KINDS.
#
#   constant                      | content signal                          | content_kind
#   --------------------------------------------------------------------------------------
#   PHOTO_HEAVY_IMAGE_THRESHOLD/  | >= threshold image references           | photo-heavy
#     _BONUS                      |   (markdown images, <img src>, and      |
#                                  |   photos/images array length)          |
#   CHRONOLOGICAL_DATE_THRESHOLD/ | >= threshold ISO-8601-ish dates         | chronological
#     _BONUS                      |                                         |
#   TABULAR_BONUS                 | a top-level slot value is a list of    | tabular-data
#                                  |   >= 2 objects (records)                |
#   HIERARCHICAL_MIN_DEPTH/       | nesting depth >= threshold              | hierarchical
#     _BONUS                      |                                         |
#   NARRATIVE_BASELINE_BONUS /    | none of the above fired, and word count | narrative
#     NARRATIVE_MIN_WORDS         |   >= threshold (substantial prose)      |
#   KEYWORD_MATCH_BONUS           | per case-folded suitability.keywords    | (any)
#                                  |   entry found as a substring of the     |
#                                  |   content text                          |
#   STALE_LABEL_PENALTY_FACTOR    | template.html.j2's sha256 != the         | (any, multiplicative,
#                                  |   manifest's suitability.labeled_hash   |   applied last)

PHOTO_HEAVY_IMAGE_THRESHOLD = 4
PHOTO_HEAVY_BONUS = 3.0

CHRONOLOGICAL_DATE_THRESHOLD = 3
CHRONOLOGICAL_BONUS = 2.0

TABULAR_MIN_RECORDS = 2
TABULAR_BONUS = 3.0

HIERARCHICAL_MIN_DEPTH = 3
HIERARCHICAL_BONUS = 3.0

NARRATIVE_MIN_WORDS = 120
NARRATIVE_BASELINE_BONUS = 1.0

KEYWORD_MATCH_BONUS = 1.0

STALE_LABEL_PENALTY_FACTOR = 0.8

ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
HTML_IMG_SRC_RE = re.compile(r"<img\b[^>]*\bsrc=", re.IGNORECASE)
WORD_RE = re.compile(r"[A-Za-z0-9']+")
PHOTO_ARRAY_KEYS = ("photos", "images")


# --- Manifest-independent raw content read --------------------------------
# Deliberately separate from jimemo.content.load_content: that function
# validates a content file against one specific template's slots (raising
# on unknown keys), but suggest must read the same content file's raw
# shape against every candidate template's differing manifest.

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

    parsed = yaml.safe_load(fm_text)
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise ContentError(f"{path}: frontmatter must be a YAML mapping")

    return parsed, body


def _load_raw_content(path: Path) -> Dict[str, Any]:
    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ContentError(f"cannot read content file {path}: {e}") from e

    if suffix == ".md":
        values, body = _parse_frontmatter(path, text)
        body = body.strip("\n")
        if body:
            values = dict(values)
            values["body"] = body
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


# --- Content signals -------------------------------------------------------

def _flatten_strings(value: Any, acc: List[str]) -> None:
    if isinstance(value, str):
        acc.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            _flatten_strings(v, acc)
    elif isinstance(value, list):
        for v in value:
            _flatten_strings(v, acc)


def _count_date_values(value: Any) -> int:
    if isinstance(value, (datetime.date, datetime.datetime)):
        return 1
    if isinstance(value, str):
        return len(ISO_DATE_RE.findall(value))
    if isinstance(value, dict):
        return sum(_count_date_values(v) for v in value.values())
    if isinstance(value, list):
        return sum(_count_date_values(v) for v in value)
    return 0


def _sum_photo_arrays(value: Any) -> int:
    total = 0
    if isinstance(value, dict):
        for key, v in value.items():
            if key.casefold() in PHOTO_ARRAY_KEYS and isinstance(v, list):
                total += len(v)
            total += _sum_photo_arrays(v)
    elif isinstance(value, list):
        for item in value:
            total += _sum_photo_arrays(item)
    return total


def _has_tabular_shape(raw: Dict[str, Any]) -> bool:
    for value in raw.values():
        if (
            isinstance(value, list)
            and len(value) >= TABULAR_MIN_RECORDS
            and all(isinstance(item, dict) for item in value)
        ):
            return True
    return False


def _depth_of(value: Any) -> int:
    if isinstance(value, dict) and value:
        return 1 + max((_depth_of(v) for v in value.values()), default=0)
    if isinstance(value, list) and value:
        return 1 + max((_depth_of(v) for v in value), default=0)
    return 0


def _max_depth(raw: Dict[str, Any]) -> int:
    """Deepest nesting under any one slot value. Deliberately does not
    count the top-level `raw` mapping itself as a nesting level -- that's
    just "the set of slots", not structure the content author created --
    so a flat table (a slot whose value is a list of flat records) comes
    out at depth 2 (list, then record dict), not 3. Otherwise a top-level
    array-of-objects (the `tabular` signal) would always also trip the
    hierarchical threshold, since dict-of-slots -> list -> record-dict is
    3 levels by construction."""
    return max((_depth_of(v) for v in raw.values()), default=0)


def _kind_bonuses(
    raw: Dict[str, Any],
    image_count: int,
    date_count: int,
    tabular: bool,
    depth: int,
    word_count: int,
) -> List[Tuple[str, float, str]]:
    """Content_kind -> (bonus, reason) for every kind this content matches.
    The structural signals (photo-heavy, chronological, tabular-data,
    hierarchical) are independent and can co-fire -- e.g. a dated photo
    timeline is legitimately both photo-heavy and chronological.
    `narrative` is the exception: a pure fallback that only fires when
    none of the structural signals did, so a content file with 6 photos
    and no other structure reads as photo-heavy, not narrative."""
    bonuses: List[Tuple[str, float, str]] = []

    if image_count >= PHOTO_HEAVY_IMAGE_THRESHOLD:
        bonuses.append(
            ("photo-heavy", PHOTO_HEAVY_BONUS, f"{image_count} image references -> photo-heavy")
        )
    if date_count >= CHRONOLOGICAL_DATE_THRESHOLD:
        bonuses.append(
            ("chronological", CHRONOLOGICAL_BONUS, f"{date_count} dated entries -> chronological")
        )
    if tabular:
        bonuses.append(
            ("tabular-data", TABULAR_BONUS, "top-level list of records -> tabular-data")
        )
    if depth >= HIERARCHICAL_MIN_DEPTH:
        bonuses.append(
            ("hierarchical", HIERARCHICAL_BONUS, f"nesting depth {depth} -> hierarchical")
        )
    if not bonuses and word_count >= NARRATIVE_MIN_WORDS:
        bonuses.append(
            ("narrative", NARRATIVE_BASELINE_BONUS,
             f"{word_count} words, no strong structural signal -> narrative")
        )

    return bonuses


def is_stale_labels(manifest: Dict[str, Any], template_dir: Path) -> bool:
    """True if `template_dir`'s template.html.j2 has changed since its
    suitability labels were written (sha256 mismatch against
    suitability.labeled_hash). False if there's no recorded hash to
    compare, or no template.html.j2 to hash."""
    labeled_hash = manifest.get("suitability", {}).get("labeled_hash")
    template_path = template_dir / "template.html.j2"
    if not labeled_hash or not template_path.is_file():
        return False
    actual_hash = hashlib.sha256(template_path.read_bytes()).hexdigest()
    return actual_hash != labeled_hash


def score_templates(content_path: Path, templates: List[Tuple[str, Path]]) -> List[Dict[str, Any]]:
    """Score every (name, template_dir) in `templates` against the content
    at `content_path`. Returns a list of
    `{"name", "score", "reasons": [str], "stale_labels": bool}` sorted by
    score descending, ties broken alphabetically by name (also the
    argmax tie rule `render auto` relies on).

    Raises ContentError if `content_path` can't be read or parsed, and
    ManifestError if a candidate template's manifest.json is invalid.
    """
    content_path = Path(content_path)
    raw = _load_raw_content(content_path)

    text_values: List[str] = []
    _flatten_strings(raw, text_values)
    blob = "\n".join(text_values)
    blob_casefold = blob.casefold()

    image_count = (
        len(MD_IMAGE_RE.findall(blob))
        + len(HTML_IMG_SRC_RE.findall(blob))
        + _sum_photo_arrays(raw)
    )
    date_count = _count_date_values(raw)
    tabular = _has_tabular_shape(raw)
    depth = _max_depth(raw)
    word_count = len(WORD_RE.findall(blob))

    kind_bonuses = _kind_bonuses(raw, image_count, date_count, tabular, depth, word_count)

    results: List[Dict[str, Any]] = []
    for name, template_dir in templates:
        template_dir = Path(template_dir)
        manifest = load_manifest(template_dir)
        suitability = manifest.get("suitability", {})
        content_kinds = suitability.get("content_kinds", [])
        keywords = suitability.get("keywords", [])

        score = 0.0
        reasons: List[str] = []

        for kind, bonus, reason in kind_bonuses:
            if kind in content_kinds:
                score += bonus
                reasons.append(reason)

        for kw in keywords:
            if kw and kw.casefold() in blob_casefold:
                score += KEYWORD_MATCH_BONUS
                reasons.append(f"keyword {kw!r} matched")

        stale = is_stale_labels(manifest, template_dir)
        if stale:
            score *= STALE_LABEL_PENALTY_FACTOR
            reasons.append("labels stale (template changed since labeling)")

        results.append({
            "name": name,
            "score": round(score, 4),
            "reasons": reasons,
            "stale_labels": stale,
        })

    results.sort(key=lambda r: (-r["score"], r["name"]))
    return results
