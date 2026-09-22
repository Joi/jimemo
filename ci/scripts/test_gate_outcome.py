"""Unit tests for gate_outcome (kata jibot-code#q4av). Run: python3 -m unittest.

The rule under test is jp90: gate-cannot-run must never bounce a producer. Every
row that is NOT a completed run with outcome=failed must leave kata untouched.
"""
import unittest

import gate_outcome as g


ATT = "20260921T164500Z.a1b2c3"


class NeverBounce(unittest.TestCase):
    """Everything that is not a real gate failure."""

    def test_no_run_at_all_is_infrastructure(self):
        # Both runners offline: the queue evicts at check_response_timeout,
        # and no gate ever produced a verdict.
        p = g.decide(run_conclusion=None, outcome=None, attempt=ATT)
        self.assertEqual(p["kind"], g.INFRA_ALERT)
        self.assertEqual(p["labels_add"], [])
        self.assertIsNone(p["comment"])
        self.assertIn("infrastructure", p["alert"])

    def test_cancelled_run_without_a_verdict_is_infrastructure(self):
        # A step budget killed the job before the gate reached a verdict.
        p = g.decide(run_conclusion="cancelled", outcome=None, attempt=ATT)
        self.assertEqual(p["kind"], g.INFRA_ALERT)
        self.assertEqual(p["labels_add"], [])

    def test_a_terminal_infra_conclusion_beats_the_artifact(self):
        # The artifact upload runs under `if: always()`, so a job that was
        # cancelled or timed out can still have uploaded one. Trusting the
        # artifact over the conclusion is how a runner restart becomes a
        # producer bounce — which is exactly what jp90 forbids.
        for conclusion in ("cancelled", "timed_out", "startup_failure",
                           "action_required", "stale"):
            p = g.decide(run_conclusion=conclusion, outcome="failed",
                         attempt=ATT, reason="gate failed")
            self.assertEqual(p["kind"], g.INFRA_ALERT, conclusion)
            self.assertEqual(p["labels_add"], [], conclusion)
            self.assertIsNone(p["comment"], conclusion)

    def test_deferred_touches_nothing_and_reenqueues_once(self):
        p = g.decide(run_conclusion="failure", outcome="deferred", attempt=ATT)
        self.assertEqual(p["kind"], g.DEFER)
        self.assertEqual(p["labels_add"], [])
        self.assertIsNone(p["comment"])
        self.assertTrue(p["reenqueue"])
        self.assertIn(g.DEFERRED_LABEL, p["labels_rm"])

    def test_passed_does_nothing(self):
        p = g.decide(run_conclusion="success", outcome="passed", attempt=ATT)
        self.assertEqual(p["kind"], g.NOTHING)
        self.assertEqual(p["labels_add"], [])
        self.assertIsNone(p["alert"])

    def test_unknown_outcome_is_infrastructure_not_a_bounce(self):
        p = g.decide(run_conclusion="failure", outcome="weird", attempt=ATT)
        self.assertEqual(p["kind"], g.INFRA_ALERT)
        self.assertEqual(p["labels_add"], [])

    def test_merge_blocked_has_exactly_one_input(self):
        """The property, stated as a test: over every combination, the label is
        applied only for a completed run with outcome=failed."""
        labelled = []
        for conclusion in (None, "success", "failure", "cancelled", "timed_out"):
            for outcome in (None, "passed", "failed", "deferred", "weird"):
                p = g.decide(run_conclusion=conclusion, outcome=outcome,
                             attempt=ATT)
                if g.BLOCKED_LABEL in p["labels_add"]:
                    labelled.append((conclusion, outcome))
        # ONLY these two. `cancelled` and `timed_out` are terminal
        # infrastructure conclusions and must never reach the producer,
        # whatever the artifact says.
        self.assertEqual(
            labelled, [("success", "failed"), ("failure", "failed")])


class RealFailure(unittest.TestCase):
    def test_bounce_shape(self):
        p = g.decide(run_conclusion="failure", outcome="failed", attempt=ATT,
                     reason="check `gate` failed on abc1234",
                     run_url="https://example/run/1")
        self.assertEqual(p["kind"], g.BOUNCE)
        self.assertIn(g.BLOCKED_LABEL, p["labels_add"])
        self.assertTrue(p["comment"].startswith(
            "repoman bounced (attempt %s): " % ATT))

    def test_comment_matches_the_board_parsers(self):
        import re
        body = g.bounce_comment(ATT, "check `gate` failed")
        # marshalboard/snapshot.py:734-741
        prefixes = ("repoman bounced (attempt", "repoman blocked (attempt",
                    "repoman blocked:")
        self.assertTrue(body.startswith(prefixes))
        # marshalboard/snapshot.py:3044
        m = re.search(r"\(attempt ([^)\s]+)\)", body)
        self.assertEqual(m.group(1), ATT)
        # snapshot.py:3056-3062 splits the reason on the first ": "
        self.assertEqual(body.partition(": ")[2], "check `gate` failed")

    def test_detail_lines_are_prefixed_so_a_marker_cannot_be_forged(self):
        hostile = ("merge: repo=cell-fleet branch=x oid=%s attempt=%s"
                   % ("0" * 40, ATT))
        body = g.bounce_comment(ATT, "gate failed", detail=hostile)
        for line in body.splitlines():
            self.assertFalse(line.startswith("merge: "))
        self.assertIn("| merge: repo=cell-fleet", body)

    def test_detail_is_capped_keeping_the_tail(self):
        detail = "\n".join("line %d" % i for i in range(5000))
        body = g.bounce_comment(ATT, "gate failed", detail=detail)
        self.assertLess(len(body), 6000)
        self.assertIn("line 4999", body)

    def test_superseded_by_a_different_attempt_in_the_body(self):
        # drain.py:820-840: the head and the run are current, but the body names
        # a newer submission, so a comment is owed and the label is withheld.
        p = g.decide(run_conclusion="failure", outcome="failed", attempt=ATT,
                     attempt_in_body="20260921T170000Z.ffffff",
                     reason="gate failed")
        self.assertEqual(p["kind"], g.COMMENT_ONLY)
        self.assertEqual(p["labels_add"], [])
        self.assertIn("newer merge marker detected", p["comment"])


