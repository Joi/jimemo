"""Unit tests for kata_close (kata jibot-code#q4av). Run: python3 -m unittest.

These assert argv byte for byte against the strings cell-fleet's drain writes
today (drain.py:1075-1076, kataops.py:230-258, drain.py:1343-1355), because the
board, the land report and producer-triage parse them.
"""
import unittest

import kata_close as kc


META = {
    "project": "jibot-code", "ref": "q4av", "repo": "jibot-ops",
    "branch": "q4av-jibot-ops-pr-canary",
    "attempt": "20260921T164500Z.a1b2c3",
    "host": "macct",
    "worktree": "/Users/joi/repos/.worktrees/q4av-jibot-ops-pr-canary",
    "dispatch_id": "macct-1789973004-96015",
    "head_sha": "b" * 40,
    "close": True,
}
MERGE = "c" * 40


def pull(merged=True, base="main"):
    return {"merged_at": "2026-09-21T00:00:00Z" if merged else None,
            "base": {"ref": base}}


class ShouldClose(unittest.TestCase):
    def test_happy(self):
        ok, why = kc.should_close(pull(), MERGE, "main", "Joi/jibot-ops",
                                  "Joi/jibot-ops")
        self.assertTrue(ok, why)

    def test_no_associated_pull_request(self):
        # A direct push to main — a break-glass landing — closes nothing.
        ok, why = kc.should_close(None, MERGE, "main", "Joi/jibot-ops",
                                  "Joi/jibot-ops")
        self.assertFalse(ok)
        self.assertIn("no pull request", why)

    def test_closed_without_merging(self):
        ok, why = kc.should_close(pull(merged=False), MERGE, "main",
                                  "Joi/jibot-ops", "Joi/jibot-ops")
        self.assertFalse(ok)
        self.assertIn("not merged", why)

    def test_merged_into_a_feature_branch_is_not_a_landing(self):
        # The guard the first design draft did not have. A false `landed`
        # record retires a worktree that still holds work.
        ok, why = kc.should_close(pull(base="some-feature"), MERGE, "main",
                                  "Joi/jibot-ops", "Joi/jibot-ops")
        self.assertFalse(ok)
        self.assertIn("not a landing", why)

    def test_wrong_repository(self):
        ok, why = kc.should_close(pull(), MERGE, "main", "Joi/jibot-ops",
                                  "Someone/else")
        self.assertFalse(ok)
        self.assertIn("is not", why)

    def test_an_empty_expected_repository_refuses(self):
        # An empty expectation used to mean "no check". The bridge always
        # knows where it runs, so an empty one is a wiring fault: refuse.
        for expected in ("", None):
            ok, why = kc.should_close(pull(), MERGE, "main", expected,
                                      "Joi/jibot-ops")
            self.assertFalse(ok, expected)
            self.assertIn("repository", why)

    def test_no_merge_sha(self):
        ok, why = kc.should_close(pull(), "", "main", "Joi/jibot-ops",
                                  "Joi/jibot-ops")
        self.assertFalse(ok)
        self.assertIn("no merge commit sha", why)


