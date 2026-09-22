"""Tell kata what GitHub did (kata jibot-code#q4av).

Two entry points, both on the bridge runner, both from main's copy of the
workflow, neither executing anything a pull request wrote:

  landed    a push to the main branch. Every pull request the push merged gets
            its kata issue closed with the landed commit as evidence — or, for
            `close: false`, a comment saying it landed and the issue stays open.
  outcome   a finished `gate` run (workflow_run, completed). A gate that FAILED
            bounces the producer: a comment naming the failed check, then the
            `merge-blocked` label. Every other non-success — cancelled, timed
            out, deferred, no verdict — is infrastructure: it is reported on
            the job and kata is not touched (jp90).

The decisions are not made here. `ci/scripts/kata_close.py` and
`ci/scripts/gate_outcome.py` are the tested decision tables; this module does
the reading and the writing around them.

  dequeued  a pull request left the merge queue (pull_request_target,
            dequeued). The producer session is usually gone by then, so kata is
            where it has to be said. A conflict with main is the producer's to
            fix and bounces. A CI failure is NOT bounced from here: it may be a
            gate that could not run, which jp90 forbids bouncing, and a gate
            that failed at the pull-request level is bounced by `outcome` with
            the evidence. It gets a plain comment, so that a removal caused by the REVIEW check
            failing in the merge group is not silent — which it was on the
            canary's first pull request.

NOT built yet, and reported rather than faked when they come up: the one
automatic retry of a deferred gate (needs Actions: write), the landing record
and reconciler, and the waived-landing alert.
"""

import io
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import gate_outcome  # noqa: E402
import kata_close  # noqa: E402
import pr_meta  # noqa: E402
import review_check  # noqa: E402
import review_evidence as gh  # noqa: E402  (its GitHub read helpers)

API = gh.API
BridgeError = gh.BridgeError
ZERO_SHA = "0" * 40


# --- kata
def kata(argv, project, kata_bin, runner=None):
    """(rc, stdout). Never raises on a non-zero exit; the caller decides."""
    cmd = [kata_bin] + list(argv) + ["--project", project, "--agent"]
    done = (runner or _run)(cmd)
    return done.returncode, (done.stdout or "")


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def kata_revision(ref, project, kata_bin, runner=None):
    """The issue's revision, through the real JSON contract."""
    done = (runner or _run)([kata_bin, "show", ref, "--project", project, "--json"])
    if done.returncode != 0:
        raise BridgeError("kata show %s failed (rc %d)" % (ref, done.returncode))
    try:
        doc = json.loads(done.stdout)
        rev = doc["issue"]["revision"]
    except (ValueError, KeyError, TypeError):
        raise BridgeError("kata show %s: no issue.revision in the reply" % ref)
    if not isinstance(rev, int):
        raise BridgeError("kata show %s: revision is not an integer" % ref)
    return rev


# --- landed
def merged_pulls(repo, before, after, token, opener=None):
    """The pull requests a push to main merged, oldest first, deduplicated."""
    if before == ZERO_SHA:
        commits = [{"sha": after}]
    else:
        commits = gh._pages("%s/repos/%s/compare/%s...%s" % (API, repo, before, after),
                            token, key="commits", opener=opener)
    out, seen = [], set()
    for c in commits:
        pulls = gh._pages("%s/repos/%s/commits/%s/pulls" % (API, repo, c["sha"]),
                          token, opener=opener)
        for p in pulls:
            if p.get("merged_at") and p["number"] not in seen:
                seen.add(p["number"])
                out.append(p)
    return out


