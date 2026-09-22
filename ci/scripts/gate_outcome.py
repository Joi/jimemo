"""What a finished gate run does to the kata issue (kata jibot-code#q4av).

The one rule this module exists to enforce: **`merge-blocked` is applied on
exactly one input — a gate run that completed with `outcome=failed`.** A gate
that could not run, a job a budget cancelled, a runner that was never there, a
pull request somebody removed from the queue by hand: none of those is a branch
defect, and jp90 (marshalcore/drain.py:1737-1743) forbids bouncing a producer
for any of them. Getting this wrong is silent — the producer is simply told
their branch is broken when it is not — so the decision is a pure function with
a test per row.

Comment text is byte-compatible with the parsers that already read it:
marshalboard/snapshot.py:734-741 (MARSHAL_COMMENT_PREFIXES) and :3044
(the `\\(attempt ([^)\\s]+)\\)` regex).
"""

# Plan kinds.
NOTHING = "nothing"
BOUNCE = "bounce"          # kata: merge-blocked + a bounced comment
COMMENT_ONLY = "comment"   # a superseded attempt: comment, but do not label
DEFER = "defer"            # ops alert + one re-enqueue; kata untouched
INFRA_ALERT = "infra"      # ops alert only; kata untouched

# Terminal conclusions that mean the INFRASTRUCTURE failed, whatever the
# artifact says. GitHub reports these on the run
# (docs.github.com/en/rest/actions/workflow-runs).
INFRA_CONCLUSIONS = ("cancelled", "timed_out", "stale", "action_required",
                     "startup_failure")

BLOCKED_LABEL = "merge-blocked"
DEFERRED_LABEL = "queue:deferred"

# drain.py:829-840 — the sentence the drain appends when a newer marker is
# already on the issue. Kept verbatim so a human reading either lane's bounce
# reads the same words.
SUPERSEDED_NOTE = (
    "(newer merge marker detected on this issue — handoff label left in place "
    "for it; this bounce applies only to the superseded attempt above)"
)


def _detail_block(text, limit=4096):
    """Gate output as comment detail: every line prefixed `| `, capped.

    The prefix is load-bearing, not cosmetic (drain.py:817-818): a test can
    print a line that matches the merge-marker grammar, and a `| ` in front of
    it survives the strip that the marker parser does, so the forged line never
    starts a line.
    """
    if not text:
        return ""
    # Cap FIRST, then prefix. Capping the joined text and slicing the tail can
    # cut into a line and leave a bare suffix with no `| ` in front of it — and
    # candidate output can put a valid `merge:` marker exactly at that
    # boundary, which would make this trusted comment marker-bearing. Every
    # line that survives is prefixed, without exception.
    lines = text.splitlines()
    kept, size = [], 0
    for ln in reversed(lines):           # keep the TAIL: the cause is last
        piece = "| " + ln
        if size + len(piece) + 1 > limit:
            break
        kept.append(piece)
        size += len(piece) + 1
    if not kept and lines:
        # A single line longer than the whole budget: truncate the LINE, then
        # prefix what is left, so the prefix is still there.
        kept = ["| " + lines[-1][-(limit - 2):]]
    return "\n".join(reversed(kept))


def bounce_comment(attempt, reason, detail=None):
    body = "repoman bounced (attempt %s): %s" % (attempt, reason)
    block = _detail_block(detail)
    if block:
        body = body + "\n\n" + block
    return body


STALE = "stale"             # a verdict the world has moved past


def is_authoritative(head_at_event, head_now, run_attempt, latest_run_attempt,
                     pr_state, pr_merged):
    """(bool, why). A verdict may arrive after the world has moved.

    A `workflow_run` event can be delivered late, and in the meantime the same
    run can have been re-run green, the pull request can have been updated, and
    it can already have landed. Acting on a stale failure means labelling a
    LANDED issue `merge-blocked`, which the bounce-fixer then acts on. So a
    verdict counts only when it is the latest attempt of that run, for the
    pull request's current head, and the pull request is still open.
    """
    if pr_merged:
        return False, "the pull request has already merged"
    if pr_state and pr_state != "open":
        return False, "the pull request is %s" % pr_state
    if head_at_event and head_now and head_at_event != head_now:
        return False, ("the head moved: verdict is for %s, head is now %s"
                       % (head_at_event[:8], head_now[:8]))
    if (run_attempt is not None and latest_run_attempt is not None
            and int(run_attempt) < int(latest_run_attempt)):
        return False, ("run attempt %s superseded by %s"
                       % (run_attempt, latest_run_attempt))
    return True, ""


