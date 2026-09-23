"""The pull-request body grammar for the PR landing lane (kata jibot-code#q4av).

`repoman-submit` writes this body; every bridge workflow reads it. It carries
the facts the kata ledger needs and GitHub does not keep: which issue to close,
which attempt this is, and where the producer's worktree lives (the last one is
what `producer-triage` compares against before it retires a worktree —
marshalboard/triage.py:732-764).

Grammar: one `key: value` per line, anywhere in the body, unknown lines ignored
so a human can write prose around it. Every value is validated against the same
charset the existing marshal grammars use, because a body is producer-supplied
text that reaches a `kata` argv:

  kata:        <project>#<short_id>
  repo:        <slug>              marshalcore/registry.py SLUG_RE
  branch:      <name>              marshalcore/marker.py BRANCH_RE
  attempt:     <YYYYMMDDTHHMMSSZ>.<6hex>   marshalcore/marker.py MARKER_RE
  host:        <hostname>
  worktree:    <absolute path>
  dispatch_id: <id> or -
  close:       true | false        (default true; `-` and absent mean true)

A body with no `kata:` line is not an error. It is a pull request that closes
nothing, which is what `repoman-submit -t` produces before its issue exists and
what a hand-opened pull request looks like.

A `kata:` project is honoured only when the repository declares it in the
repository variable KATA_PROJECTS (`project_declared`, kata jibot-code#3vb4).

Run: python3 -m unittest discover -s ci/scripts -t ci/scripts
"""

import re

# Mirrors of the grammars in cell-fleet's marshalcore. They are duplicated here
# rather than imported because jibot-ops does not have marshalcore on its path,
# and they are pinned by tests that quote the originals with their line numbers.
SLUG_RE = re.compile(r"[a-z0-9._-]{1,64}\Z")
BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}\Z")
ATTEMPT_RE = re.compile(r"\d{8}T\d{6}Z\.[0-9a-f]{6}\Z")
REF_RE = re.compile(r"[a-z0-9]{1,32}\Z")
PROJECT_RE = re.compile(r"[a-z0-9._-]{1,64}\Z")
HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,127}\Z")
DISPATCH_RE = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")

KEYS = ("kata", "repo", "branch", "attempt", "host", "worktree",
        "dispatch_id", "close")

# A key line is `key: value` at the start of a line, leading spaces allowed so
# the body can sit inside a markdown list. The value runs to end of line and is
# stripped; an empty value is a malformed line, not an absent key.
_LINE_RE = re.compile(r"^[ \t]*(%s)[ \t]*:[ \t]*(.*)$" % "|".join(KEYS))


def _err(msg):
    return None, msg


def parse_body(text):
    """(meta, error). Exactly one of the two is None.

    `meta` is a dict with the keys above; `close` is a bool; absent optional
    keys are absent from the dict rather than None, so a caller that needs one
    fails loudly on KeyError instead of writing "None" into a kata comment.
    """
    if not isinstance(text, str):
        return _err("body is not text")

    seen = {}
    for raw in text.splitlines():
        m = _LINE_RE.match(raw)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key in seen:
            # Two `kata:` lines is the shape a body-forging attempt takes, and
            # review.py:82-93 makes the same call for a second `review:` line:
            # ambiguity is malformed, never "the first one wins".
            return _err("duplicate key: %s" % key)
        seen[key] = value

    meta = {}

    if "kata" in seen:
        v = seen["kata"]
        if "#" not in v:
            return _err("kata: expected <project>#<short_id>, got %r" % v)
        project, _, ref = v.partition("#")
        if not PROJECT_RE.match(project):
            return _err("kata: bad project %r" % project)
        if not REF_RE.match(ref):
            return _err("kata: bad issue ref %r" % ref)
        meta["project"] = project
        meta["ref"] = ref

    if "repo" in seen:
        v = seen["repo"]
        if not SLUG_RE.match(v) or not re.search(r"[a-z0-9]", v):
            return _err("repo: bad slug %r" % v)
        meta["repo"] = v

    if "branch" in seen:
        v = seen["branch"]
        if not BRANCH_RE.match(v) or ".." in v or v.endswith("/"):
            return _err("branch: bad branch %r" % v)
        meta["branch"] = v

    if "attempt" in seen:
        v = seen["attempt"]
        if not ATTEMPT_RE.match(v):
            return _err("attempt: bad attempt id %r" % v)
        meta["attempt"] = v

    if "host" in seen:
        v = seen["host"]
        if not HOST_RE.match(v):
            return _err("host: bad hostname %r" % v)
        meta["host"] = v

    if "worktree" in seen:
        v = seen["worktree"]
        if not v.startswith("/") or "\n" in v or len(v) > 4096:
            return _err("worktree: expected an absolute path, got %r" % v)
        meta["worktree"] = v

    if "dispatch_id" in seen:
        v = seen["dispatch_id"]
        if v not in ("", "-"):
            if not DISPATCH_RE.match(v):
                return _err("dispatch_id: bad id %r" % v)
            meta["dispatch_id"] = v

    if "close" in seen:
        v = seen["close"]
        if v in ("", "-", "true"):
            meta["close"] = True
        elif v == "false":
            meta["close"] = False
        else:
            return _err("close: expected true or false, got %r" % v)
    else:
        meta["close"] = True

    # A body that names an issue must also name everything the close needs, or
    # the close job would have to invent an idempotency key — and an invented
    # key is a duplicate close waiting for the first retry.
    if "ref" in meta:
        for need in ("repo", "branch", "attempt"):
            if need not in meta:
                return _err("body names a kata issue but has no %s: line" % need)

    return meta, None


def closes_an_issue(meta):
    """True when this pull request's landing should touch a kata issue."""
    return "ref" in meta


# The body is producer-supplied text, and the bridge that reads it holds a kata
# token that can close an issue in ANY project. So the `kata:` project a body
# names is only honoured when the repository declares it: the repository
# variable KATA_PROJECTS, a comma-separated list of project names (kata
# jibot-code#3vb4). More than one, because a repository can file its handoffs
# in a project other than the one its kata alias points at. Unset means the
# bridge touches nothing on that repository.
def declared_projects(value):
    """(frozenset, error) — the kata projects a repository serves.

    Unset or blank is the empty set, not an error, so the caller's refusal can
    say which it was. An empty entry or a bad name refuses the whole value.
    """
    if value is None or not value.strip():
        return frozenset(), None
    out = set()
    for entry in value.split(","):
        name = entry.strip()
        if not PROJECT_RE.match(name):
            return None, "bad project name %r" % name
        out.add(name)
    return frozenset(out), None


def project_declared(meta, value):
    """(ok, why): may this repository's bridge touch `meta["project"]`?"""
    projects, err = declared_projects(value)
    if err:
        return False, "KATA_PROJECTS is malformed (%s)" % err
    if not projects:
        return False, "KATA_PROJECTS is not set on this repository"
    if meta.get("project") not in projects:
        return False, ("kata project %r is not in KATA_PROJECTS (%s)"
                       % (meta.get("project"), ",".join(sorted(projects))))
    return True, ""