def land_one(pr, repo, main_branch, kata_bin, runner=None):
    """(status, detail) for one merged pull request."""
    merge_sha = pr.get("merge_commit_sha") or ""
    ok, why = kata_close.should_close(pr, merge_sha, main_branch, repo, repo)
    if not ok:
        return "skipped", why
    meta, err = pr_meta.parse_body(pr.get("body") or "")
    if err:
        return "skipped", "pull request #%d body is malformed: %s" % (pr["number"], err)
    if "ref" not in meta:
        return "skipped", "pull request #%d names no kata issue" % pr["number"]
    meta["head_sha"] = (pr.get("head") or {}).get("sha")

    if not meta["close"]:
        rc, _ = kata(kata_close.no_close_comment_argv(meta, merge_sha),
                     meta["project"], kata_bin, runner)
        return ("commented" if rc == 0 else "failed",
                "%s#%s landed %s, left open (rc %d)"
                % (meta["project"], meta["ref"], merge_sha[:8], rc))

    # One retry on a revision conflict, with the SAME idempotency key: the key
    # is what makes the retry safe, and the new revision is what makes it work.
    for _attempt in (1, 2):
        revision = kata_revision(meta["ref"], meta["project"], kata_bin, runner)
        rc, _ = kata(kata_close.close_argv(meta, merge_sha, revision),
                     meta["project"], kata_bin, runner)
        if rc != kata_close.REVISION_CONFLICT_EXIT:
            break
    return ("closed" if rc == 0 else "failed",
            "%s#%s closed with commit %s (rc %d)"
            % (meta["project"], meta["ref"], merge_sha[:8], rc))


def landed(env, opener=None, runner=None):
    repo, token = env["GITHUB_REPOSITORY"], env["GITHUB_TOKEN"]
    main_branch = env.get("BRIDGE_BASE_BRANCH", "main")
    pulls = merged_pulls(repo, env["BRIDGE_BEFORE"], env["BRIDGE_AFTER"], token,
                         opener=opener)
    if not pulls:
        print("kata-bridge: this push merged no pull request — nothing to tell kata")
        return 0
    worst = 0
    for pr in pulls:
        status, detail = land_one(pr, repo, main_branch, env["KATA_BIN"], runner)
        print("kata-bridge: #%d %s: %s" % (pr["number"], status, detail))
        if status == "failed":
            worst = 1
    return worst


# --- outcome
def fetch_outcome(repo, run_id, run_attempt, token, opener=None, fetch=None):
    """The gate's own verdict from its artifact, or None when there is none.

    The two runner accounts share no filesystem, so the artifact is DOWNLOADED;
    a local path would find nothing and read every real failure as "no verdict".
    """
    arts = gh._pages("%s/repos/%s/actions/runs/%d/artifacts" % (API, repo, int(run_id)),
                     token, key="artifacts", opener=opener)
    arts = [a for a in arts if a.get("name") == "gate-outcome" and not a.get("expired")]
    if not arts:
        return None
    # A run keeps the artifacts of EVERY attempt. A re-run that died in
    # checkout uploads nothing, and the previous attempt's `outcome=failed` is
    # still there — so the artifact must say which attempt it is from, and only
    # this event's attempt counts. The gate wrapper writes both fields.
    for art in reversed(arts):
        blob = (fetch or _download)(art["archive_download_url"], token)
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                text = z.read("gate-outcome").decode("utf-8", "replace")
        except (zipfile.BadZipFile, KeyError):
            continue
        fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        if (fields.get("outcome") and fields.get("run_id") == str(run_id)
                and fields.get("run_attempt") == str(run_attempt)):
            return fields
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _download(url, token):
    """GitHub answers with a redirect to blob storage. The second request must
    NOT carry the GitHub token: it is another host's URL, already signed."""
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer %s" % token,
        "Accept": "application/vnd.github+json"})
    try:
        urllib.request.build_opener(_NoRedirect).open(req, timeout=30)
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location") if exc.code in (301, 302, 303, 307) else None
        if not location:
            raise BridgeError("artifact download: HTTP %s" % exc.code)
        with urllib.request.urlopen(location, timeout=60) as resp:
            return resp.read()
    raise BridgeError("artifact download: expected a redirect")


def pull_for_run(repo, env, token, opener=None):
    """The pull request a gate run was about, read once."""
    if env.get("BRIDGE_RUN_EVENT") == "merge_group":
        number, _base = review_check.parse_queue_ref(env.get("BRIDGE_RUN_BRANCH", ""))
    else:
        pulls = gh._pages("%s/repos/%s/commits/%s/pulls"
                          % (API, repo, env["BRIDGE_RUN_HEAD_SHA"]), token, opener=opener)
        open_pulls = [p for p in pulls if p.get("state") == "open"]
        number = open_pulls[0]["number"] if len(open_pulls) == 1 else None
    if number is None:
        return None
    return gh._request("%s/repos/%s/pulls/%d" % (API, repo, number), token, opener=opener)