def decide(run_conclusion, outcome, attempt,
           head_at_event=None, head_now=None,
           attempt_in_body=None, reason=None, detail=None, run_url=None,
           run_attempt=None, latest_run_attempt=None,
           pr_state="open", pr_merged=False, deferred_heads=()):
    """Return a plan dict for a finished (or absent) gate run.

    run_conclusion: 'success' | 'failure' | 'cancelled' | 'timed_out' | None
        None means no gate run exists for this head at all — both runners
        offline, or the workflow never dispatched.
    outcome: 'passed' | 'failed' | 'deferred' | None
        The wrapper's own verdict, read from the run's artifact. None means the
        run produced none, which is what a cancelled or timed-out job looks
        like — Actions reports those conclusions, so "no artifact" and "no run"
        stay distinguishable.
    attempt_in_body: the submission attempt the issue names NOW, when the
        caller can obtain it from a source it trusts. The workflow bridge
        passes None — the gate artifact carries no attempt, and the head
        comparison covers the same ground, since a re-submission on an
        unchanged head judges the same code. Kept because the drain makes this
        distinction (drain.py:820-840) and a future caller with a trusted
        source should be able to.
    deferred_heads: the head SHAs whose one automatic retry is already spent,
        read from the pull request's durable landing record. A label cannot
        carry this: the bridge removes the label to re-enqueue, so the pair
        would loop forever.
    """
    plan = {"kind": NOTHING, "labels_add": [], "labels_rm": [],
            "comment": None, "alert": None, "reenqueue": False,
            "rerun_gate": False, "record_deferred_head": None}

    # 1. A TERMINAL INFRASTRUCTURE CONCLUSION WINS OVER THE ARTIFACT.
    #    A job that was cancelled or timed out can still have uploaded an
    #    artifact before it died — the upload runs under `if: always()` — so
    #    "there is an outcome" does not mean "the gate reached a verdict".
    #    Trusting the artifact over the conclusion is how a runner restart
    #    becomes a producer bounce, which is precisely what jp90 forbids.
    if run_conclusion in INFRA_CONCLUSIONS:
        plan["kind"] = INFRA_ALERT
        plan["alert"] = (
            "repoman: gate run %s for attempt %s%s — infrastructure, not a "
            "branch defect; the producer was not bounced"
            % (run_conclusion, attempt, " " + run_url if run_url else "")
        )
        return plan

    # 2. No run, or a run that produced no verdict: both runners offline, or a
    #    step budget that killed the job before the gate could answer.
    if run_conclusion is None or outcome is None:
        plan["kind"] = INFRA_ALERT
        plan["alert"] = (
            "repoman: no gate verdict for attempt %s (run=%s outcome=%s)%s — "
            "infrastructure, not a branch defect; the producer was not bounced"
            % (attempt, run_conclusion, outcome,
               " " + run_url if run_url else "")
        )
        return plan

    if outcome == "passed":
        return plan

    # 3. AUTHORITY BEFORE ACTION — before a bounce AND before a retry. A
    #    `workflow_run` event can arrive after the world has moved, and a stale
    #    deferral that re-runs a gate or re-enqueues a merged pull request is
    #    as wrong as a stale failure that bounces one.
    ok, why = is_authoritative(head_at_event, head_now, run_attempt,
                               latest_run_attempt, pr_state, pr_merged)
    if not ok:
        plan["kind"] = STALE
        plan["alert"] = ("repoman: dropping a stale gate verdict (%s) for "
                         "attempt %s — %s" % (outcome, attempt, why))
        return plan

    if outcome == "deferred":
        # jp90: gate-cannot-run never bounces a producer. Nothing on the issue,
        # ever — the only question is whether the one automatic retry is left.
        plan["kind"] = DEFER
        if head_at_event and head_at_event in tuple(deferred_heads):
            plan["alert"] = (
                "repoman: gate deferred AGAIN for attempt %s on %s%s — the "
                "automatic retry for this head is spent; a human is needed"
                % (attempt, (head_at_event or "")[:8],
                   " " + run_url if run_url else "")
            )
            return plan
        plan["record_deferred_head"] = head_at_event
        # A PR-level deferred gate leaves a RED check, and enqueueing does not
        # make a red check green: the gate has to run again. A merge-group
        # defer only needs the pull request put back in the queue.
        plan["rerun_gate"] = True
        plan["reenqueue"] = True
        plan["labels_rm"] = [DEFERRED_LABEL]
        plan["alert"] = (
            "repoman: gate could not run for attempt %s — deferred%s; "
            "retrying once, kata untouched"
            % (attempt, " " + run_url if run_url else "")
        )
        return plan

    if outcome != "failed":
        plan["kind"] = INFRA_ALERT
        plan["alert"] = (
            "repoman: unknown gate outcome %r for attempt %s — treating as "
            "infrastructure; the producer was not bounced" % (outcome, attempt)
        )
        return plan

    # 4. A real failure, on a verdict already proven authoritative above.
    #    The stale-ATTEMPT guard is separate (drain.py:820-840): the head and the
    # run are current, but the pull request body names a newer submission, so a
    # comment is still owed while the label would blame the wrong work.
    body = bounce_comment(attempt, reason or "gate failed", detail)
    if attempt_in_body and attempt and attempt_in_body != attempt:
        plan["kind"] = COMMENT_ONLY
        plan["comment"] = body + "\n\n" + SUPERSEDED_NOTE
        return plan

    plan["kind"] = BOUNCE
    plan["labels_add"] = [BLOCKED_LABEL]
    plan["labels_rm"] = [DEFERRED_LABEL]
    plan["comment"] = body
    return plan
