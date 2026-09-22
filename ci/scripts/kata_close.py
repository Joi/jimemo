"""Closing the kata issue when a pull request lands (kata jibot-code#q4av).

Reproduces `drain._close_landed` (cell-fleet marshalcore/drain.py:1029-1193)
field for field, because the board, the land report and the bounce-fixer parse
what it writes. The three details that are easy to get wrong, and which have a
test each:

  * the evidence flag is `--evidence commit:<sha>`, not `--commit <sha>`
    (kataops.py:230-258);
  * the idempotency key is `repoman-<slug>-<ref>-<attempt>-<landed sha>`
    (drain.py:1075) — derived only from immutable facts, so a replayed webhook,
    a reconciler run and a manual re-run all send the SAME request and the
    daemon returns the original close;
  * the WHOLE REQUEST is persisted before it is sent, and a retry replays it
    verbatim. kata fingerprints the idempotency key TOGETHER WITH the request,
    so re-rendering the message or speculatively re-reading the revision can
    return `idempotency_mismatch` instead of the original close. Only a
    definite revision conflict (exit 6) justifies a new revision, and the new
    one is persisted before the retry — the same rule the drain follows by
    journalling `close_rev` (drain.py:1123).

And the guard the first design draft did not have: a pull request merged into
some other branch must not close an issue or publish a `landed` record. That
record is authoritative input to worktree cleanup, so a false one retires a
worktree that still holds work.
"""

import time

REVISION_CONFLICT_EXIT = 6


def should_close(pr, merge_sha, main_branch, expected_repo, repo_now):
    """(bool, why). Four conditions, not one.

    `pr` is the pull request resolved from the merge commit
    (GET /repos/{o}/{r}/commits/{sha}/pulls), not an event payload: the close
    job is triggered by a push to main, so that the workflow file it runs is
    main's and not the pull request's.

    The guard the first design draft did not have is the base-branch one. A
    pull request merged into a feature branch must not close an issue or
    publish a `landed` record — that record is authoritative input to worktree
    cleanup, so a false one retires a worktree that still holds work.
    """
    if not pr:
        return False, "no pull request is associated with %s" % merge_sha[:8]
    if not pr.get("merged_at"):
        return False, "the associated pull request is not merged"
    base_ref = ((pr.get("base") or {}).get("ref"))
    if base_ref != main_branch:
        return False, ("merged into %r, not %r — not a landing"
                       % (base_ref, main_branch))
    if expected_repo and repo_now != expected_repo:
        return False, ("repository %r is not %r" % (repo_now, expected_repo))
    if not merge_sha:
        return False, "no merge commit sha"
    return True, ""


def fmt_took(seconds):
    """`drain._fmt_took` shapes: 45s, 4m12s, 1h5m. Empty when unknown."""
    if seconds is None or seconds < 0:
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return " took %ds" % seconds
    if seconds < 3600:
        return " took %dm%02ds" % (seconds // 60, seconds % 60)
    return " took %dh%dm" % (seconds // 3600, (seconds % 3600) // 60)


def idempotency_key(meta, merge_sha):
    return "repoman-%s-%s-%s-%s" % (
        meta["repo"], meta["ref"], meta["attempt"], merge_sha)


def close_message(meta, took_seconds=None):
    return "Landed on main by repoman (attempt %s%s)." % (
        meta["attempt"], fmt_took(took_seconds))


def close_argv(meta, merge_sha, revision, took_seconds=None):
    """The `kata close` argv for a landed pull request."""
    return [
        "close", meta["ref"], "--done",
        "--message", close_message(meta, took_seconds),
        "--evidence", "commit:%s" % merge_sha,
        "--idempotency-key", idempotency_key(meta, merge_sha),
        "--if-match", str(revision),
    ]


def no_close_comment_argv(meta, merge_sha, took_seconds=None):
    """`--no-close`: comment instead of closing (drain.py:1457-1458)."""
    body = ("repoman landed %s (attempt %s%s); issue left open per close=false"
            % (merge_sha, meta["attempt"], fmt_took(took_seconds)))
    return ["comment", meta["ref"], "--body", body]


def waived_alert(meta, merge_sha, evidence_description):
    """The post-landing waiver audit (drain.py:1859-1877), re-homed.

    The drain emits this from the attempt's journalled `review_waived` value.
    The journal is being deleted, so the close job reads the verifier's status
    description for the landed head instead. Returns None when the landing
    carried real review evidence.
    """
    if not evidence_description:
        return None
    if "waived" not in evidence_description.lower():
        return None
    return ("repoman: %s review-waived — landed %s without review evidence"
            % (meta["ref"], merge_sha[:8]))


def landed_payload(meta, merge_sha, at_epoch=None):
    """The dk21 record (drain.py:1343-1355).

    Every key here is load-bearing: `producer-triage` accepts the record only
    when `host` matches the local host, `worktree` matches the subject
    worktree, `branch` matches, and `base_sha` is in the local object store
    with HEAD an ancestor of it (marshalboard/triage.py:732-764). `base_sha` is
    the pull request head — the pre-rebase oid's equivalent, and the only sha
    the producer's worktree can be compared against.
    """
    if at_epoch is None:
        at_epoch = time.time()
    return {
        "branch": meta["branch"],
        "repo": meta["repo"],
        "landed_sha": merge_sha,
        "base_sha": meta["head_sha"],
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at_epoch)),
        "attempt": meta["attempt"],
        "host": meta.get("host"),
        "worktree": meta.get("worktree"),
        "dispatch_id": meta.get("dispatch_id"),
    }