def outcome(env, opener=None, runner=None, fetch=None):
    repo, token = env["GITHUB_REPOSITORY"], env["GITHUB_TOKEN"]
    conclusion = env.get("BRIDGE_RUN_CONCLUSION") or None
    if conclusion == "success":
        print("kata-bridge: the gate passed — nothing to tell kata")
        return 0

    pr = pull_for_run(repo, env, token, opener=opener)
    if pr is None:
        print("kata-bridge: ALERT the failed gate run %s belongs to no single open "
              "pull request — nobody was bounced" % env.get("BRIDGE_RUN_URL"))
        return 0
    meta, err = pr_meta.parse_body(pr.get("body") or "")
    if err or "ref" not in meta:
        print("kata-bridge: pull request #%d names no kata issue — nobody to tell"
              % pr["number"])
        return 0

    # AUTHORITY: is this the newest attempt of this run? The event names its
    # own attempt; the API names the latest.
    run_attempt = env["BRIDGE_RUN_ATTEMPT"]
    run_now = gh._request("%s/repos/%s/actions/runs/%d" % (API, repo, int(env["BRIDGE_RUN_ID"])),
                          token, opener=opener)
    latest_attempt = run_now.get("run_attempt")

    verdict = fetch_outcome(repo, env["BRIDGE_RUN_ID"], run_attempt, token,
                            opener=opener, fetch=fetch)
    if env.get("BRIDGE_RUN_EVENT") == "merge_group":
        # A MERGE-GROUP VERDICT IS NEVER BOUNCED FROM HERE. It is about an
        # integration commit, so there is no head to compare, and nothing
        # immutable ties the run to the submission the pull request body names
        # NOW: a producer can repair and re-submit while this job waits for a
        # runner, and the old failure would land on the new attempt. (Comparing
        # the run's start with the attempt's timestamp was tried and reverted:
        # the two clocks are different machines'.) GitHub removes the pull
        # request from the queue, and the `dequeued` path — which does have the
        # event's own head to check — is what tells the producer.
        print("kata-bridge: ALERT gate %s in the merge group for #%d (outcome=%s) %s — "
              "not bounced from here; the dequeue comment tells the producer"
              % (conclusion, pr["number"], (verdict or {}).get("outcome"),
                 env.get("BRIDGE_RUN_URL") or ""))
        return 0
    plan = gate_outcome.decide(
        conclusion, (verdict or {}).get("outcome"), meta["attempt"],
        head_at_event=env.get("BRIDGE_RUN_HEAD_SHA"),
        head_now=(pr.get("head") or {}).get("sha"),
        reason="gate failed",
        run_url=env.get("BRIDGE_RUN_URL"),
        run_attempt=run_attempt, latest_run_attempt=latest_attempt,
        pr_state=pr.get("state"), pr_merged=bool(pr.get("merged_at")))

    if plan["alert"]:
        print("kata-bridge: ALERT %s" % plan["alert"])
    if plan["rerun_gate"] or plan["reenqueue"]:
        print("kata-bridge: the automatic retry is NOT built yet — a human re-runs this one")
    if not plan["comment"]:
        return 0

    # Comment FIRST, and only label once the comment is known to be there: a
    # label without its explanation is the state this lane must never produce.
    body = plan["comment"] + ("\n\n%s" % env["BRIDGE_RUN_URL"] if env.get("BRIDGE_RUN_URL") else "")
    rc, _ = kata(["comment", meta["ref"], "--body", body], meta["project"],
                 env["KATA_BIN"], runner)
    if rc != 0:
        print("kata-bridge: could not comment on %s#%s (rc %d) — NOT labelling"
              % (meta["project"], meta["ref"], rc), file=sys.stderr)
        return 1
    for label in plan["labels_add"]:
        rc, _ = kata(["label", "add", meta["ref"], label], meta["project"],
                     env["KATA_BIN"], runner)
        if rc != 0:
            print("kata-bridge: comment posted but label %s failed (rc %d)" % (label, rc),
                  file=sys.stderr)
            return 1
    print("kata-bridge: bounced %s#%s" % (meta["project"], meta["ref"]))
    return 0


# --- dequeued
# The webhook's removal reasons. MERGE and ALREADY_MERGED are landings, which
# `landed` reports; saying anything here would be noise on a closed issue.
DEQUEUE_SILENT = ("MERGE", "ALREADY_MERGED")
DEQUEUE_BOUNCES = {
    "MERGE_CONFLICT": "removed from the merge queue: it conflicts with main — "
                      "rebase onto main and re-run repoman-submit",
}


