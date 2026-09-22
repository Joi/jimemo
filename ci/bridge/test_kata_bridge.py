"""Tests for the kata bridge. No network, no kata, no token file."""

import io
import json
import unittest
import zipfile

import kata_bridge as kb
from test_bridge import FakeGitHub, REPO, SHA_A, SHA_B

MERGE = "e" * 40
BODY = "\n".join(["kata: jibot-code#kc3m", "repo: canary-ops", "branch: feat",
                  "attempt: 20260921T120000Z.abcdef", "host: macct",
                  "worktree: /Users/joi/repos/canary-ops"])


def pr(number=7, merged=True, base="main", body=BODY, head=SHA_A, state="closed"):
    return {"number": number, "merged_at": "2026-09-21T12:00:00Z" if merged else None,
            "merge_commit_sha": MERGE, "base": {"ref": base}, "body": body,
            "head": {"sha": head}, "state": state}


class Kata:
    """Records argv; `show` answers with a revision, `close` with a scripted rc."""

    def __init__(self, close_rcs=(0,), comment_rc=0, label_rc=0, revisions=(5, 6)):
        self.argv, self.close_rcs = [], list(close_rcs)
        self.comment_rc, self.label_rc, self.revisions = comment_rc, label_rc, list(revisions)

    def __call__(self, cmd):
        self.argv.append(cmd[1:])
        verb = cmd[1]

        class R:
            stdout, stderr, returncode = "", "", 0
        if verb == "show":
            R.stdout = json.dumps({"issue": {"revision": self.revisions.pop(0)}})
        elif verb == "close":
            R.returncode = self.close_rcs.pop(0)
        elif verb == "comment":
            R.returncode = self.comment_rc
        elif verb == "label":
            R.returncode = self.label_rc
        return R

    def verbs(self):
        return [a[0] for a in self.argv]


def landed_env(**kw):
    e = {"GITHUB_REPOSITORY": REPO, "GITHUB_TOKEN": "t", "KATA_BIN": "kata",
         "BRIDGE_BEFORE": SHA_B, "BRIDGE_AFTER": MERGE}
    e.update(kw)
    return e


def landed_gh(pulls):
    return FakeGitHub({
        ("GET", "/repos/%s/compare/%s...%s" % (REPO, SHA_B, MERGE)): {"commits": [{"sha": MERGE}]},
        ("GET", "/repos/%s/commits/%s/pulls" % (REPO, MERGE)): pulls})


class Landed(unittest.TestCase):
    def test_a_merged_pull_request_closes_its_issue_with_the_landed_commit(self):
        k = Kata()
        rc = kb.main(["landed"], landed_env(), opener=landed_gh([pr()]), runner=k)
        self.assertEqual(rc, 0)
        self.assertEqual(k.verbs(), ["show", "close"])
        close = k.argv[1]
        self.assertEqual(close[:3], ["close", "kc3m", "--done"])
        self.assertIn("commit:%s" % MERGE, close)
        self.assertEqual(close[close.index("--if-match") + 1], "5")
        self.assertEqual(close[close.index("--idempotency-key") + 1],
                         "repoman-canary-ops-kc3m-20260921T120000Z.abcdef-%s" % MERGE)
        self.assertEqual(close[-3:], ["--project", "jibot-code", "--agent"])

    def test_a_revision_conflict_retries_once_with_the_new_revision_and_the_same_key(self):
        k = Kata(close_rcs=(6, 0))
        rc = kb.main(["landed"], landed_env(), opener=landed_gh([pr()]), runner=k)
        self.assertEqual(rc, 0)
        self.assertEqual(k.verbs(), ["show", "close", "show", "close"])
        first, second = k.argv[1], k.argv[3]
        self.assertEqual(first[first.index("--if-match") + 1], "5")
        self.assertEqual(second[second.index("--if-match") + 1], "6")
        self.assertEqual(first[first.index("--idempotency-key") + 1],
                         second[second.index("--idempotency-key") + 1])

    def test_close_false_comments_and_never_closes(self):
        k = Kata()
        kb.main(["landed"], landed_env(), opener=landed_gh([pr(body=BODY + "\nclose: false")]),
                runner=k)
        self.assertEqual(k.verbs(), ["comment"])
        self.assertIn("repoman landed %s" % MERGE, k.argv[0][3])
        self.assertIn("issue left open", k.argv[0][3])

    def test_a_merge_into_another_branch_is_not_a_landing(self):
        k = Kata()
        rc = kb.main(["landed"], landed_env(), opener=landed_gh([pr(base="feature")]), runner=k)
        self.assertEqual((rc, k.argv), (0, []))

    def test_an_unmerged_pull_request_on_the_commit_is_ignored(self):
        k = Kata()
        kb.main(["landed"], landed_env(), opener=landed_gh([pr(merged=False)]), runner=k)
        self.assertEqual(k.argv, [])

    def test_a_hostile_body_never_reaches_kata(self):
        k = Kata()
        kb.main(["landed"], landed_env(),
                opener=landed_gh([pr(body=BODY.replace("kc3m", "x; rm -rf ~"))]), runner=k)
        self.assertEqual(k.argv, [])

    def test_a_failed_close_fails_the_job(self):
        k = Kata(close_rcs=(1,))
        rc = kb.main(["landed"], landed_env(), opener=landed_gh([pr()]), runner=k)
        self.assertEqual(rc, 1)