class CloseArgv(unittest.TestCase):
    def test_argv_is_the_drain_shape(self):
        argv = kc.close_argv(META, MERGE, revision=15, took_seconds=252)
        self.assertEqual(argv, [
            "close", "q4av", "--done",
            "--message",
            "Landed on main by repoman (attempt 20260921T164500Z.a1b2c3 took 4m12s).",
            "--evidence", "commit:" + MERGE,
            "--idempotency-key",
            "repoman-jibot-ops-q4av-20260921T164500Z.a1b2c3-" + MERGE,
            "--if-match", "15",
        ])

    def test_evidence_flag_is_not_commit(self):
        argv = kc.close_argv(META, MERGE, revision=1)
        self.assertNotIn("--commit", argv)
        self.assertIn("--evidence", argv)

    def test_key_depends_only_on_immutable_facts(self):
        a = kc.idempotency_key(META, MERGE)
        b = kc.idempotency_key(dict(META), MERGE)
        self.assertEqual(a, b)

    def test_took_shapes(self):
        self.assertEqual(kc.fmt_took(45), " took 45s")
        self.assertEqual(kc.fmt_took(252), " took 4m12s")
        self.assertEqual(kc.fmt_took(3900), " took 1h5m")
        self.assertEqual(kc.fmt_took(None), "")

    def test_no_close_comment(self):
        argv = kc.no_close_comment_argv(META, MERGE, took_seconds=45)
        self.assertEqual(argv[:2], ["comment", "q4av"])
        self.assertEqual(
            argv[3],
            "repoman landed %s (attempt 20260921T164500Z.a1b2c3 took 45s); "
            "issue left open per close=false" % MERGE)


class LandedRecord(unittest.TestCase):
    def test_payload_keys_match_triage_acceptance(self):
        # marshalboard/triage.py:732-764 reads exactly these.
        p = kc.landed_payload(META, MERGE, at_epoch=0)
        self.assertEqual(
            sorted(p),
            sorted(["branch", "repo", "landed_sha", "base_sha", "at",
                    "attempt", "host", "worktree", "dispatch_id"]))
        self.assertEqual(p["landed_sha"], MERGE)
        self.assertEqual(p["base_sha"], META["head_sha"])
        self.assertEqual(p["at"], "1970-01-01T00:00:00Z")

    def test_base_sha_is_the_pr_head_not_the_merge(self):
        p = kc.landed_payload(META, MERGE, at_epoch=0)
        self.assertNotEqual(p["base_sha"], p["landed_sha"])


class WaiverAudit(unittest.TestCase):
    def test_waived_landing_alerts(self):
        msg = kc.waived_alert(META, MERGE, "review waived (audited) on bbbbbbbb")
        self.assertIsNotNone(msg)
        self.assertIn("review-waived", msg)
        self.assertIn(MERGE[:8], msg)

    def test_reviewed_landing_does_not(self):
        self.assertIsNone(
            kc.waived_alert(META, MERGE, "review evidence on bbbbbbbb: ok"))
        self.assertIsNone(kc.waived_alert(META, MERGE, None))