def dequeued(env, opener=None, runner=None):
    repo, token = env["GITHUB_REPOSITORY"], env["GITHUB_TOKEN"]
    reason = (env.get("BRIDGE_DEQUEUE_REASON") or "UNKNOWN_REMOVAL_REASON").upper()
    if reason in DEQUEUE_SILENT:
        print("kata-bridge: dequeued for %s — a landing, reported elsewhere" % reason)
        return 0
    pr = gh._request("%s/repos/%s/pulls/%d" % (API, repo, int(env["BRIDGE_PR_NUMBER"])),
                     token, opener=opener)
    # AUTHORITY BEFORE ACTION, as on the outcome path: the event describes the
    # pull request as it WAS. By the time this job runs the producer may have
    # rebased and re-submitted, and the body read here would then carry the NEW
    # attempt — so an old conflict would be pinned on repaired work. A conflict
    # cannot be fixed without moving the head, so a moved head means the event
    # is about work that no longer exists. The event's head is required: with
    # no head to compare, nothing can be said about which submission this was.
    event_head = env.get("BRIDGE_PR_HEAD_SHA") or ""
    if not event_head:
        raise BridgeError("the dequeued event carried no pull request head")
    ok, why = gate_outcome.is_authoritative(
        event_head, (pr.get("head") or {}).get("sha"), None, None,
        pr.get("state"), bool(pr.get("merged_at")))
    if not ok:
        print("kata-bridge: dropping a stale dequeue (%s) for #%d — %s"
              % (reason, pr["number"], why))
        return 0
    meta, err = pr_meta.parse_body(pr.get("body") or "")
    if err or "ref" not in meta:
        print("kata-bridge: pull request #%d names no kata issue — nobody to tell"
              % pr["number"])
        return 0
    url = pr.get("html_url") or ""

    if reason in DEQUEUE_BOUNCES:
        body = gate_outcome.bounce_comment(meta["attempt"], DEQUEUE_BOUNCES[reason])
        labels = [gate_outcome.BLOCKED_LABEL]
    else:
        # Deliberately NOT the bounce prefix and NOT a label: see the module
        # docstring. This is information, and it says what would follow if the
        # cause were the producer's.
        body = ("repoman queue: pull request #%d left the merge queue without "
                "landing (%s, attempt %s). A required check did not pass in its "
                "merge group — the gate against main plus this change, or "
                "mujin/review-evidence, which is re-verified there. The runs are "
                "on the pull request. If the gate could not run at all, nothing "
                "is needed from you; otherwise fix, push and re-run repoman-submit."
                % (pr["number"], reason, meta["attempt"]))
        labels = []
    if url:
        body += "\n\n" + url
    rc, _ = kata(["comment", meta["ref"], "--body", body], meta["project"],
                 env["KATA_BIN"], runner)
    if rc != 0:
        print("kata-bridge: could not comment on %s#%s (rc %d) — NOT labelling"
              % (meta["project"], meta["ref"], rc), file=sys.stderr)
        return 1
    for label in labels:
        rc, _ = kata(["label", "add", meta["ref"], label], meta["project"],
                     env["KATA_BIN"], runner)
        if rc != 0:
            print("kata-bridge: comment posted but label %s failed (rc %d)" % (label, rc),
                  file=sys.stderr)
            return 1
    print("kata-bridge: told %s#%s the pull request left the queue (%s)"
          % (meta["project"], meta["ref"], reason))
    return 0


def main(argv=None, env=None, opener=None, runner=None, fetch=None):
    argv = argv if argv is not None else sys.argv[1:]
    env = dict(env if env is not None else os.environ)
    env.setdefault("KATA_BIN", "/opt/homebrew/bin/kata")
    try:
        if runner is None:
            gh.load_kata_token(env.get("MUJIN_KATA_TOKEN", gh.KATA_TOKEN_PATH), os.environ)
        if argv == ["landed"]:
            return landed(env, opener=opener, runner=runner)
        if argv == ["outcome"]:
            return outcome(env, opener=opener, runner=runner, fetch=fetch)
        if argv == ["dequeued"]:
            return dequeued(env, opener=opener, runner=runner)
        raise BridgeError("usage: kata_bridge.py landed|outcome|dequeued")
    except (BridgeError, KeyError, ValueError) as exc:
        print("kata-bridge: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