def landing_meta_key(repo, pr_number):
    """One record PER LANDING, not per issue — and landings are per REPO.

    A single `landing` key under the issue would be overwritten by a second
    pull request for the same issue, which `--no-close` and multi-repo work
    both produce; and `landing:12` alone collides between two repositories'
    pull request #12 on one issue, which is exactly the multi-repo case this
    design exists to serve. `kataops.meta_set` is last-write-wins, so the key
    carries both.
    """
    return "landing:%s:%d" % (repo, int(pr_number))


def landing_record(meta, pr_number, merge_sha, revision, took_seconds=None,
                   started_at_epoch=None, closed=False, landed_record=False,
                   waiver_alerted=False, deferred_heads=None):
    """The completion record the reconciler works from.

    It holds the WHOLE REQUEST, not just an idempotency key. kata fingerprints
    the key together with the request, so replaying the same key with a re-read
    revision and a re-rendered message can come back `idempotency_mismatch`
    instead of returning the original close. The drain solves this by
    journalling `close_rev` (drain.py:1123) and replaying verbatim; this is the
    same solution in a different store.
    """
    # The OPERATION is persisted, not just its arguments: `--no-close` lands
    # with a comment rather than a close, and a record that always says "close"
    # cannot replay one. `started_at_epoch` is the name the reconciler reads —
    # writing `started_at` instead meant every constructed record got no
    # in-flight grace and the reconciler could race the close job.
    close_it = meta.get("close", True)
    rec = {
        "pr": int(pr_number),
        "repo": meta["repo"],
        "ref": meta["ref"],
        "project": meta.get("project"),
        "attempt": meta["attempt"],
        "merge_sha": merge_sha,
        "head_sha": meta.get("head_sha"),
        "operation": "close" if close_it else "comment",
        "revision": revision,
        "started_at_epoch": started_at_epoch,
        "closed": bool(closed),
        "landed_record": bool(landed_record),
        "waiver_alerted": bool(waiver_alerted),
        "deferred_heads": list(deferred_heads or []),
    }
    if close_it:
        rec["message"] = close_message(meta, took_seconds)
        rec["evidence"] = "commit:%s" % merge_sha
        rec["idempotency_key"] = idempotency_key(meta, merge_sha)
    else:
        rec["argv"] = no_close_comment_argv(meta, merge_sha, took_seconds)
    return rec


def replay_argv(record):
    """The argv for a retry: the PERSISTED request, verbatim.

    Never re-render the message and never re-read the revision speculatively —
    only a definite revision conflict justifies changing it, and then the new
    revision is persisted before the retry (see `with_new_revision`).

    A `--no-close` landing replays its comment, not a close: the operation is
    part of the request.
    """
    if record.get("operation") == "comment":
        return list(record["argv"])
    return [
        "close", record["ref"], "--done",
        "--message", record["message"],
        "--evidence", record["evidence"],
        "--idempotency-key", record["idempotency_key"],
        "--if-match", str(record["revision"]),
    ]


def with_new_revision(record, revision):
    """After a DEFINITE revision conflict (kata exit 6), and only then."""
    out = dict(record)
    out["revision"] = revision
    return out


def unfinished(record):
    """Which sub-operations of a landing still have to be retried."""
    if not record:
        return ["closed", "landed_record", "waiver_alerted"]
    return [k for k in ("closed", "landed_record", "waiver_alerted")
            if not record.get(k)]


def reconcile_targets(records, now_epoch, in_flight_grace=3600):
    """Which landing records the reconciler should touch.

    Skips one whose `started_at` is younger than the grace period: the close
    job may still be running, and `meta_set` is last-write-wins, so two writers
    on one key lose progress. The grace is longer than any close job can take
    and shorter than the reconciler's own cadence.
    """
    out = []
    for rec in records or []:
        if not unfinished(rec):
            continue
        started = rec.get("started_at_epoch")
        if started is not None and (now_epoch - started) < in_flight_grace:
            continue
        out.append(rec)
    return out


def advance_watermark(watermark, records):
    """The watermark moves only past landings that are COMPLETE.

    Ties on the merge timestamp are broken by pull-request number, so the
    watermark is a (timestamp, pr) pair and never loses a landing that merged
    in the same second as another.
    """
    best = watermark
    for rec in sorted(records or [],
                      key=lambda r: (r.get("merged_at_epoch", 0), r.get("pr", 0))):
        if unfinished(rec):
            break
        best = (rec.get("merged_at_epoch", 0), rec.get("pr", 0))
    return best
