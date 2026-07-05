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
from .errors import ContentError, ManifestError
from .manifest import load_manifest

add_vendor_to_path()
import yaml  # noqa: E402

# --- Weights -------------------------------------------------------------
# Points added to a template's score when a content signal fires and the
# template's suitability.content_kinds declares the matching kind. All
# five content_kind signals are independent and may co-fire (a dated photo
# timeline is both photo-heavy and chronological; a briefing memo with a
# stats sidecar is both narrative and, weakly, tabular-data) -- ranking is
# decided by which template's declared kinds line up with the strongest
# combination of bonuses, not by any one signal suppressing another.
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
#   NARRATIVE_MIN_WORDS /         | prose (the markdown `body` slot, plus   | narrative
#     NARRATIVE_WORDS_PER_RECORD/ |   any nested `body` fields inside       |
#     _BONUS                      |   data-slot list items) is substantial  |
#                                  |   (>= threshold words) AND dominates    |
#                                  |   the content -- either there are no    |
#                                  |   structured records at all, or there   |
#                                  |   are few enough words-per-record that  |
#                                  |   the records read as a small sidecar   |
#                                  |   rather than the point of the document |
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
NARRATIVE_WORDS_PER_RECORD_THRESHOLD = 30
NARRATIVE_BONUS = 2.0

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


def _top_level_record_counts(raw: Dict[str, Any]) -> List[int]:
    """Length of every top-level slot value that is a list of objects
    (records) -- e.g. a `stats` or `rows` slot. Feeds both the
    tabular-data signal (any list >= TABULAR_MIN_RECORDS) and the
    narrative prose-dominance signal (which needs a record *count* to
    weigh prose against), so both share one walk of the content."""
    return [
        len(value)
        for value in raw.values()
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value)
    ]


def _prose_word_count(value: Any) -> int:
    """Word count of this content's actual prose: the markdown `body`
    slot (the free-standing article text of an .md content file, per
    `_load_raw_content`) plus any nested `body` field found inside a
    data-slot item (e.g. a briefing's `sections[].body`, a timeline's
    `events[].body`). Deliberately narrower than "every string in the
    content" -- short structural strings (labels, headings, table cells,
    a one-paragraph `intro`) are not prose for this signal's purposes;
    otherwise every content file with a sentence of description would
    look narrative."""
    total = 0
    if isinstance(value, dict):
        body = value.get("body")
        if isinstance(body, str):
            total += len(WORD_RE.findall(body))
        for key, v in value.items():
            if key == "body":
                continue
            total += _prose_word_count(v)
    elif isinstance(value, list):
        for item in value:
            total += _prose_word_count(item)
    return total


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
    prose_words: int,
    record_count: int,
) -> List[Tuple[str, float, str]]:
    """Content_kind -> (bonus, reason) for every kind this content matches.
    All five signals are independent and can co-fire -- e.g. a dated photo
    timeline is legitimately both photo-heavy and chronological, and a
    briefing memo with a small stats sidecar is legitimately both
    narrative and (weakly) tabular-data. `narrative` fires whenever prose
    dominates the content: there's a substantial amount of it (>=
    NARRATIVE_MIN_WORDS), and either there are no structured records to
    compete with it, or the records present are sparse relative to how
    much prose accompanies them (a small sidecar, not the point of the
    document) -- see NARRATIVE_WORDS_PER_RECORD_THRESHOLD."""
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
    prose_dominant = prose_words >= NARRATIVE_MIN_WORDS and (
        record_count == 0
        or prose_words >= record_count * NARRATIVE_WORDS_PER_RECORD_THRESHOLD
    )
    if prose_dominant:
        bonuses.append(
            ("narrative", NARRATIVE_BONUS,
             f"prose-dominant ({prose_words} words, {record_count} records) -> narrative")
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


def score_templates(
    content_path: Path, templates: List[Tuple[str, Path]]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Score every (name, template_dir) in `templates` against the content
    at `content_path`. Returns `(results, warnings)`:

    - `results`: a list of `{"name", "score", "reasons": [str],
      "stale_labels": bool}` sorted by score descending, ties broken
      alphabetically by name (also the argmax tie rule `render auto`
      relies on).
    - `warnings`: one `"skipping template '<name>': <error>"` string for
      each candidate whose manifest.json failed to load. That candidate
      is excluded from `results` rather than aborting the whole ranking
      -- a personal template library with one broken manifest shouldn't
      take `suggest`/`render auto` down for every other template (the
      same scan-and-warn precedent as `doctor`'s stale-label scan).
      `render <explicit-name>` is unaffected and stays fail-closed.

    Raises ContentError if `content_path` can't be read or parsed.
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
    record_counts = _top_level_record_counts(raw)
    tabular = any(n >= TABULAR_MIN_RECORDS for n in record_counts)
    depth = _max_depth(raw)
    prose_words = _prose_word_count(raw)
    record_count = sum(record_counts)

    kind_bonuses = _kind_bonuses(
        raw, image_count, date_count, tabular, depth, prose_words, record_count
    )

    results: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for name, template_dir in templates:
        template_dir = Path(template_dir)
        try:
            manifest = load_manifest(template_dir)
        except ManifestError as e:
            warnings.append(f"skipping template '{name}': {e}")
            continue
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
    return results, warnings
