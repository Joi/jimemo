"""Structural tests for the workflow files (kata jibot-code#q4av).

Adapted from jibot-ops 8f46f7e (kata jibot-code#bden). This repo's parts are
`HOSTED`, `HostedWorkflows`, `Runners` and `ThisReposGate` (jimemo's gate is
pytest alone on a GitHub-hosted runner, so no warm step, no store and no
isolation check); everything else is the source's, unchanged.

Stdlib only, on purpose: these run as plain python3 from a clean checkout,
with nothing installed beyond pytest, so PyYAML is not available and must
not be assumed. These are
text assertions with explicit patterns rather than a YAML parse — narrower, but
they hold in the environment the gate actually runs in, and a YAML parse would
not have caught any of what they check anyway.

The one that matters most is `SecurityInvariant`: no workflow that mentions
`secrets.` may trigger on `pull_request:` or `merge_group:`, because those two
events run the workflow file FROM THE PULL REQUEST. That is the whole trust
split of this design, and it is here as a test rather than as a paragraph in a
spec because a paragraph cannot fail a gate.

Run: python3 -m unittest discover -s ci/scripts -t ci/scripts
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
WF_DIR = os.path.join(REPO, ".github", "workflows")

# Every workflow sits on one side of the trust split, and the two sets are
# asserted exactly: adding a workflow without deciding which side it is on
# fails the gate.
#
# UNTRUSTED runs the pull request's own copy of its YAML (pull_request,
# merge_group), on a GitHub-hosted runner, and holds nothing.
# TRUSTED runs main's copy (pull_request_target, workflow_dispatch,
# workflow_run, push, schedule), on the mujin-bridge account, and may hold a
# secret — so it must never check out or execute anything a pull request wrote.
UNTRUSTED = {"gate.yml"}
TRUSTED = {"review-evidence.yml", "kata-bridge.yml"}
EXPECTED = UNTRUSTED | TRUSTED
# This repo's workflows from before the PR lane. They are OUTSIDE the trust
# split: GitHub-hosted runners, GitHub-stored secrets, nothing of the fleet's.
# Named exactly, so a new workflow still has to be put on one side or the
# other, and held to the one rule that keeps them outside: they never ask for
# a self-hosted runner.
HOSTED = {"ci.yml"}
TRUSTED_TRIGGERS = {"pull_request_target", "workflow_dispatch", "workflow_run",
                    "push", "schedule"}

CHECK_NAME = "mujin/review-evidence"


def read(name):
    with open(os.path.join(WF_DIR, name)) as fh:
        return fh.read()


def triggers(text):
    """The event names under the top-level `on:` block."""
    out = set()
    in_on = False
    for line in text.splitlines():
        if re.match(r"^on:\s*$", line):
            in_on = True
            continue
        if in_on:
            if line and not line.startswith((" ", "\t", "#")):
                break
            m = re.match(r"^  ([a-z_]+):", line)
            if m:
                out.add(m.group(1))
    return out


class FilesExist(unittest.TestCase):
    def test_every_workflow_is_present_and_non_empty(self):
        # Asserted FIRST and by count: a test that walks a glob would pass
        # vacuously over an empty directory, which is the failure mode of the
        # "yaml ok" check this replaces.
        self.assertTrue(os.path.isdir(WF_DIR), WF_DIR)
        found = {f for f in os.listdir(WF_DIR) if f.endswith(".yml")}
        self.assertEqual(found, EXPECTED | HOSTED)
        for name in EXPECTED:
            self.assertGreater(len(read(name)), 200, name)


class HostedWorkflows(unittest.TestCase):
    def test_a_hosted_workflow_never_asks_for_a_fleet_runner(self):
        for name in sorted(HOSTED):
            text = read(name)
            self.assertNotIn("self-hosted", text, name)
            self.assertNotIn("mujin-", text, name)


class SecurityInvariant(unittest.TestCase):
    """The trust split, as a test."""

    def test_no_secret_bearing_workflow_runs_pull_request_authored_yaml(self):
        for name in sorted(EXPECTED):
            text = read(name)
            if "secrets." not in text:
                continue
            evs = triggers(text)
            self.assertNotIn("pull_request", evs,
                             "%s names secrets. and triggers on pull_request — "
                             "that runs the PULL REQUEST's copy of this file"
                             % name)
            self.assertNotIn("merge_group", evs,
                             "%s names secrets. and triggers on merge_group — "
                             "that runs the PULL REQUEST's copy of this file"
                             % name)

    def test_an_untrusted_workflow_holds_nothing(self):
        for name in sorted(UNTRUSTED):
            text = read(name)
            self.assertNotIn("secrets.", text, name)
            # An environment is how a job is handed a secret.
            self.assertIsNone(re.search(r"^    environment:", text, re.M), name)
            self.assertNotIn("mujin-bridge", text, name)

    def test_no_workflow_names_a_github_secret(self):
        # The bridge's credentials (the App key, the kata token) live on the
        # bridge runner's filesystem. A `secrets.` reference would mean one of
        # them had been copied into GitHub, where a workflow a pull request
        # rewrote is one misconfiguration away from it.
        for name in sorted(EXPECTED):
            self.assertNotIn("secrets.", read(name), name)

    def test_a_trusted_workflow_only_has_triggers_that_run_mains_yaml(self):
        for name in sorted(TRUSTED):
            evs = triggers(read(name))
            self.assertTrue(evs, name)
            self.assertLessEqual(evs, TRUSTED_TRIGGERS,
                                 "%s triggers on %s" % (name, sorted(evs - TRUSTED_TRIGGERS)))

    def test_a_trusted_workflow_runs_on_the_bridge_and_never_on_the_gate_account(self):
        for name in sorted(TRUSTED):
            text = read(name)
            self.assertIn("runs-on: [self-hosted, macOS, mujin-bridge]", text, name)
            self.assertIsNone(re.search(r"runs-on:.*mujin-gate", text), name)

    def test_a_trusted_workflow_never_checks_out_the_pull_request(self):
        # pull_request_target with a checkout of the PR head is the classic
        # way to hand a secret to candidate code.
        for name in sorted(TRUSTED):
            text = read(name)
            for bad in ("pull_request.head.ref", "pull_request.head.repo",
                        "refs/pull/", "github.head_ref"):
                self.assertNotIn(bad, text, "%s mentions %s" % (name, bad))
            refs = re.findall(r"^          ref: (.+)$", text, re.M)
            self.assertEqual(refs, ["${{ github.event.repository.default_branch }}"], name)

    def test_a_trusted_workflow_interpolates_no_event_text_into_a_shell_line(self):
        # `run: echo ${{ github.event.pull_request.title }}` is script
        # injection. Event values travel as env and are read by the program.
        for name in sorted(TRUSTED):
            for line in read(name).splitlines():
                if re.match(r"^\s+run:", line):
                    self.assertNotIn("${{", line, "%s: %s" % (name, line.strip()))

    def test_gate_asks_for_read_only_contents(self):
        text = read("gate.yml")
        self.assertIn("permissions:\n  contents: read\n", text)
        # It must not be able to label, comment or write a status: every
        # PR-facing write belongs to the bridge.
        for perm in ("pull-requests: write", "statuses: write",
                     "issues: write", "contents: write"):
            self.assertNotIn(perm, text)


class OwnerGuard(unittest.TestCase):
    def test_every_job_is_skipped_outside_the_organisation(self):
        # One job per workflow, and its `if:` must START with the owner test:
        # `a || owner` would run everywhere `a` holds.
        for name in sorted(EXPECTED):
            conds = re.findall(r"^    if: (.+)$", read(name), re.M)
            self.assertEqual(len(conds), 1, name)
            self.assertTrue(
                conds[0] == "github.repository_owner == 'ito-works'"
                or conds[0].startswith("github.repository_owner == 'ito-works' && ("),
                "%s: %s" % (name, conds[0]))


class LaneGuard(unittest.TestCase):
    def test_every_workflow_runs_the_lane_guard(self):
        for name in sorted(EXPECTED):
            self.assertIn("ci/lane-guard.sh", read(name), name)

    def test_every_step_after_the_guard_is_guarded(self):
        # A guard is worthless if a later step runs regardless. Count the
        # steps and require the condition on all of them but the checkout and
        # the guard itself — "at least one somewhere" would pass a file whose
        # last step is unguarded, which is the one that would matter.
        for name in sorted(EXPECTED):
            text = read(name)
            steps = len(re.findall(r"^      - (?:name|uses|id):", text, re.M))
            guarded = text.count("steps.lane.outputs.active == 'true'")
            self.assertGreaterEqual(guarded, steps - 2,
                                    "%s: %d steps, %d guarded"
                                    % (name, steps, guarded))


class Triggers(unittest.TestCase):
    def test_gate(self):
        self.assertEqual(triggers(read("gate.yml")),
                         {"pull_request", "merge_group"})


class KataBridgeEntryPoints(unittest.TestCase):
    """The Python tests call main(["dequeued"]) directly, so they would keep
    passing with the trigger deleted or the mode routed wrong. These pin the
    wiring that reaches them."""

    def setUp(self):
        self.text = read("kata-bridge.yml")

    def test_the_three_triggers(self):
        self.assertEqual(triggers(self.text),
                         {"push", "pull_request_target", "workflow_run"})
        self.assertIn("  pull_request_target:\n    types: [dequeued]\n", self.text)
        self.assertIn("  push:\n    branches: [main]\n", self.text)
        self.assertIn("  workflow_run:\n    workflows: [gate]\n    types: [completed]\n",
                      self.text)

    def test_each_trigger_routes_to_its_own_mode(self):
        self.assertIn(
            "BRIDGE_MODE: ${{ github.event_name == 'push' && 'landed' || "
            "(github.event_name == 'pull_request_target' && 'dequeued' || 'outcome') }}",
            self.text)
        self.assertIn('run: python3 ci/bridge/kata_bridge.py "$BRIDGE_MODE"', self.text)

    def test_the_dequeue_event_reaches_the_program_as_environment(self):
        for binding in (
                "BRIDGE_PR_NUMBER: ${{ github.event.pull_request.number }}",
                "BRIDGE_PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}",
                "BRIDGE_DEQUEUE_REASON: ${{ github.event.reason }}"):
            self.assertIn(binding, self.text)

    def test_only_the_dequeued_activity_is_accepted(self):
        # pull_request_target with its DEFAULT types (opened, synchronize,
        # reopened) would run this credentialed job on every push to every
        # pull request, routed to a mode that then comments on kata.
        m = re.search(r"  pull_request_target:\n    types: \[([^\]]*)\]", self.text)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).strip(), "dequeued")


class CheckName(unittest.TestCase):
    def test_the_required_check_name_is_pinned(self):
        # The ruleset matches a required check by NAME. Pinned here so the
        # deferred verifier cannot drift from what the ruleset will require.
        with open(os.path.join(HERE, "review_check.py")) as fh:
            src = fh.read()
        self.assertIn('STATUS_CONTEXT = "%s"' % CHECK_NAME, src)


class OutcomeTransport(unittest.TestCase):
    def test_the_artifact_is_uploaded_even_when_the_gate_fails(self):
        # The outcome matters most on the failing paths; without always() the
        # bridge gets nothing on exactly the outcomes it must distinguish.
        text = read("gate.yml")
        self.assertIn("upload-artifact", text)
        self.assertIn("if: always()", text)

    def test_the_gate_passes_the_head_for_the_staleness_check(self):
        self.assertIn("GATE_HEAD_SHA:", read("gate.yml"))


class Runners(unittest.TestCase):
    def test_the_gate_runs_on_a_github_hosted_runner(self):
        # jimemo is public, and the organisation's own gate machines take no
        # public repository (kata jibot-code#bden): a job that asked for one
        # would never be scheduled, and the required check would never report.
        text = read("gate.yml")
        self.assertIn("    runs-on: ubuntu-latest\n", text)
        self.assertNotIn("self-hosted", text)
        # It must never be scheduled on the account that holds the token, nor
        # name either fleet runner account at all.
        self.assertNotIn("mujin-", text)


class ThisReposGate(unittest.TestCase):
    """What the port changes. The file this was copied from pins no gate
    command at all (it only locates `run: ci/run-gate.sh`, and the shell suite
    runs fake gates), so the source repo's `./test-gate.sh` would pass here.
    jimemo's gate is pytest alone: no dependency store, so no warm step."""

    ROW_CMD = '"python3 -m pytest tests -q"'

    def setUp(self):
        self.text = read("gate.yml")
        head, self.gate = self.text.split("      - name: gate\n", 1)
        self.gate = self.gate.split("      - name:", 1)[0]
        self.before = head.rsplit("      - name:", 1)[1]

    def test_the_gate_step_runs_the_registry_row(self):
        self.assertIn(self.ROW_CMD + "\n", self.gate)
        self.assertNotIn('"./test-gate.sh"', self.text)
        self.assertNotIn("pnpm", self.text)

    def test_there_is_no_warm_step(self):
        # No store to warm: a warm step here would run candidate code with the
        # network for nothing.
        self.assertNotIn("--warm", self.text)
        self.assertNotIn("continue-on-error", self.text)
        self.assertTrue(self.before.startswith(" pin the base ref\n"),
                        self.before[:40])
        # The base-ref pin precedes the first candidate code the job runs.
        self.assertLess(self.text.index("      - name: pin the base ref\n"),
                        self.text.index("ci/run-gate.sh"))

    def test_the_command_string_is_expanded_by_the_steps_shell(self):
        self.assertIn('\\\n            ' + self.ROW_CMD, self.gate)
        self.assertNotIn("'python3", self.text)

    def test_python_comes_from_setup_python_at_the_floor_series(self):
        # A hosted runner's system python3 is below jimemo's 3.13.6 floor and
        # has no pytest. setup-python puts its interpreter first on PATH, and
        # run-gate.sh passes PATH through its `env -i`; pytest is installed
        # into that interpreter, pinned to one version.
        self.assertIn("uses: actions/setup-python@", self.text)
        self.assertIsNotNone(re.search(
            r'^          python-version: "3\.13"$', self.text, re.M))
        self.assertIn("run: python -m pip install pytest==8.4.2\n", self.text)
        self.assertIn('run: |\n          ci/run-gate.sh "${{ runner.temp }}/gate-scratch" \\\n',
                      self.gate)
        for gone in ("GATE_TOOLS", "/opt/homebrew", "/Users/"):
            self.assertNotIn(gone, self.text)

    def test_every_action_is_pinned_to_a_commit(self):
        # A pull request runs this file, but what it `uses:` is fetched by
        # tag; a moved tag would change the gate without a diff here.
        uses = re.findall(r"^\s+(?:- )?uses: (.*)$", self.text, re.M)
        self.assertEqual(len(uses), 3, uses)
        for ref in uses:
            self.assertIsNotNone(re.match(
                r"[\w.-]+/[\w.-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+$", ref), ref)

    def test_the_budgets_are_the_registry_contract(self):
        # Two 300 s gate attempts and a job that holds them. The queue's
        # check_response_timeout_minutes (30) must be above the job's.
        self.assertIn("        timeout-minutes: 11\n", self.gate)
        self.assertIn("\n    timeout-minutes: 14\n", self.text)

    def test_a_host_that_cannot_run_pytest_defers(self):
        # Not just "python3 starts": the bare runner PATH has a python3 that
        # starts and has no pytest. The readiness probe is run by the wrapper
        # as `ci/gate-ready.sh --version` before the gate.
        self.assertIn("          RUN_GATE_PREFLIGHT: python3 ci/gate-ready.sh\n",
                      self.gate)
        probe = os.path.join(REPO, "ci", "gate-ready.sh")
        self.assertTrue(os.access(probe, os.X_OK), probe)

    def test_no_prose_from_the_source_repo_survives(self):
        for stale in ("jibot-ops's gate", "15 for this repo", "10 here",
                      "GATE_HOST_LOCK_PATH", "GATE_STORE", "GATE_TOOLS",
                      "isolation-check", "mujin-gate"):
            self.assertNotIn(stale, self.text)


if __name__ == "__main__":
    unittest.main()
