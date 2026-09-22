"""The trusted review-evidence verifier (kata jibot-code#q4av).

Why this runs here and not in `repoman-submit`: any token with push access can
`POST /repos/{owner}/{repo}/statuses/{sha}`, so a required review check that the
producer publishes is forgeable by exactly the thing it is meant to constrain —
an agent that skipped the wrapper, or an automated actor the wrapper refuses a
waiver to. The verifier reads kata itself, decides, and publishes the status
from the trusted domain.

It does **not** reimplement the `review:` grammar. That grammar lives in one
place (cell-fleet marshalcore/review.py:38-43) and a second copy of a security
grammar is how the two drift. This module shells to it and maps its exit codes:

    0 ok · 6 waived · 3 missing/stale · 4 malformed/unresolved · other = error

On top of that it enforces the one rule a local check cannot enforce: a waiver
from an automated actor. `repoman-submit:67-93` refuses `--no-review-gate` for
an actor ending `-unattended`, `-unattended-codex` or `-dispatcher`, but a
local refusal only binds a producer that chose to run the local code.
"""

import re
import subprocess

OK, WAIVED, MISSING, MALFORMED = 0, 6, 3, 4

# repoman-submit:85 — the suffix table, verbatim.
AUTOMATED_ACTOR_SUFFIXES = ("-unattended", "-unattended-codex", "-dispatcher")

STATUS_CONTEXT = "mujin/review-evidence"


def _run(cmd, stdin=None):
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True)


def is_automated_actor(actor):
    return bool(actor) and actor.endswith(AUTOMATED_ACTOR_SUFFIXES)


def waiver_author_ok(actor):
    """(ok, why) for the identity that posted a waiver.

    An UNKNOWN author is not an acceptable author. `actor=None` used to fall
    through `is_automated_actor` as false and let the waiver pass — a
    fail-OPEN on exactly the control that exists to stop an unattended worker
    waiving its own review. Missing provenance refuses.
    """
    if not actor:
        return False, ("the waiver's author could not be identified — "
                       "refusing rather than accepting an unattributed waiver")
    if is_automated_actor(actor):
        return False, "review waived by automated actor %s — refused" % actor
    return True, ""


def verdict(ref, project, head_sha, kata_bin, review_py, actor=None,
            runner=None):
    """(state, description) for the commit status on `head_sha`.

    state is 'success', 'failure' or 'error'. 'error' means the verifier could
    not reach a verdict, which is not a pass: the check stays red and the pull
    request does not enqueue. Fail closed, exactly as repoman-submit:232-235
    does when it cannot read kata.
    """
    run = runner or _run

    show = run([kata_bin, "show", ref, "--project", project, "--json"])
    if show.returncode != 0:
        return "error", "kata unreadable (rc %d) — failing closed" % show.returncode

    checked = run(["python3", review_py, "check-show-json", head_sha],
                  stdin=show.stdout)
    rc = checked.returncode
    detail = (checked.stdout or checked.stderr or "").strip().splitlines()
    detail = detail[0][:120] if detail else ""

    if rc == OK:
        return "success", "review evidence on %s: %s" % (head_sha[:8], detail or "ok")

    if rc == WAIVED:
        # The audited waiver is a human's decision. An unattended actor waiving
        # its own review is the case the local refusal exists for, and an
        # unattributable waiver is worse than an automated one.
        ok, why = waiver_author_ok(actor)
        if not ok:
            return "failure", why
        return "success", "review waived (audited) by %s on %s" % (
            actor, head_sha[:8])

    if rc == MISSING:
        return "failure", "no review evidence for %s (missing or stale)" % head_sha[:8]

    if rc == MALFORMED:
        return "failure", "review evidence for %s is malformed or unresolved" % head_sha[:8]

    return "error", "review check failed (rc %d) — failing closed" % rc


def group_verdict(members, verifier_logins):
    """(ok, reason) for a merge group.

    `members` is a list of dicts: {number, head_sha, statuses, attributed}.
    `statuses` is the list of commit statuses on that head, each a dict with
    `context`, `state` and `creator_login`.

    Three ways to fail, and the third is the one a merge queue makes possible
    and a pull request does not:

    1. a member has no successful `mujin/review-evidence` status;
    2. the status was created by somebody other than the verifier — a producer
       can post one, and a status whose creator is not the verifier is treated
       as absent;
    3. a commit in the group could not be attributed to a pull request at all.
       That is either a bug or an attack, and neither should merge.

    `statuses` must be ordered newest-first, as the statuses endpoint returns
    them: only the CURRENT status for the context counts.
    """
    if not members:
        return False, "merge group resolved to no pull requests — failing closed"

    for m in members:
        if not m.get("attributed", True):
            return False, ("a commit in the merge group is not attributable to "
                           "a pull request — failing closed")

        head = m.get("head_sha")
        if not head:
            return False, "pull request #%s has no head sha" % m.get("number")

        # The CURRENT status for the context, never "any success in the
        # history". The statuses endpoint returns newest first, so the first
        # match is the one in force; searching the whole list lets an older
        # success defeat a newer failure, which is the same as having no gate.
        current = None
        for st in (m.get("statuses") or []):
            if st.get("context") == STATUS_CONTEXT:
                current = st
                break
        if current is None:
            return False, ("pull request #%s has no %s status on %s"
                           % (m.get("number"), STATUS_CONTEXT, head[:8]))
        if current.get("state") != "success":
            return False, ("pull request #%s's current %s status is %s on %s"
                           % (m.get("number"), STATUS_CONTEXT,
                              current.get("state"), head[:8]))
        if current.get("creator_login") not in verifier_logins:
            return False, (
                "pull request #%s carries a %s status created by %s, not the "
                "verifier — treated as absent"
                % (m.get("number"), STATUS_CONTEXT,
                   current.get("creator_login")))

    return True, "every member reviewed at its own head"


# gh-readonly-queue/<base>/pr-<number>-<base sha>
_QUEUE_REF_RE = re.compile(
    r"^(?:refs/heads/)?gh-readonly-queue/(?P<base>.+)/pr-(?P<number>\d+)-(?P<sha>[0-9a-f]{7,40})$")


def parse_queue_ref(ref):
    """(number, base) for a single-PR merge-queue ref, or (None, None).

    With `max_entries_to_merge: 1` — where every repo starts — this is the whole
    of group membership. A group of more than one is resolved from the merge
    group's commits through the API instead, because the ref names only the
    last pull request in the group and prose in a commit message is not an
    identifier. A caller that gets (None, None) for a multi-PR group must fail
    closed rather than guess.
    """
    m = _QUEUE_REF_RE.match(ref or "")
    if not m:
        return None, None
    return int(m.group("number")), m.group("base")
