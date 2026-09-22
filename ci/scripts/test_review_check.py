"""Unit tests for review_check (kata jibot-code#q4av). Run: python3 -m unittest."""
import unittest
from types import SimpleNamespace

import review_check as rc


def runner(show_rc=0, show_out="{}", check_rc=0, check_out="ok"):
    calls = []

    def run(cmd, stdin=None):
        calls.append((cmd, stdin))
        if cmd[1] == "show":
            return SimpleNamespace(returncode=show_rc, stdout=show_out, stderr="")
        return SimpleNamespace(returncode=check_rc, stdout=check_out, stderr="")

    run.calls = calls
    return run


HEAD = "b" * 40


class Verdict(unittest.TestCase):
    def test_ok(self):
        state, desc = rc.verdict("q4av", "jibot-code", HEAD, "kata",
                                 "/x/review.py", runner=runner())
        self.assertEqual(state, "success")
        self.assertIn(HEAD[:8], desc)

    def test_missing_or_stale_fails(self):
        state, _ = rc.verdict("q4av", "jibot-code", HEAD, "kata",
                              "/x/review.py", runner=runner(check_rc=rc.MISSING))
        self.assertEqual(state, "failure")

    def test_malformed_fails(self):
        state, _ = rc.verdict("q4av", "jibot-code", HEAD, "kata",
                              "/x/review.py", runner=runner(check_rc=rc.MALFORMED))
        self.assertEqual(state, "failure")

    def test_kata_unreadable_is_error_not_success(self):
        # repoman-submit:232-235 fails closed for the same reason: a ledger we
        # cannot read is not a ledger that says yes.
        state, desc = rc.verdict("q4av", "jibot-code", HEAD, "kata",
                                 "/x/review.py", runner=runner(show_rc=1))
        self.assertEqual(state, "error")
        self.assertIn("failing closed", desc)

    def test_unexpected_exit_code_is_error(self):
        state, _ = rc.verdict("q4av", "jibot-code", HEAD, "kata",
                              "/x/review.py", runner=runner(check_rc=127))
        self.assertEqual(state, "error")

    def test_waiver_from_a_human_actor_passes(self):
        state, desc = rc.verdict("q4av", "jibot-code", HEAD, "kata",
                                 "/x/review.py", actor="joi-macct",
                                 runner=runner(check_rc=rc.WAIVED))
        self.assertEqual(state, "success")
        self.assertIn("waived", desc)

    def test_a_waiver_with_an_UNKNOWN_author_is_refused(self):
        # actor=None used to fall through is_automated_actor as false and let
        # the waiver pass — a fail-OPEN on the control that exists to stop an
        # unattended worker waiving its own review.
        state, desc = rc.verdict("q4av", "jibot-code", HEAD, "kata",
                                 "/x/review.py", actor=None,
                                 runner=runner(check_rc=rc.WAIVED))
        self.assertEqual(state, "failure")
        self.assertIn("could not be identified", desc)

    def test_waiver_from_an_automated_actor_is_refused(self):
        # The rule a local check cannot enforce: repoman-submit:67-93 refuses
        # this, but only for a producer that chose to run the local code.
        for actor in ("joi-macct-unattended", "joi-azbd2-unattended-codex",
                      "joi-macazbd-dispatcher"):
            state, desc = rc.verdict("q4av", "jibot-code", HEAD, "kata",
                                     "/x/review.py", actor=actor,
                                     runner=runner(check_rc=rc.WAIVED))
            self.assertEqual(state, "failure", actor)
            self.assertIn("automated actor", desc)

    def test_head_sha_is_what_is_checked(self):
        r = runner()
        rc.verdict("q4av", "jibot-code", HEAD, "kata", "/x/review.py", runner=r)
        check_cmd = r.calls[1][0]
        self.assertEqual(check_cmd[-1], HEAD)
        self.assertIn("check-show-json", check_cmd)


def status(state="success", creator="mujin-bot", context=rc.STATUS_CONTEXT):
    return {"context": context, "state": state, "creator_login": creator}


def member(number=1, head="a" * 40, statuses=None, attributed=True):
    return {"number": number, "head_sha": head,
            "statuses": statuses if statuses is not None else [status()],
            "attributed": attributed}


VERIFIERS = {"mujin-bot"}


class GroupVerdict(unittest.TestCase):
    def test_happy(self):
        ok, why = rc.group_verdict([member()], VERIFIERS)
        self.assertTrue(ok, why)

    def test_empty_group_fails_closed(self):
        ok, why = rc.group_verdict([], VERIFIERS)
        self.assertFalse(ok)
        self.assertIn("failing closed", why)

    def test_unattributable_commit_fails_closed(self):
        ok, why = rc.group_verdict([member(attributed=False)], VERIFIERS)
        self.assertFalse(ok)
        self.assertIn("not attributable", why)

    def test_missing_status(self):
        ok, why = rc.group_verdict([member(statuses=[])], VERIFIERS)
        self.assertFalse(ok)
        self.assertIn("has no", why)

    def test_forged_status_is_treated_as_absent(self):
        # Any push-access token can post this status. The group check is where
        # a forged one is caught.
        ok, why = rc.group_verdict(
            [member(statuses=[status(creator="joi")])], VERIFIERS)
        self.assertFalse(ok)
        self.assertIn("created by joi", why)
        self.assertIn("treated as absent", why)

    def test_a_newer_failure_beats_an_older_success(self):
        # The statuses endpoint returns newest first. Searching the whole list
        # for any success lets a stale pass defeat the current verdict, which
        # is the same as having no gate at all.
        ok, why = rc.group_verdict(
            [member(statuses=[status(state="failure"), status()])], VERIFIERS)
        self.assertFalse(ok)
        self.assertIn("current", why)

    def test_a_newer_success_over_an_older_failure_passes(self):
        ok, _ = rc.group_verdict(
            [member(statuses=[status(), status(state="failure")])], VERIFIERS)
        self.assertTrue(ok)

    def test_a_pending_current_status_does_not_pass(self):
        ok, _ = rc.group_verdict(
            [member(statuses=[status(state="pending"), status()])], VERIFIERS)
        self.assertFalse(ok)

    def test_failed_status_does_not_pass(self):
        ok, _ = rc.group_verdict(
            [member(statuses=[status(state="failure")])], VERIFIERS)
        self.assertFalse(ok)

    def test_a_status_with_another_context_does_not_pass(self):
        ok, _ = rc.group_verdict(
            [member(statuses=[status(context="ci/other")])], VERIFIERS)
        self.assertFalse(ok)

    def test_one_bad_member_fails_the_whole_group(self):
        ok, _ = rc.group_verdict(
            [member(number=1), member(number=2, statuses=[])], VERIFIERS)
        self.assertFalse(ok)


class QueueRef(unittest.TestCase):
    def test_parses(self):
        self.assertEqual(
            rc.parse_queue_ref("refs/heads/gh-readonly-queue/main/pr-12-abc1234"),
            (12, "main"))
        self.assertEqual(
            rc.parse_queue_ref("gh-readonly-queue/main/pr-3-" + "a" * 40),
            (3, "main"))

    def test_base_with_a_slash(self):
        self.assertEqual(
            rc.parse_queue_ref("gh-readonly-queue/release/2.0/pr-9-abc1234"),
            (9, "release/2.0"))

    def test_non_queue_ref_returns_none(self):
        for ref in ("refs/heads/main", "", None, "gh-readonly-queue/main/pr-x-abc"):
            self.assertEqual(rc.parse_queue_ref(ref), (None, None), ref)


if __name__ == "__main__":
    unittest.main()