class Completion(unittest.TestCase):
    def rec(self, **kw):
        return kc.landing_record(META, 12, MERGE, revision=15,
                                 took_seconds=252, **kw)

    def test_the_record_names_its_repo(self):
        self.assertEqual(self.rec()["repo"], META["repo"])

    def test_unfinished_lists_each_suboperation(self):
        self.assertEqual(kc.unfinished(None),
                         ["closed", "landed_record", "waiver_alerted"])
        self.assertEqual(kc.unfinished(self.rec(closed=True)),
                         ["landed_record", "waiver_alerted"])
        self.assertEqual(
            kc.unfinished(self.rec(closed=True, landed_record=True,
                                   waiver_alerted=True)), [])

    def test_a_closed_issue_with_no_landed_record_is_still_unfinished(self):
        # This is the case "is the issue still open?" cannot see, and it is why
        # the reconciler works from the record instead.
        rec = self.rec(closed=True, landed_record=False, waiver_alerted=True)
        self.assertIn("landed_record", kc.unfinished(rec))

    def test_the_key_is_per_landing_not_per_issue(self):
        # Two pull requests for one issue — which --no-close and multi-repo
        # work both produce — must not overwrite each other's progress.
        self.assertNotEqual(kc.landing_meta_key("r", 12),
                            kc.landing_meta_key("r", 13))
        self.assertEqual(kc.landing_meta_key("jibot-ops", 12),
                         "landing:jibot-ops:12")

    def test_the_key_is_scoped_by_repository(self):
        # Two repositories' pull request #12 on ONE kata issue is the
        # multi-repo case this design exists to serve; an unscoped key would
        # have them overwrite each other's completion record.
        self.assertNotEqual(kc.landing_meta_key("cell-fleet", 12),
                            kc.landing_meta_key("jibot-ops", 12))

    def test_a_no_close_landing_replays_its_comment_not_a_close(self):
        meta = dict(META); meta["close"] = False
        rec = kc.landing_record(meta, 12, MERGE, revision=1, took_seconds=45)
        self.assertEqual(rec["operation"], "comment")
        argv = kc.replay_argv(rec)
        self.assertEqual(argv[0], "comment")
        self.assertNotIn("--done", argv)
        self.assertEqual(argv, kc.no_close_comment_argv(meta, MERGE, 45))

    def test_the_constructor_writes_the_field_the_reconciler_reads(self):
        rec = kc.landing_record(META, 12, MERGE, revision=1,
                                started_at_epoch=1000)
        self.assertEqual(rec["started_at_epoch"], 1000)
        # and the grace actually applies to a record the constructor made
        self.assertEqual(kc.reconcile_targets([rec], 1100), [])

    def test_a_retry_replays_the_persisted_request_verbatim(self):
        rec = self.rec()
        first = kc.close_argv(META, MERGE, revision=15, took_seconds=252)
        self.assertEqual(kc.replay_argv(rec), first)

    def test_a_retry_does_not_re_render_the_message(self):
        # kata fingerprints the key together with the request, so a re-rendered
        # message under the same key can return idempotency_mismatch.
        rec = self.rec()
        rec_later = dict(rec)
        # Even if the elapsed time would render differently now:
        self.assertIn("took 4m12s", kc.replay_argv(rec_later)[4])

    def test_only_a_definite_conflict_changes_the_revision(self):
        rec = self.rec()
        self.assertEqual(kc.replay_argv(rec)[-1], "15")
        rec2 = kc.with_new_revision(rec, 16)
        self.assertEqual(kc.replay_argv(rec2)[-1], "16")
        # and everything else is byte-identical
        self.assertEqual(kc.replay_argv(rec)[:-1], kc.replay_argv(rec2)[:-1])
        # the original record is untouched, so a failed retry can be re-read
        self.assertEqual(rec["revision"], 15)


class Reconciler(unittest.TestCase):
    def rec(self, pr, done, started=0, merged=0):
        r = kc.landing_record(META, pr, MERGE, revision=1,
                              started_at_epoch=started,
                              closed=done, landed_record=done,
                              waiver_alerted=done)
        r["merged_at_epoch"] = merged
        return r

    def test_complete_records_are_skipped(self):
        self.assertEqual(kc.reconcile_targets([self.rec(1, True)], 10**9), [])

    def test_an_in_flight_record_is_left_alone(self):
        # meta_set is last-write-wins, so two writers on one key lose progress.
        r = self.rec(1, False, started=1000)
        self.assertEqual(kc.reconcile_targets([r], 1100), [])
        self.assertEqual(len(kc.reconcile_targets([r], 1000 + 3601)), 1)

    def test_watermark_stops_at_the_first_incomplete_landing(self):
        recs = [self.rec(1, True, merged=100), self.rec(2, False, merged=200),
                self.rec(3, True, merged=300)]
        self.assertEqual(kc.advance_watermark((0, 0), recs), (100, 1))

    def test_watermark_breaks_timestamp_ties_by_pr_number(self):
        recs = [self.rec(5, True, merged=100), self.rec(4, True, merged=100)]
        self.assertEqual(kc.advance_watermark((0, 0), recs), (100, 5))

    def test_watermark_does_not_move_when_the_first_is_unfinished(self):
        recs = [self.rec(1, False, merged=100), self.rec(2, True, merged=200)]
        self.assertEqual(kc.advance_watermark((50, 0), recs), (50, 0))


if __name__ == "__main__":
    unittest.main()
