"""Unit tests for pr_meta (kata jibot-code#q4av). Run: python3 -m unittest."""
import unittest

import pr_meta


GOOD = """This lands the thing.

kata: jibot-code#q4av
repo: jibot-ops
branch: q4av-jibot-ops-pr-canary
attempt: 20260921T164500Z.a1b2c3
host: macct
worktree: /Users/joi/repos/.worktrees/q4av-jibot-ops-pr-canary
dispatch_id: macct-1789973004-96015

review: fresheyes --gpt pass=2 blocker=0 substantive=0 fixed=18 rebutted=1 oid=""" + "a" * 40


class ParseBody(unittest.TestCase):
    def test_good_body(self):
        meta, err = pr_meta.parse_body(GOOD)
        self.assertIsNone(err)
        self.assertEqual(meta["project"], "jibot-code")
        self.assertEqual(meta["ref"], "q4av")
        self.assertEqual(meta["repo"], "jibot-ops")
        self.assertEqual(meta["attempt"], "20260921T164500Z.a1b2c3")
        self.assertEqual(meta["host"], "macct")
        self.assertEqual(meta["dispatch_id"], "macct-1789973004-96015")
        self.assertTrue(meta["close"])
        self.assertTrue(pr_meta.closes_an_issue(meta))

    def test_prose_around_the_keys_is_ignored(self):
        meta, err = pr_meta.parse_body(
            "Some prose.\nNot a key: but colon-ish\n" + GOOD)
        self.assertIsNone(err)
        self.assertEqual(meta["ref"], "q4av")

    def test_no_kata_line_closes_nothing_and_is_not_an_error(self):
        meta, err = pr_meta.parse_body("just a pull request\n")
        self.assertIsNone(err)
        self.assertFalse(pr_meta.closes_an_issue(meta))

    def test_duplicate_key_is_malformed(self):
        # review.py:82-93 makes the same call for a second `review:` line:
        # ambiguity is malformed, never "first one wins".
        meta, err = pr_meta.parse_body(GOOD + "\nkata: jibot-code#zzzz\n")
        self.assertIsNone(meta)
        self.assertIn("duplicate key", err)

    def test_issue_without_the_close_fields_is_refused(self):
        meta, err = pr_meta.parse_body("kata: jibot-code#q4av\n")
        self.assertIsNone(meta)
        self.assertIn("no repo", err)

    def test_bad_attempt_shapes(self):
        for bad in ("20260921T164500Z.A1B2C3",   # hex must be lowercase
                    "20260921T164500Z.a1b2c",    # five hex digits
                    "20260921164500Z.a1b2c3",    # no T
                    "20260921T164500Z-a1b2c3"):  # dash not dot
            body = GOOD.replace("20260921T164500Z.a1b2c3", bad)
            meta, err = pr_meta.parse_body(body)
            self.assertIsNone(meta, bad)
            self.assertIn("attempt", err)

    def test_branch_rules(self):
        for bad in ("-leading-dash", "has..dots", "trailing/",
                    "spaces here"):
            body = GOOD.replace("q4av-jibot-ops-pr-canary", bad)
            meta, err = pr_meta.parse_body(body)
            self.assertIsNone(meta, bad)
            self.assertIn("branch", err)

    def test_slug_must_carry_an_alnum(self):
        body = GOOD.replace("repo: jibot-ops", "repo: ---")
        meta, err = pr_meta.parse_body(body)
        self.assertIsNone(meta)
        self.assertIn("repo", err)

    def test_worktree_must_be_absolute(self):
        body = GOOD.replace("worktree: /Users/joi", "worktree: Users/joi")
        meta, err = pr_meta.parse_body(body)
        self.assertIsNone(meta)
        self.assertIn("worktree", err)

    def test_close_false(self):
        meta, err = pr_meta.parse_body(GOOD + "\nclose: false\n")
        self.assertIsNone(err)
        self.assertFalse(meta["close"])

    def test_close_garbage_is_refused_rather_than_defaulted(self):
        meta, err = pr_meta.parse_body(GOOD + "\nclose: maybe\n")
        self.assertIsNone(meta)
        self.assertIn("close", err)

    def test_dispatch_id_dash_means_absent(self):
        body = GOOD.replace("dispatch_id: macct-1789973004-96015",
                            "dispatch_id: -")
        meta, err = pr_meta.parse_body(body)
        self.assertIsNone(err)
        self.assertNotIn("dispatch_id", meta)

    def test_non_text_body(self):
        meta, err = pr_meta.parse_body(None)
        self.assertIsNone(meta)
        self.assertIn("not text", err)


if __name__ == "__main__":
    unittest.main()
