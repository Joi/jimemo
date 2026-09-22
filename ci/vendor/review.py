"""Review-evidence grammar for repoman, formerly the merge marshal (kata jibot-code#39e3).

A handoff lands only if its kata issue carries evidence that an
INDEPENDENT-model review covered the exact commit being handed off. The
evidence is one line in a kata comment:

    review: <tool>[ <flag>] pass=<n> blocker=<n> substantive=<n> fixed=<n> rebutted=<n> oid=<40-hex>
    review: waived oid=<40-hex>

The grammar lives here and nowhere else: marshal-submit (bash) calls this
file as a script (`python3 review.py check-show-json <oid>` /
`validate-body <oid>`), the drain imports it. Keep it importable as a plain
file -- no package-relative imports.

Out of scope on purpose (issue text): whether the review really happened.
The gate checks presence, commit binding and arithmetic; honesty stays on
the session that posted the line.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass

WAIVED_LABEL = "review-waived"
README_REF = "ops/repoman/README.md § Review evidence"
GRAMMAR = ("review: <tool>[ --<flag>] pass=N blocker=N substantive=N "
           "fixed=N rebutted=N oid=<40-hex>")

# Strict, line-anchored, no trailing text -- same discipline as MARKER_RE.
# Counts are ASCII digits only ([0-9], never \d: Python's \d also matches
# Unicode decimal digits, which int() would accept but the README grammar
# does not promise) and BOUNDED to 6 digits: producer-controlled text
# reaches int() here, and CPython >= 3.11 raises ValueError past its
# int_max_str_digits limit (4300) -- an unbounded `[0-9]+` would let one
# persistent comment crash every drain cycle. README documents the bound.
REVIEW_RE = re.compile(
    r"review: ([a-z0-9._-]{1,32})(?: (--[a-z0-9-]{1,32}))? pass=([0-9]{1,6}) blocker=([0-9]{1,6})"
    r" substantive=([0-9]{1,6}) fixed=([0-9]{1,6}) rebutted=([0-9]{1,6}) oid=([0-9a-f]{40})\Z"
)
WAIVER_RE = re.compile(r"review: waived oid=([0-9a-f]{40})\Z")
WAIVED_TOOL = "waived"

VERDICTS = ("ok", "waived", "unresolved", "malformed", "stale", "missing")


@dataclass(frozen=True)
class Review:
    tool: str
    flag: str | None
    passes: int
    blocker: int
    substantive: int
    fixed: int
    rebutted: int
    oid: str
    waived: bool = False


def _is_review_line(line: str) -> bool:
    return line.strip().startswith("review:")


def parse_review_line(line: str) -> Review | None:
    """Parse one stripped line. The waiver form is tried first; the tool
    name `waived` is reserved for it and rejected in the normal form so a
    numeric line cannot masquerade as a waiver (or vice versa)."""
    s = line.strip()
    w = WAIVER_RE.match(s)
    if w:
        return Review(WAIVED_TOOL, None, 0, 0, 0, 0, 0, w.group(1), waived=True)
    m = REVIEW_RE.match(s)
    if not m:
        return None
    tool, flag, p, b, sub, f, r, oid = m.groups()
    if tool == WAIVED_TOOL:
        return None
    return Review(tool, flag, int(p), int(b), int(sub), int(f), int(r), oid)


def review_line_of(body: str) -> tuple[str | None, bool]:
    """The one review-bearing line of a comment body.

    Returns (line, malformed). Exactly one `review:` line is allowed per
    comment; a second one makes the WHOLE comment malformed (the caller
    must then discard the first line too, even if it parses -- otherwise a
    valid first line could smuggle a malformed sibling past the gate).
    """
    lines = [ln for ln in body.splitlines() if _is_review_line(ln)]
    if not lines:
        return None, False
    return lines[0].strip(), len(lines) > 1


def is_resolved(r: Review, body: str) -> tuple[bool, str]:
    """The CLAUDE.md rule made arithmetic: every BLOCKER/SUBSTANTIVE finding
    of the final pass is fixed or explicitly rebutted. A rebuttal must be
    written down -- rebutted>0 requires prose in the same comment."""
    if r.waived:
        return True, ""
    if r.passes < 1:
        return False, "pass must be >= 1"
    needed = r.blocker + r.substantive
    if r.fixed + r.rebutted < needed:
        return False, (f"fixed={r.fixed} + rebutted={r.rebutted} < blocker={r.blocker}"
                       f" + substantive={r.substantive}: every BLOCKER/SUBSTANTIVE"
                       " finding must be fixed or rebutted")
    if r.rebutted > 0:
        prose = [ln for ln in body.splitlines()
                 if ln.strip() and not _is_review_line(ln)]
        if not prose:
            return False, (f"rebutted={r.rebutted} but the comment carries no rebuttal"
                           " text: write the rebuttal in the same comment, under"
                           " the review line")
    return True, ""


def check_evidence(comments: list[dict], oid: str) -> tuple[str, str]:
    """Verdict for a handoff at `oid` given the issue's comments.

    Order-independent and deterministic (no "newest wins": oid binding
    replaces it). Returns (verdict, detail) with verdict in VERDICTS:
      waived      a waiver line for this oid
      ok          a parsed, resolved line for this oid
      unresolved  lines for this oid exist but none passes is_resolved
      malformed   no line for this oid; some review line does not parse
                  (or a comment carries two review lines)
      stale       parsed lines exist only for other oids
      missing     no review line anywhere
    """
    oid = (oid or "").strip()
    matching: list[tuple[Review, str]] = []
    malformed: list[str] = []
    other_oids: set[str] = set()
    for c in comments or []:
        if not isinstance(c, dict):
            continue          # a non-dict entry is noise, never a crash
        body = c.get("body", "") or ""
        if not isinstance(body, str):
            continue
        line, bad = review_line_of(body)
        if line is None:
            continue
        if bad:
            malformed.append(f"{line[:60]} (+ a second review: line in the same comment)")
            continue
        r = parse_review_line(line)
        if r is None:
            malformed.append(line[:80])
            continue
        if r.oid == oid:
            matching.append((r, body))
        else:
            other_oids.add(r.oid)
    for r, body in matching:
        if r.waived:
            return "waived", f"review waived for {oid[:8]}"
    unresolved_why: list[str] = []
    for r, body in matching:
        ok, why = is_resolved(r, body)
        if ok:
            return "ok", (f"{r.tool}{' ' + r.flag if r.flag else ''} pass={r.passes}"
                          f" blocker={r.blocker} substantive={r.substantive}"
                          f" fixed={r.fixed} rebutted={r.rebutted} oid={oid[:8]}")
        unresolved_why.append(why)
    if matching:
        return "unresolved", (f"review evidence for {oid[:8]} is unresolved: "
                              + "; ".join(unresolved_why) + f" (see {README_REF})")
    if malformed:
        return "malformed", (f"review line does not match the grammar `{GRAMMAR}`: "
                             + " | ".join(malformed) + f" (see {README_REF})")
    if other_oids:
        seen = ", ".join(sorted(o[:8] for o in other_oids))
        return "stale", (f"review evidence is for oid {seen}, handoff is {oid[:8]}:"
                         " post fresh evidence for the new head"
                         f" (see {README_REF})")
    return "missing", (f"no review evidence comment for {oid[:8]}: post"
                       f" `{GRAMMAR}` on the issue (see {README_REF})")


def labels_of(payload: dict) -> list[str]:
    """Label names from a `kata show --json` payload: dicts with "label"
    (the real daemon), plain strings (older shapes), null/missing → []."""
    out: list[str] = []
    labels = (payload or {}).get("labels") if isinstance(payload, dict) else None
    if not isinstance(labels, list):
        return out          # null, missing, or a scalar: never a crash
    for x in labels:
        if isinstance(x, dict):
            name = x.get("label") or x.get("name")
            if name:
                out.append(str(name))
        elif isinstance(x, str) and x:
            out.append(x)
    return out


# ------------------------------------------------------------------ CLI
# Exit codes (marshal-submit depends on them):
#   0  ok
#   2  usage
#   3  missing / stale          (producer: post evidence for this head)
#   4  malformed / unresolved   (producer: fix the line / the arithmetic)
#   5  stdin is not the JSON expected
#   6  waived                   (distinct so marshal-submit can refuse a
#                                waiver smuggled through --review and can
#                                audit-label a pre-posted one)
EXIT_BY_VERDICT = {"ok": 0, "waived": 6, "missing": 3, "stale": 3,
                   "malformed": 4, "unresolved": 4}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("check-show-json", "validate-body"):
        print("usage: review.py check-show-json <oid>   (stdin: kata show --json)\n"
              "       review.py validate-body <oid>     (stdin: one comment body)",
              file=sys.stderr)
        return 2
    cmd, oid = argv
    if not re.fullmatch(r"[0-9a-f]{40}", oid):
        print(f"review.py: oid must be 40 hex chars, got {oid!r}", file=sys.stderr)
        return 2
    data = sys.stdin.read()
    if cmd == "check-show-json":
        try:
            payload = json.loads(data or "{}")
            if not isinstance(payload, dict):
                raise ValueError("top level is not an object")
            comments = payload.get("comments")
            if comments is not None and not isinstance(comments, list):
                raise ValueError("comments is not a list")      # falsy wrong types too
            comments = comments or []
        except (ValueError, AttributeError) as e:
            print(f"review.py: stdin is not `kata show --json` output: {e}", file=sys.stderr)
            return 5
        verdict, detail = check_evidence(comments, oid)
    else:
        verdict, detail = check_evidence([{"body": data}], oid)
    print(detail)
    return EXIT_BY_VERDICT[verdict]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