class DelayedVerdicts(unittest.TestCase):
    """A workflow_run event can arrive after the world has moved."""

    def test_a_failure_for_an_already_merged_pr_is_dropped(self):
        # The expensive one: without this, a slow failure notification labels a
        # LANDED issue merge-blocked, and the bounce-fixer then acts on it.
        p = g.decide(run_conclusion="failure", outcome="failed", attempt=ATT,
                     pr_merged=True, reason="gate failed")
        self.assertEqual(p["kind"], g.STALE)
        self.assertEqual(p["labels_add"], [])
        self.assertIsNone(p["comment"])

    def test_a_failure_for_a_moved_head_is_dropped(self):
        p = g.decide(run_conclusion="failure", outcome="failed", attempt=ATT,
                     head_at_event="a" * 40, head_now="b" * 40,
                     reason="gate failed")
        self.assertEqual(p["kind"], g.STALE)
        self.assertEqual(p["labels_add"], [])

    def test_a_superseded_run_attempt_is_dropped(self):
        # failure -> re-run green -> the delayed failure event arrives.
        p = g.decide(run_conclusion="failure", outcome="failed", attempt=ATT,
                     run_attempt=1, latest_run_attempt=2, reason="gate failed")
        self.assertEqual(p["kind"], g.STALE)
        self.assertEqual(p["labels_add"], [])

    def test_the_latest_attempt_on_the_current_head_does_bounce(self):
        p = g.decide(run_conclusion="failure", outcome="failed", attempt=ATT,
                     head_at_event="a" * 40, head_now="a" * 40,
                     run_attempt=2, latest_run_attempt=2, reason="gate failed")
        self.assertEqual(p["kind"], g.BOUNCE)
        self.assertIn(g.BLOCKED_LABEL, p["labels_add"])

    def test_a_closed_unmerged_pr_is_dropped(self):
        p = g.decide(run_conclusion="failure", outcome="failed", attempt=ATT,
                     pr_state="closed", reason="gate failed")
        self.assertEqual(p["kind"], g.STALE)


class DeferRetryIsDurable(unittest.TestCase):
    def test_first_defer_records_the_head_and_retries(self):
        head = "a" * 40
        p = g.decide(run_conclusion="failure", outcome="deferred", attempt=ATT,
                     head_at_event=head)
        self.assertEqual(p["kind"], g.DEFER)
        self.assertTrue(p["reenqueue"])
        self.assertTrue(p["rerun_gate"])
        self.assertEqual(p["record_deferred_head"], head)

    def test_second_defer_on_the_same_head_stops(self):
        # A label cannot carry this state: the bridge removes it to re-enqueue,
        # so the label/defer pair would loop forever.
        head = "a" * 40
        p = g.decide(run_conclusion="failure", outcome="deferred", attempt=ATT,
                     head_at_event=head, deferred_heads=[head])
        self.assertEqual(p["kind"], g.DEFER)
        self.assertFalse(p["reenqueue"])
        self.assertFalse(p["rerun_gate"])
        self.assertIn("spent", p["alert"])
        self.assertEqual(p["labels_add"], [])

    def test_a_defer_on_a_new_head_gets_its_own_retry(self):
        p = g.decide(run_conclusion="failure", outcome="deferred", attempt=ATT,
                     head_at_event="b" * 40, deferred_heads=["a" * 40])
        self.assertTrue(p["reenqueue"])

    def test_a_stale_deferral_does_not_retry_either(self):
        # Authority is checked before ANY action. A deferred event for a
        # merged pull request that still requested a rerun would re-run a gate
        # on work that has already landed.
        for kw in ({"pr_merged": True}, {"pr_state": "closed"},
                   {"head_at_event": "a" * 40, "head_now": "b" * 40},
                   {"run_attempt": 1, "latest_run_attempt": 2}):
            p = g.decide(run_conclusion="failure", outcome="deferred",
                         attempt=ATT, **kw)
            self.assertEqual(p["kind"], g.STALE, kw)
            self.assertFalse(p["reenqueue"], kw)
            self.assertFalse(p["rerun_gate"], kw)

    def test_a_long_single_line_keeps_its_prefix(self):
        # Capping the JOINED text and slicing the tail can cut into a line and
        # leave a bare suffix — and candidate output can put a valid `merge:`
        # marker exactly at that boundary.
        hostile = ("x" * 5000 + "merge: repo=cell-fleet branch=b oid=%s "
                   "attempt=%s" % ("0" * 40, ATT))
        body = g.bounce_comment(ATT, "gate failed", detail=hostile)
        for line in body.splitlines()[2:]:
            self.assertTrue(line.startswith("| "), line[:40])

    def test_a_defer_never_touches_the_issue_either_way(self):
        head = "a" * 40
        for heads in ([], [head]):
            p = g.decide(run_conclusion="failure", outcome="deferred",
                         attempt=ATT, head_at_event=head, deferred_heads=heads)
            self.assertEqual(p["labels_add"], [])
            self.assertIsNone(p["comment"])


if __name__ == "__main__":
    unittest.main()