def artifact_zip(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("gate-outcome", text)
    return buf.getvalue()


def outcome_env(**kw):
    e = {"GITHUB_REPOSITORY": REPO, "GITHUB_TOKEN": "t", "KATA_BIN": "kata",
         "BRIDGE_RUN_ID": "42", "BRIDGE_RUN_CONCLUSION": "failure",
         "BRIDGE_RUN_ATTEMPT": "1",
         "BRIDGE_RUN_EVENT": "pull_request", "BRIDGE_RUN_HEAD_SHA": SHA_A,
         "BRIDGE_RUN_URL": "https://github.test/run/42"}
    e.update(kw)
    return e


def outcome_gh(open_pr, artifacts=True, latest_attempt=1, n_artifacts=1):
    return FakeGitHub({
        # Routed BEFORE the bare run lookup: FakeGitHub matches by prefix.
        ("GET", "/repos/%s/actions/runs/42/artifacts" % REPO): {"artifacts": (
            [{"name": "gate-outcome", "expired": False,
              "archive_download_url": "https://x/zip/%d" % i}
             for i in range(n_artifacts)] if artifacts else [])},
        ("GET", "/repos/%s/actions/runs/42" % REPO): {"run_attempt": latest_attempt},
        ("GET", "/repos/%s/commits/%s/pulls" % (REPO, SHA_A)): [open_pr],
        ("GET", "/repos/%s/pulls/7" % REPO): open_pr})


def verdict_text(outcome, run_id="42", run_attempt="1"):
    return "outcome=%s\nrun_id=%s\nrun_attempt=%s\nhead_sha=%s\n" % (
        outcome, run_id, run_attempt, SHA_A)


OPEN = pr(merged=False, state="open")


class Outcome(unittest.TestCase):
    def run_outcome(self, text, k=None, env=None, artifacts=True, open_pr=OPEN,
                    latest_attempt=1, texts=None):
        """`text` is an outcome word, expanded to a full artifact for run 42
        attempt 1; pass `texts` for raw artifact bodies, one per artifact."""
        k = k or Kata()
        bodies = texts if texts is not None else [verdict_text(text) if text else ""]
        by_url = {"https://x/zip/%d" % i: b for i, b in enumerate(bodies)}
        rc = kb.main(["outcome"], env or outcome_env(),
                     opener=outcome_gh(open_pr, artifacts, latest_attempt, len(bodies)),
                     runner=k, fetch=lambda url, tok: artifact_zip(by_url[url]))
        return rc, k

    def test_a_failed_gate_comments_first_then_labels(self):
        rc, k = self.run_outcome("failed")
        self.assertEqual(rc, 0)
        self.assertEqual(k.verbs(), ["comment", "label"])
        body = k.argv[0][3]
        self.assertTrue(body.startswith(
            "repoman bounced (attempt 20260921T120000Z.abcdef): gate failed"), body)
        self.assertIn("https://github.test/run/42", body)
        self.assertEqual(k.argv[1][:4], ["label", "add", "kc3m", "merge-blocked"])

    def test_a_comment_that_fails_means_no_label(self):
        rc, k = self.run_outcome("failed", k=Kata(comment_rc=1))
        self.assertEqual(rc, 1)
        self.assertEqual(k.verbs(), ["comment"])

    def test_a_deferred_gate_never_touches_kata(self):
        rc, k = self.run_outcome("deferred")
        self.assertEqual((rc, k.argv), (0, []))

    def test_a_cancelled_run_never_bounces_even_with_a_failed_artifact(self):
        rc, k = self.run_outcome("failed",
                                 env=outcome_env(BRIDGE_RUN_CONCLUSION="cancelled"))
        self.assertEqual((rc, k.argv), (0, []))

    def test_no_artifact_is_infrastructure_not_a_bounce(self):
        rc, k = self.run_outcome(None, artifacts=False)
        self.assertEqual((rc, k.argv), (0, []))

    def test_a_verdict_for_a_head_the_pull_request_has_left_is_dropped(self):
        moved = pr(merged=False, state="open", head=SHA_B)
        rc, k = self.run_outcome("failed", open_pr=moved)
        self.assertEqual((rc, k.argv), (0, []))

    def test_a_passing_gate_reads_nothing(self):
        k = Kata()
        rc = kb.main(["outcome"], outcome_env(BRIDGE_RUN_CONCLUSION="success"),
                     opener=FakeGitHub({}), runner=k)
        self.assertEqual((rc, k.argv), (0, []))

    def test_a_merge_group_failure_is_never_bounced_from_the_outcome_path(self):
        # Nothing immutable ties a merge-group run to the submission the body
        # names now, so a repaired re-submission could inherit the old failure.
        # The dequeue path, which has the event's head, tells the producer.
        for word in ("failed", "deferred", None):
            env = outcome_env(BRIDGE_RUN_EVENT="merge_group", BRIDGE_RUN_HEAD_SHA="f" * 40,
                              BRIDGE_RUN_BRANCH="gh-readonly-queue/main/pr-7-" + "d" * 40)
            rc, k = self.run_outcome(word, env=env)
            self.assertEqual((rc, k.argv), (0, []), word)

    # --- a verdict is bound to ITS run attempt and to the CURRENT submission
    def test_a_rerun_that_uploaded_nothing_does_not_inherit_the_old_attempts_failure(self):
        # Attempt 2 died in checkout; attempt 1's artifact is still on the run.
        rc, k = self.run_outcome(None, texts=[verdict_text("failed", run_attempt="1")],
                                 env=outcome_env(BRIDGE_RUN_ATTEMPT="2"), latest_attempt=2)
        self.assertEqual((rc, k.argv), (0, []))

    def test_the_artifact_of_this_attempt_is_found_among_older_ones(self):
        rc, k = self.run_outcome(None, texts=[verdict_text("passed", run_attempt="1"),
                                              verdict_text("failed", run_attempt="2")],
                                 env=outcome_env(BRIDGE_RUN_ATTEMPT="2"), latest_attempt=2)
        self.assertEqual(k.verbs(), ["comment", "label"])

    def test_an_artifact_from_another_run_is_not_a_verdict(self):
        rc, k = self.run_outcome(None, texts=[verdict_text("failed", run_id="41")])
        self.assertEqual((rc, k.argv), (0, []))

    def test_a_superseded_run_attempt_is_dropped(self):
        rc, k = self.run_outcome("failed", latest_attempt=2)
        self.assertEqual((rc, k.argv), (0, []))

class ArtifactDownload(unittest.TestCase):
    """The real downloader, with the transport mocked. GitHub answers an
    artifact download with a redirect to blob storage: the GitHub token must go
    to GitHub and must NOT follow the redirect to the other host."""

    def setUp(self):
        self.first, self.second = [], []
        self._build, self._urlopen = kb.urllib.request.build_opener, kb.urllib.request.urlopen

        class Opener:
            def __init__(opener_self, handlers):
                opener_self.handlers = handlers

            def open(opener_self, req, timeout=None):
                self.first.append((req.full_url, dict(req.header_items()), opener_self.handlers))
                raise self.first_reply

        def urlopen(target, timeout=None):
            self.second.append(target)
            return io.BytesIO(b"ZIPBYTES")
        kb.urllib.request.build_opener = lambda *handlers: Opener(handlers)
        kb.urllib.request.urlopen = urlopen

    def tearDown(self):
        kb.urllib.request.build_opener, kb.urllib.request.urlopen = self._build, self._urlopen

    def redirect(self, code=302, location="https://blob.example/artifact.zip?sig=abc"):
        import email.message
        headers = email.message.Message()
        if location:
            headers["Location"] = location
        return kb.urllib.error.HTTPError("https://api.github.com/x", code, "Found", headers, None)

    def test_the_token_goes_to_github_and_not_to_the_redirect_target(self):
        self.first_reply = self.redirect()
        self.assertEqual(kb._download("https://api.github.com/x", "SECRET-TOKEN"), b"ZIPBYTES")
        (url, headers, handlers), = self.first
        self.assertEqual(headers.get("Authorization"), "Bearer SECRET-TOKEN")
        # The opener must not follow redirects by itself: urllib would carry
        # the Authorization header along to the new host.
        self.assertIn(kb._NoRedirect, handlers)
        # The second request is a bare URL: no Request object, so no headers
        # of ours at all, and certainly not the token.
        self.assertEqual(self.second, ["https://blob.example/artifact.zip?sig=abc"])
        self.assertNotIn("SECRET-TOKEN", repr(self.second))

    def test_the_redirect_handler_really_refuses_to_follow(self):
        self.assertIsNone(kb._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://x"))

    def test_an_error_that_is_not_a_redirect_is_a_bridge_error(self):
        for reply in (self.redirect(code=403), self.redirect(code=302, location=None)):
            self.first_reply = reply
            with self.assertRaises(kb.BridgeError):
                kb._download("https://api.github.com/x", "SECRET-TOKEN")
        self.assertEqual(self.second, [])


class Dequeued(unittest.TestCase):
    def run_dq(self, reason, the_pr=OPEN, k=None, event_head=SHA_A):
        k = k or Kata()
        env = {"GITHUB_REPOSITORY": REPO, "GITHUB_TOKEN": "t", "KATA_BIN": "kata",
               "BRIDGE_PR_NUMBER": "7", "BRIDGE_DEQUEUE_REASON": reason,
               "BRIDGE_PR_HEAD_SHA": event_head}
        gh_ = FakeGitHub({("GET", "/repos/%s/pulls/7" % REPO): the_pr})
        return kb.main(["dequeued"], env, opener=gh_, runner=k), k

    def test_a_ci_failure_is_said_but_never_labelled(self):
        # It may be a gate that could not run. jp90: that never bounces.
        rc, k = self.run_dq("CI_FAILURE")
        self.assertEqual(rc, 0)
        self.assertEqual(k.verbs(), ["comment"])
        body = k.argv[0][3]
        self.assertTrue(body.startswith("repoman queue: pull request #7 left the merge queue"), body)
        self.assertNotIn("repoman bounced", body)
        self.assertIn("merge group", body)
        self.assertIn("CI_FAILURE", body)

    def test_a_conflict_bounces_comment_first_then_label(self):
        rc, k = self.run_dq("MERGE_CONFLICT")
        self.assertEqual(k.verbs(), ["comment", "label"])
        self.assertTrue(k.argv[0][3].startswith(
            "repoman bounced (attempt 20260921T120000Z.abcdef): removed from the merge queue"))
        self.assertEqual(k.argv[1][:4], ["label", "add", "kc3m", "merge-blocked"])

    def test_a_conflict_whose_comment_fails_is_not_labelled(self):
        rc, k = self.run_dq("MERGE_CONFLICT", k=Kata(comment_rc=1))
        self.assertEqual((rc, k.verbs()), (1, ["comment"]))

    def test_a_conflict_whose_label_fails_fails_the_job_after_the_comment(self):
        rc, k = self.run_dq("MERGE_CONFLICT", k=Kata(label_rc=1))
        self.assertEqual((rc, k.verbs()), (1, ["comment", "label"]))

    def test_a_conflict_event_for_a_head_the_producer_has_moved_past_is_dropped(self):
        # The producer rebased and re-submitted while this job waited for a
        # runner: the body now names the NEW attempt, and the old conflict must
        # not be pinned on it.
        rebased = pr(merged=False, state="open", head=SHA_B)
        for reason in ("MERGE_CONFLICT", "CI_FAILURE"):
            rc, k = self.run_dq(reason, the_pr=rebased, event_head=SHA_A)
            self.assertEqual((rc, k.argv), (0, []), reason)

    def test_a_closed_unmerged_pull_request_is_not_bounced(self):
        closed = pr(merged=False, state="closed")
        rc, k = self.run_dq("MERGE_CONFLICT", the_pr=closed)
        self.assertEqual((rc, k.argv), (0, []))

    def test_an_event_with_no_head_cannot_be_attributed_and_fails_the_job(self):
        rc, k = self.run_dq("MERGE_CONFLICT", event_head="")
        self.assertEqual((rc, k.argv), (1, []))

    def test_a_landing_says_nothing_and_reads_nothing(self):
        for reason in ("MERGE", "ALREADY_MERGED", "merge"):
            k = Kata()
            env = {"GITHUB_REPOSITORY": REPO, "GITHUB_TOKEN": "t", "KATA_BIN": "kata",
                   "BRIDGE_PR_NUMBER": "7", "BRIDGE_DEQUEUE_REASON": reason}
            rc = kb.main(["dequeued"], env, opener=FakeGitHub({}), runner=k)
            self.assertEqual((rc, k.argv), (0, []))

    def test_a_merged_pull_request_says_nothing_whatever_the_reason(self):
        rc, k = self.run_dq("CI_FAILURE", the_pr=pr())
        self.assertEqual((rc, k.argv), (0, []))

    def test_an_absent_or_unknown_reason_is_still_said(self):
        for reason in ("", "SOMETHING_NEW"):
            rc, k = self.run_dq(reason)
            self.assertEqual(k.verbs(), ["comment"])

    def test_a_pull_request_with_no_kata_ref_tells_nobody(self):
        rc, k = self.run_dq("CI_FAILURE", the_pr=pr(merged=False, state="open", body="prose"))
        self.assertEqual((rc, k.argv), (0, []))


if __name__ == "__main__":
    unittest.main()
