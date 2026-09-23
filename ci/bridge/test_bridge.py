"""Tests for the review-evidence bridge. No network, no key, no kata.

Run: python3 -m unittest discover -s ci/bridge -t ci/bridge
"""

import base64
import io
import json
import os
import stat
import tempfile
import unittest
import urllib.error

import app_token
import review_evidence as rev

REPO = "ito-works/canary-ops"
APP = 5020232
SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_G = "c" * 40
BODY = "\n".join(["kata: jibot-code#q4av", "repo: canary-ops", "branch: feat",
                  "attempt: 20260921T120000Z.abcdef"])


class FakeGitHub:
    """An opener: maps (method, path-with-query-prefix) to a JSON document."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, req, timeout=None):
        method = req.get_method()
        url = req.full_url.replace(rev.API, "")
        self.calls.append((method, url, req.data, req.headers.get("Authorization")))
        for (m, prefix), doc in self.routes.items():
            if m == method and url.startswith(prefix):
                if isinstance(doc, int):
                    raise urllib.error.HTTPError(url, doc, "x", {}, io.BytesIO(b""))
                return _Resp(doc(url) if callable(doc) else doc)
        raise AssertionError("unrouted %s %s" % (method, url))


class _Resp(io.BytesIO):
    def __init__(self, doc):
        super().__init__(json.dumps(doc).encode())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def pr_doc(number=7, head=SHA_A, body=BODY, state="open"):
    return {"number": number, "head": {"sha": head}, "body": body, "state": state}


def check_run(conclusion="success", app=APP, name=rev.CHECK_NAME, rid=1,
              status="completed"):
    return {"id": rid, "name": name, "status": status, "conclusion": conclusion,
            "app": {"id": app}}


class Runner:
    """Stands in for kata and review.py: returns the review.py exit code."""

    def __init__(self, review_rc, kata_rc=0):
        self.review_rc, self.kata_rc, self.argv = review_rc, kata_rc, []

    def __call__(self, cmd, stdin=None):
        self.argv.append(cmd)
        rc = self.kata_rc if cmd[1] == "show" else self.review_rc

        class R:
            returncode = rc
            stdout = "{}" if cmd[1] == "show" else "review: fresheyes --gpt pass=1"
            stderr = ""
        return R()


def env(**kw):
    e = {"GITHUB_REPOSITORY": REPO, "GITHUB_TOKEN": "read-token",
         "MUJIN_APP_ID": str(APP), "MUJIN_APP_INSTALLATION_ID": "163475648",
         "KATA_BIN": "/nonexistent/kata"}
    e.update(kw)
    return e


def published(gh):
    posts = [c for c in gh.calls if c[0] == "POST" and c[1].endswith("/check-runs")]
    return [json.loads(p[2]) for p in posts], [p[3] for p in posts]


class PullRequestPath(unittest.TestCase):
    def run_pr(self, review_rc, pr=None, event_head=SHA_A, kata_rc=0):
        gh = FakeGitHub({("GET", "/repos/%s/pulls/7" % REPO): pr or pr_doc(),
                         ("POST", "/repos/%s/check-runs" % REPO): {"id": 99}})
        runner = Runner(review_rc, kata_rc)
        rc = rev.main(env(BRIDGE_EVENT="pull_request_target", BRIDGE_PR_NUMBER="7",
                          BRIDGE_PR_HEAD_SHA=event_head),
                      opener=gh, runner=runner, mint=lambda *a: "app-token")
        return rc, gh, runner

    def test_valid_evidence_publishes_success_on_the_snapshot_head_with_the_app_token(self):
        rc, gh, runner = self.run_pr(0)
        self.assertEqual(rc, 0)
        bodies, auth = published(gh)
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0]["head_sha"], SHA_A)
        self.assertEqual(bodies[0]["conclusion"], "success")
        self.assertEqual(bodies[0]["name"], "mujin/review-evidence")
        self.assertEqual(auth, ["Bearer app-token"])
        # kata and review.py were asked about THIS issue and THIS head.
        self.assertEqual(runner.argv[0][:5],
                         ["/nonexistent/kata", "show", "q4av", "--project", "jibot-code"])
        self.assertEqual(runner.argv[1][-2:], ["check-show-json", SHA_A])

    def test_the_read_token_never_publishes(self):
        _rc, gh, _ = self.run_pr(0)
        for method, _url, _data, auth in gh.calls:
            if method == "POST":
                self.assertNotEqual(auth, "Bearer read-token")

    def test_missing_stale_and_malformed_evidence_publish_failure(self):
        for review_rc in (3, 4):
            rc, gh, _ = self.run_pr(review_rc)
            self.assertEqual(rc, 0)
            self.assertEqual(published(gh)[0][0]["conclusion"], "failure")

    def test_every_waiver_is_refused_while_no_author_is_resolved(self):
        rc, gh, _ = self.run_pr(6)
        self.assertEqual(rc, 0)
        body = published(gh)[0][0]
        self.assertEqual(body["conclusion"], "failure")
        self.assertIn("could not be identified", body["output"]["summary"])

    def test_unreadable_kata_publishes_failure_not_success(self):
        rc, gh, _ = self.run_pr(0, kata_rc=7)
        self.assertEqual(rc, 0)
        self.assertEqual(published(gh)[0][0]["conclusion"], "failure")

    def test_a_body_with_no_kata_ref_fails(self):
        rc, gh, runner = self.run_pr(0, pr=pr_doc(body="just prose"))
        self.assertEqual(published(gh)[0][0]["conclusion"], "failure")
        self.assertEqual(runner.argv, [])

    def test_a_hostile_body_never_reaches_an_argv(self):
        rc, gh, runner = self.run_pr(
            0, pr=pr_doc(body=BODY.replace("jibot-code#q4av", "x#y; rm -rf ~")))
        self.assertEqual(published(gh)[0][0]["conclusion"], "failure")
        self.assertEqual(runner.argv, [])

    def test_a_stale_event_publishes_nothing(self):
        rc, gh, runner = self.run_pr(0, event_head=SHA_B)
        self.assertEqual(rc, 0)
        self.assertEqual(published(gh)[0], [])
        self.assertEqual(runner.argv, [])

    def test_a_closed_pull_request_publishes_nothing(self):
        rc, gh, _ = self.run_pr(0, pr=pr_doc(state="closed"))
        self.assertEqual(published(gh)[0], [])

    def test_an_unreachable_github_exits_1_and_publishes_nothing(self):
        gh = FakeGitHub({("GET", "/repos/%s/pulls/7" % REPO): 502})
        rc = rev.main(env(BRIDGE_EVENT="workflow_dispatch", BRIDGE_PR_NUMBER="7"),
                      opener=gh, runner=Runner(0), mint=lambda *a: "t")
        self.assertEqual(rc, 1)
        self.assertEqual(published(gh)[0], [])

    def test_a_token_failure_exits_1(self):
        def mint(*a):
            raise app_token.TokenError("no key")
        gh = FakeGitHub({("GET", "/repos/%s/pulls/7" % REPO): pr_doc()})
        rc = rev.main(env(BRIDGE_EVENT="workflow_dispatch", BRIDGE_PR_NUMBER="7"),
                      opener=gh, runner=Runner(0), mint=mint)
        self.assertEqual(rc, 1)

    def test_an_unknown_trigger_exits_1(self):
        rc = rev.main(env(BRIDGE_EVENT="pull_request"), opener=FakeGitHub({}),
                      runner=Runner(0), mint=lambda *a: "t")
        self.assertEqual(rc, 1)


class GroupRun:
    """The merge-group harness, shared by the classes below without their tests."""

    QUEUE_REF = "refs/heads/gh-readonly-queue/main/pr-7-" + "d" * 40

    BASE = "f" * 40

    def run_group(self, entries, runs_by_head, n_commits=None, queue_ref=None,
                  graphql=None, base_oid=BASE, merge_method="REBASE", commits=None,
                  rules=None):
        """entries: [(position, group_sha, pr_number, pr_head, pr_commit_count)]"""
        nodes = [{"position": pos, "state": "AWAITING_CHECKS",
                  "headCommit": {"oid": gsha}, "baseCommit": {"oid": base_oid},
                  "pullRequest": {"number": num, "headRefOid": head,
                                  "commits": {"totalCount": n}}}
                 for pos, gsha, num, head, n in entries]
        if n_commits is None:
            n_commits = sum(e[4] for e in entries)
        if commits is None:
            commits = [{"sha": "%040d" % i} for i in range(n_commits)]
        queue = {"entries": {"nodes": nodes}}
        if rules is None:
            rules = [{"type": "required_status_checks", "parameters": {}},
                     {"type": "merge_queue",
                      "parameters": {"merge_method": merge_method,
                                     "grouping_strategy": "ALLGREEN"}}]
        routes = {
            # The method comes from the branch's rules, never from GraphQL
            # `configuration` (the workflow token is refused that field).
            ("GET", "/repos/%s/rules/branches/main" % REPO): rules,
            ("POST", "/graphql"): graphql if graphql is not None else {"data": {
                "repository": {"mergeQueue": queue}}},
            # The comparison is against the entry's immutable base commit. A
            # request that names the BRANCH is unrouted and fails the test.
            ("GET", "/repos/%s/compare/%s...%s" % (REPO, self.BASE, SHA_G)): {
                "commits": commits},
            ("POST", "/repos/%s/check-runs" % REPO): {"id": 5},
        }
        for head, runs in runs_by_head.items():
            routes[("GET", "/repos/%s/commits/%s/check-runs" % (REPO, head))] = (
                runs if isinstance(runs, int) else {"check_runs": runs})
        gh = FakeGitHub(routes)
        rc = rev.main(env(BRIDGE_EVENT="merge_group", BRIDGE_GROUP_SHA=SHA_G,
                          BRIDGE_QUEUE_REF=queue_ref or self.QUEUE_REF),
                      opener=gh, mint=lambda *a: "app-token")
        bodies, _ = published(gh)
        return rc, bodies, gh


class MergeGroupPath(GroupRun, unittest.TestCase):
    def test_a_reviewed_member_publishes_success_on_the_group_commit(self):
        rc, bodies, gh = self.run_group([(1, SHA_G, 7, SHA_A, 2)], {SHA_A: [check_run()]})
        self.assertEqual(rc, 0)
        self.assertEqual(bodies[0]["head_sha"], SHA_G)
        self.assertEqual(bodies[0]["conclusion"], "success")
        # The lookup itself is filtered by the App.
        self.assertTrue(any("app_id=%d" % APP in c[1] for c in gh.calls))

    def test_a_check_run_by_another_app_is_absent(self):
        _rc, bodies, _ = self.run_group([(1, SHA_G, 7, SHA_A, 1)],
                                        {SHA_A: [check_run(app=15368)]})  # GitHub Actions
        self.assertEqual(bodies[0]["conclusion"], "failure")
        self.assertIn("no mujin/review-evidence check by the App",
                      bodies[0]["output"]["summary"])

    def test_the_latest_run_decides_not_an_older_success(self):
        _rc, bodies, _ = self.run_group(
            [(1, SHA_G, 7, SHA_A, 1)],
            {SHA_A: [check_run("success", rid=1), check_run("failure", rid=2)]})
        self.assertEqual(bodies[0]["conclusion"], "failure")

    def test_a_commit_nobody_owns_fails_the_group(self):
        # The member's pull request holds one commit; the group adds two.
        _rc, bodies, _ = self.run_group([(1, SHA_G, 7, SHA_A, 1)],
                                        {SHA_A: [check_run()]}, n_commits=2)
        self.assertEqual(bodies[0]["conclusion"], "failure")
        self.assertIn("2 commits added, members hold 1", bodies[0]["output"]["summary"])

    def test_every_entry_ahead_in_the_queue_is_a_member_and_must_be_reviewed(self):
        _rc, bodies, _ = self.run_group(
            [(1, "9" * 40, 6, SHA_B, 1), (2, SHA_G, 7, SHA_A, 1)],
            {SHA_A: [check_run()], SHA_B: []})
        self.assertEqual(bodies[0]["conclusion"], "failure")
        self.assertIn("#6", bodies[0]["output"]["summary"])

    def test_an_entry_behind_in_the_queue_is_not_a_member(self):
        _rc, bodies, _ = self.run_group(
            [(1, SHA_G, 7, SHA_A, 1), (2, "9" * 40, 8, SHA_B, 1)],
            {SHA_A: [check_run()]}, n_commits=1)
        self.assertEqual(bodies[0]["conclusion"], "success")
        self.assertNotIn("#8", bodies[0]["output"]["summary"])

    def test_the_base_is_the_entrys_own_commit_never_the_moving_branch(self):
        rc, bodies, gh = self.run_group([(1, SHA_G, 7, SHA_A, 1)], {SHA_A: [check_run()]})
        urls = [c[1] for c in gh.calls]
        self.assertTrue(any("/compare/%s..." % self.BASE in u for u in urls))
        self.assertFalse(any("/compare/main..." in u for u in urls))

    def test_an_entry_with_no_base_commit_publishes_nothing(self):
        rc, bodies, _ = self.run_group([(1, SHA_G, 7, SHA_A, 1)], {SHA_A: [check_run()]},
                                       base_oid=None)
        self.assertEqual((rc, bodies), (1, []))

    def test_the_queue_ref_must_name_the_LAST_member_not_just_any_member(self):
        _rc, bodies, _ = self.run_group(
            [(1, "9" * 40, 6, SHA_B, 1), (2, SHA_G, 7, SHA_A, 1)],
            {SHA_A: [check_run()], SHA_B: [check_run()]},
            queue_ref="refs/heads/gh-readonly-queue/main/pr-6-" + "d" * 40)
        self.assertEqual(bodies[0]["conclusion"], "failure")
        self.assertIn("does not name this group's last pull request (#7)",
                      bodies[0]["output"]["summary"])

    def test_a_queue_ref_for_another_base_branch_fails(self):
        _rc, bodies, _ = self.run_group(
            [(1, SHA_G, 7, SHA_A, 1)], {SHA_A: [check_run()]},
            queue_ref="refs/heads/gh-readonly-queue/release/pr-7-" + "d" * 40)
        self.assertEqual(bodies[0]["conclusion"], "failure")

    def test_a_queue_ref_that_disagrees_with_the_entries_fails(self):
        _rc, bodies, _ = self.run_group(
            [(1, SHA_G, 7, SHA_A, 1)], {SHA_A: [check_run()]},
            queue_ref="refs/heads/gh-readonly-queue/main/pr-9-" + "d" * 40)
        self.assertEqual(bodies[0]["conclusion"], "failure")

    def test_a_commit_that_heads_no_queue_entry_publishes_nothing(self):
        rc, bodies, _ = self.run_group([(1, "9" * 40, 7, SHA_A, 1)], {SHA_A: [check_run()]})
        self.assertEqual((rc, bodies), (1, []))

    def test_a_graphql_error_publishes_nothing(self):
        rc, bodies, _ = self.run_group([], {}, graphql={"errors": [{"message": "x"}]})
        self.assertEqual((rc, bodies), (1, []))

    def test_an_api_error_mid_resolution_publishes_nothing(self):
        rc, bodies, _ = self.run_group([(1, SHA_G, 7, SHA_A, 1)], {SHA_A: 500})
        self.assertEqual(rc, 1)
        self.assertEqual(bodies, [])


def commit(sha, *parents):
    return {"sha": sha, "parents": [{"sha": p} for p in parents]}


class MergeMethodMergeGroup(GroupRun, unittest.TestCase):
    """The queue merges with MERGE: one two-parent merge per member, chained."""

    ENTRY = [(1, SHA_G, 7, SHA_A, 2)]
    PR_OWN = [commit("1" * 40, GroupRun.BASE), commit(SHA_A, "1" * 40)]

    def merge_group(self, commits, entries=None, runs=None):
        return self.run_group(entries or self.ENTRY, runs or {SHA_A: [check_run()]},
                              merge_method="MERGE", commits=commits)

    def assert_fails(self, bodies, text):
        self.assertEqual(bodies[0]["conclusion"], "failure")
        self.assertIn("does not match its queue entries", bodies[0]["output"]["summary"])
        self.assertIn(text, bodies[0]["output"]["summary"])

    def test_one_member_merged_onto_the_base_publishes_success(self):
        rc, bodies, _ = self.merge_group(self.PR_OWN + [commit(SHA_G, self.BASE, SHA_A)])
        self.assertEqual(rc, 0)
        self.assertEqual(bodies[0]["head_sha"], SHA_G)
        self.assertEqual(bodies[0]["conclusion"], "success")

    def test_two_members_chained_in_queue_order_publish_success(self):
        m1 = "9" * 40
        commits = [commit("1" * 40, self.BASE), commit(SHA_B, "1" * 40),
                   commit(m1, self.BASE, SHA_B),
                   commit(SHA_A, self.BASE), commit(SHA_G, m1, SHA_A)]
        _rc, bodies, _ = self.merge_group(
            commits, entries=[(1, m1, 6, SHA_B, 2), (2, SHA_G, 7, SHA_A, 1)],
            runs={SHA_A: [check_run()], SHA_B: [check_run()]})
        self.assertEqual(bodies[0]["conclusion"], "success")
        self.assertIn("#6@", bodies[0]["output"]["summary"])

    def test_members_merged_out_of_queue_order_fail(self):
        m1 = "9" * 40
        commits = [commit("1" * 40, self.BASE), commit(SHA_B, "1" * 40),
                   commit(m1, self.BASE, SHA_A),
                   commit(SHA_A, self.BASE), commit(SHA_G, m1, SHA_B)]
        _rc, bodies, _ = self.merge_group(
            commits, entries=[(1, m1, 6, SHA_B, 2), (2, SHA_G, 7, SHA_A, 1)],
            runs={SHA_A: [check_run()], SHA_B: [check_run()]})
        self.assert_fails(bodies, "not pull request #7's head")

    def test_the_rebase_count_is_short_one_merge_and_fails(self):
        _rc, bodies, _ = self.merge_group(self.PR_OWN)
        self.assert_fails(bodies, "2 commits added, members hold 2 plus 1 merge commits")

    def test_a_valid_chain_with_a_commit_the_member_does_not_hold_fails_on_the_count(self):
        # The chain is intact; the pull request says it holds ONE commit but
        # the group adds two under its head. Only the count catches this.
        _rc, bodies, _ = self.merge_group(self.PR_OWN + [commit(SHA_G, self.BASE, SHA_A)],
                                          entries=[(1, SHA_G, 7, SHA_A, 1)])
        self.assert_fails(bodies, "3 commits added, members hold 1 plus 1 merge commits")

    def test_an_extra_non_merge_commit_in_place_of_the_merge_fails(self):
        _rc, bodies, _ = self.merge_group(self.PR_OWN + [commit(SHA_G, SHA_A)])
        self.assert_fails(bodies, "is not a two-parent merge for pull request #7")

    def test_a_merge_of_something_that_is_not_the_member_head_fails(self):
        _rc, bodies, _ = self.merge_group(
            self.PR_OWN[:1] + [commit("2" * 40, "1" * 40),
                               commit(SHA_G, self.BASE, "2" * 40)])
        self.assert_fails(bodies, "merges 22222222, not pull request #7's head")

    def test_a_merge_with_its_parents_swapped_fails(self):
        _rc, bodies, _ = self.merge_group(self.PR_OWN + [commit(SHA_G, SHA_A, self.BASE)])
        self.assert_fails(bodies, "not pull request #7's head")

    def test_an_octopus_merge_fails(self):
        _rc, bodies, _ = self.merge_group(
            self.PR_OWN[:1] + [commit(SHA_A, "1" * 40),
                               commit(SHA_G, self.BASE, SHA_A, "1" * 40)])
        self.assert_fails(bodies, "is not a two-parent merge")

    def test_a_chain_that_does_not_end_at_the_base_fails(self):
        other = "3" * 40
        _rc, bodies, _ = self.merge_group(self.PR_OWN + [commit(SHA_G, other, SHA_A)])
        self.assert_fails(bodies, "ends at 33333333, not the base")

    def test_a_group_commit_the_compare_does_not_list_fails(self):
        _rc, bodies, _ = self.merge_group(
            self.PR_OWN + [commit("4" * 40, self.BASE, SHA_A)])
        self.assert_fails(bodies, "is not a commit the group adds")

    def test_a_malformed_parent_list_fails_rather_than_crashing(self):
        bad = {"sha": SHA_G, "parents": [{"sha": self.BASE}, "not-a-dict"]}
        rc, bodies, _ = self.merge_group(self.PR_OWN + [bad])
        self.assertEqual(rc, 0)
        self.assert_fails(bodies, "is not a two-parent merge")

    def test_a_parent_list_that_is_not_a_list_fails_rather_than_crashing(self):
        for raw in (7, True, None, "ab", {"sha": self.BASE}):
            bad = {"sha": SHA_G, "parents": raw}
            rc, bodies, _ = self.merge_group(self.PR_OWN + [bad])
            self.assertEqual(rc, 0, raw)
            self.assert_fails(bodies, "is not a two-parent merge")

    def test_a_compare_entry_with_no_sha_fails(self):
        _rc, bodies, _ = self.merge_group(
            [{"parents": []}, commit(SHA_A, "1" * 40), commit(SHA_G, self.BASE, SHA_A)])
        self.assert_fails(bodies, "a commit with no sha")

    def test_the_merge_count_does_not_excuse_an_unreviewed_member(self):
        _rc, bodies, _ = self.merge_group(self.PR_OWN + [commit(SHA_G, self.BASE, SHA_A)],
                                          runs={SHA_A: []})
        self.assertEqual(bodies[0]["conclusion"], "failure")
        self.assertIn("no mujin/review-evidence check by the App",
                      bodies[0]["output"]["summary"])


class MergeMethodIsReadFromTheQueue(GroupRun, unittest.TestCase):
    def test_rebase_is_read_and_keeps_the_plain_count(self):
        _rc, bodies, gh = self.run_group([(1, SHA_G, 7, SHA_A, 2)], {SHA_A: [check_run()]},
                                         merge_method="REBASE")
        self.assertEqual(bodies[0]["conclusion"], "success")
        query = json.loads([c for c in gh.calls if c[1] == "/graphql"][0][2])["query"]
        self.assertNotIn("configuration", query)
        self.assertTrue(any(c[1].startswith("/repos/%s/rules/branches/main" % REPO)
                            for c in gh.calls))

    def test_rebase_does_not_accept_the_merge_shape(self):
        _rc, bodies, _ = self.run_group([(1, SHA_G, 7, SHA_A, 1)], {SHA_A: [check_run()]},
                                        merge_method="REBASE", n_commits=2)
        self.assertEqual(bodies[0]["conclusion"], "failure")
        self.assertIn("2 commits added, members hold 1", bodies[0]["output"]["summary"])

    def test_squash_is_not_verified_and_fails(self):
        _rc, bodies, _ = self.run_group([(1, SHA_G, 7, SHA_A, 1)], {SHA_A: [check_run()]},
                                        merge_method="SQUASH")
        self.assertEqual(bodies[0]["conclusion"], "failure")
        self.assertIn("merge method SQUASH is not one this check verifies",
                      bodies[0]["output"]["summary"])

    def test_an_unknown_method_is_not_verified_and_fails(self):
        _rc, bodies, _ = self.run_group([(1, SHA_G, 7, SHA_A, 1)], {SHA_A: [check_run()]},
                                        merge_method="FAST_FORWARD")
        self.assertEqual(bodies[0]["conclusion"], "failure")
        self.assertIn("merge method FAST_FORWARD is not one this check verifies",
                      bodies[0]["output"]["summary"])

    def test_rules_that_name_no_single_method_publish_nothing(self):
        # An otherwise valid, reviewed group: only the rules are wrong.
        mq = lambda params: {"type": "merge_queue", "parameters": params}
        for rules in ([], [{"type": "deletion"}],
                      [{"type": "merge_queue"}], [mq(None)], [mq({})],
                      [mq({"merge_method": None})], [mq({"merge_method": 7})],
                      [mq({"merge_method": ["MERGE"]})],
                      [mq({"merge_method": "REBASE"}), mq({"merge_method": "MERGE"})],
                      [mq({"merge_method": "REBASE"}), mq({})],
                      # A valid rule does not excuse a malformed neighbour.
                      [mq({"merge_method": "REBASE"}), None],
                      [mq({"merge_method": "REBASE"}), {}],
                      [mq({"merge_method": "REBASE"}), "merge_queue"],
                      [mq({"merge_method": "REBASE"}),
                       {"type": ["merge_queue"], "parameters": {"merge_method": "MERGE"}}],
                      {"message": "Not Found"}):
            rc, bodies, _ = self.run_group([(1, SHA_G, 7, SHA_A, 1)],
                                           {SHA_A: [check_run()]}, rules=rules)
            self.assertEqual((rc, bodies), (1, []), rules)

    def test_two_rulesets_that_agree_on_the_method_are_one_method(self):
        rules = [{"type": "merge_queue", "parameters": {"merge_method": "REBASE"}},
                 {"type": "merge_queue", "parameters": {"merge_method": "REBASE"}}]
        _rc, bodies, _ = self.run_group([(1, SHA_G, 7, SHA_A, 1)],
                                        {SHA_A: [check_run()]}, rules=rules)
        self.assertEqual(bodies[0]["conclusion"], "success")

    def test_an_unreadable_rules_endpoint_publishes_nothing(self):
        rc, bodies, _ = self.run_group([(1, SHA_G, 7, SHA_A, 1)],
                                       {SHA_A: [check_run()]}, rules=403)
        self.assertEqual((rc, bodies), (1, []))


class KataToken(unittest.TestCase):
    def test_a_0600_file_is_loaded_into_the_child_environment_and_stripped(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("tok-123\n")
        os.chmod(fh.name, 0o600)
        environ = {}
        rev.load_kata_token(fh.name, environ)
        os.unlink(fh.name)
        self.assertEqual(environ, {"KATA_AUTH_TOKEN": "tok-123"})

    def test_a_file_other_accounts_can_read_is_refused(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("tok\n")
        os.chmod(fh.name, 0o640)
        with self.assertRaises(rev.BridgeError) as ctx:
            rev.load_kata_token(fh.name, {})
        os.unlink(fh.name)
        self.assertIn("must be 0600", str(ctx.exception))

    def test_a_missing_or_empty_file_is_refused(self):
        with self.assertRaises(rev.BridgeError):
            rev.load_kata_token("/nonexistent/kata-token", {})
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("  \n")
        os.chmod(fh.name, 0o600)
        with self.assertRaises(rev.BridgeError):
            rev.load_kata_token(fh.name, {})
        os.unlink(fh.name)


class AppToken(unittest.TestCase):
    def test_the_jwt_names_the_app_and_expires_inside_ten_minutes(self):
        jwt = app_token.build_jwt(APP, "/unused", now=1000, sign=lambda m: b"sig")
        header, payload, sig = jwt.split(".")
        pad = lambda s: s + "=" * (-len(s) % 4)
        self.assertEqual(json.loads(base64.urlsafe_b64decode(pad(header)))["alg"], "RS256")
        claims = json.loads(base64.urlsafe_b64decode(pad(payload)))
        self.assertEqual(claims, {"iat": 940, "exp": 1540, "iss": str(APP)})
        self.assertEqual(base64.urlsafe_b64decode(pad(sig)), b"sig")

    def test_a_key_other_accounts_can_read_is_refused(self):
        with tempfile.NamedTemporaryFile() as fh:
            os.chmod(fh.name, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
            with self.assertRaises(app_token.TokenError) as ctx:
                app_token.build_jwt(APP, fh.name)
            self.assertIn("must be 0600", str(ctx.exception))

    def test_a_missing_key_is_a_token_error(self):
        with self.assertRaises(app_token.TokenError):
            app_token.build_jwt(APP, "/nonexistent/key.pem")

    def test_the_token_is_scoped_to_one_repo_and_checks_write(self):
        seen = {}

        def opener(req, timeout=None):
            seen["body"] = json.loads(req.data)
            seen["url"] = req.full_url
            return _Resp({"token": "ghs_x"})
        orig = app_token.build_jwt
        app_token.build_jwt = lambda *a, **k: "jwt"
        try:
            tok = app_token.installation_token(APP, 163475648, "canary-ops",
                                               opener=opener)
        finally:
            app_token.build_jwt = orig
        self.assertEqual(tok, "ghs_x")
        self.assertEqual(seen["body"], {"repositories": ["canary-ops"],
                                        "permissions": {"checks": "write"}})
        self.assertTrue(seen["url"].endswith("/app/installations/163475648/access_tokens"))


if __name__ == "__main__":
    unittest.main()
