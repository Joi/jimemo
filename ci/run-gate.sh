#!/bin/bash
# run-gate.sh — run a repo's gate the way repoman runs it (kata jibot-code#q4av).
#
# Two things a bare `run:` step does not do, and both of them are why this file
# exists.
#
# 1. THE ENVIRONMENT IS AN ALLOWLIST, NOT A SCRUB.
#    marshalcore/sandbox.py:12-28 passes exactly PATH, LANG and LC_ALL through,
#    then synthesizes HOME and TMPDIR inside a per-attempt scratch directory.
#    An Actions job's environment is the opposite — dozens of GITHUB_* and
#    RUNNER_* variables plus whatever the runner service inherited. The gate is
#    tested against the narrow one and behaves differently in the wide one, so
#    we rebuild the narrow one with `env -i`.
#
#    This is FIDELITY, not security. The security boundary is the runner user
#    and the workflow-provenance rules (the gate runs as mujin-gate, which
#    holds no credential; credential-bearing workflows trigger on
#    pull_request_target / workflow_run / schedule so they run main's copy of
#    the YAML). `env -i` controls inheritance and nothing else.
#
# 2. THE DEFER CONTRACT.
#    test-gate.sh has four exit codes and only one of them is a branch defect
#    (scripts/test-gate.sh:1-8). A merge queue has no "defer" — a check either
#    reports success or the pull request leaves the queue — so losing the
#    distinction would turn every cold dependency store and every broken
#    toolchain into a producer bounce, which jp90 forbids
#    (marshalcore/drain.py:1737-1743).
#
#    The predicate is drain._gate_cannot_run (drain.py:83-102, :182-199), and
#    it is NOT "exit 2 or 3":
#      * a dependency-store-miss signature is a defer whatever the exit code
#      * exit 2 is a defer
#      * exit 3 is a defer ONLY with a valid `test-gate: TOOLCHAIN` marker
#      * everything else is a failure
#
#    There is deliberately NO exit-124 rule. Repoman's predicate checks
#    `tr.timed_out` — a flag its own supervisor sets — and never an exit code.
#    The wrapper has no supervisor: in a workflow the supervisor is Actions'
#    `timeout-minutes`, which CANCELS the step, so the wrapper never returns at
#    all and the bridge sees a completed run with no outcome artifact (which it
#    treats as infrastructure, not as a producer failure). Adding a "124 never
#    defers" rule here would diverge from the as-built predicate for a command
#    that genuinely exits 124 with a store miss, which today defers.
#
# Usage: run-gate.sh [--warm] <scratch dir> <gate command>
# Outcome: `outcome=<passed|failed|deferred>` written to $GATE_OUTCOME_FILE
# (default <scratch>/outcome), to $GITHUB_OUTPUT, and to stdout.
# bash 3.2 compatible.
set -uo pipefail

TOOLCHAIN_PREFIX="test-gate: TOOLCHAIN probe="
STORE_MISS_MARKER="ERR_PNPM_NO_OFFLINE_TARBALL"

warm=0
if [ "${1:-}" = "--warm" ]; then
    warm=1
    shift
fi

scratch="${1:-}"
shift || true
gate_cmd="$*"

if [ -z "$scratch" ] || [ -z "$gate_cmd" ]; then
    echo "usage: run-gate.sh [--warm] <scratch dir> <gate command>" >&2
    exit 2
fi

case "$scratch" in
    /*) ;;
    *) echo "run-gate: scratch dir must be absolute, got '$scratch'" >&2; exit 2 ;;
esac

# The gate sweeps /tmp/gate.* on age alone (test-gate.sh:1890-1897), so a
# scratch directory in that namespace can be deleted out from under a running
# job by an unrelated one.
case "$scratch" in
    /tmp/gate.*|/private/tmp/gate.*)
        echo "run-gate: scratch dir must not live under /tmp/gate.* — the gate sweeps that namespace on age" >&2
        exit 2 ;;
esac

mkdir -p "$scratch/tmp" || exit 2
OUTCOME_FILE="${GATE_OUTCOME_FILE:-$scratch/outcome}"

log_dir="$scratch/log"
mkdir -p "$log_dir" || exit 2

run_once() { # $1 = log file → the gate's exit status
    local out="$1"
    # GATE_HOST_LOCK_PATH is passed only when set, and as one argv element, so
    # a path with a space cannot split into two variables. It is the one lever
    # that must survive `env -i`: the gate's per-host lock keys on HOME
    # (test-gate.sh:688) and HOME here is a scratch dir, so without an explicit
    # shared path this gate does NOT serialize against repoman's gate or a
    # hand-run one on the same Mac — the agk3 failure (four gates on one Mac,
    # load 54, cross-killed pytest).
    if [ -n "${GATE_HOST_LOCK_PATH:-}" ]; then
        env -i \
            PATH="$PATH" LANG="${LANG-}" LC_ALL="${LC_ALL-}" \
            HOME="$scratch" TMPDIR="$scratch/tmp" \
            GATE_HOST_LOCK_PATH="$GATE_HOST_LOCK_PATH" \
            /bin/sh -c "$gate_cmd" > "$out" 2>&1
    else
        env -i \
            PATH="$PATH" LANG="${LANG-}" LC_ALL="${LC_ALL-}" \
            HOME="$scratch" TMPDIR="$scratch/tmp" \
            /bin/sh -c "$gate_cmd" > "$out" 2>&1
    fi
    return $?
}

# drain._toolchain_of (drain.py:131-170), clause for clause: exactly one
# marker line, and it is the last nonblank line. Zero markers and more than one
# are both "not a toolchain failure" — a spoofed second marker invalidates the
# field rather than letting the last one win.
#
# In PYTHON, not in grep, and that is the point. The drain splits lines with
# Python's splitlines(), which breaks on CR, NEL, LS (U+2028), PS (U+2029),
# VT and FF as well as LF; grep only sees LF. Two markers separated by a U+2028
# read as ONE line to grep — so grep would accept a defer the drain rejects,
# and candidate output controls that text. `tests/test_marshal_drain.py:1824`
# is the regression on the other side of this contract.
# Exit codes: 0 = a valid marker; 1 = parsed, and NOT a valid marker;
# 2 = the parser itself could not run.
toolchain_marker_valid() { # $1 = log file
    [ -f "$1" ] || return 1
    python3 - "$1" "$TOOLCHAIN_PREFIX" <<'PYEOF'
import sys
path, prefix = sys.argv[1], sys.argv[2]
try:
    with open(path, "r", errors="replace") as fh:
        raw = fh.read()
except OSError:
    sys.exit(1)
lines = raw.splitlines()
if len([l for l in lines if l.startswith(prefix)]) != 1:
    sys.exit(1)
nonblank = [l for l in lines if l.strip()]
sys.exit(0 if nonblank and nonblank[-1].startswith(prefix) else 1)
PYEOF
    rc=$?
    # A python3 that cannot START is not "the marker is invalid" — it is the
    # host's toolchain being broken, which is the very thing exit 3 reports.
    # The gate's own probe names python3 (test-gate.sh:2814-2821), so the case
    # is not hypothetical: a broken python3 makes the gate exit 3 AND makes
    # this parser fail, and calling that a producer failure is the jp90
    # violation this whole predicate exists to avoid. 126/127 are the shell's
    # "cannot execute" / "not found".
    case "$rc" in
        0) return 0 ;;
        126|127) return 0 ;;
        *) return "$rc" ;;
    esac
}

# drain._gate_cannot_run, clause for clause.
gate_cannot_run() { # $1 = exit status, $2 = log file
    local rc="$1" out="$2"

    # A cold store after a failed warm is the host's problem, not the branch's,
    # whatever exit code it surfaced as — checked FIRST, as the drain does.
    if grep -qF "$STORE_MISS_MARKER" "$out" 2>/dev/null; then
        return 0
    fi

    # test-gate.sh's reserved could-not-run code.
    [ "$rc" = 2 ] && return 0

    # The host's own tools refusing — but only when the gate says so IN THE
    # FORM the drain accepts. A substring match is not the predicate:
    # drain._toolchain_of requires EXACTLY ONE line beginning
    # `test-gate: TOOLCHAIN probe=`, and it must be the LAST NONBLANK line.
    # Candidate output can print anything, so a looser check lets a branch
    # turn its own failure into an infrastructure deferral — and an eternal
    # deferral costs an operator a diagnosis.
    if [ "$rc" = 3 ] && toolchain_marker_valid "$out"; then
        return 0
    fi

    return 1
}

emit() { # $1 = outcome
    # The bridge needs more than the word: a verdict is authoritative only when
    # it is the latest attempt of the latest run for the CURRENT head, and the
    # pull request is still open. These facts travel together in the artifact,
    # because a workflow_run event can arrive after the world has moved. The
    # submission attempt is not here: it lives in the pull request body, which
    # the bridge parses and validates itself.
    {
        echo "outcome=$1"
        echo "run_id=${GITHUB_RUN_ID:-}"
        echo "run_attempt=${GITHUB_RUN_ATTEMPT:-}"
        echo "head_sha=${GATE_HEAD_SHA:-}"
    } > "$OUTCOME_FILE"
    echo "run-gate: outcome=$1"
    [ -n "${GITHUB_OUTPUT:-}" ] && echo "outcome=$1" >> "$GITHUB_OUTPUT"
    [ -n "${GITHUB_STEP_SUMMARY:-}" ] && echo "gate outcome: \`$1\`" >> "$GITHUB_STEP_SUMMARY"
    return 0
}

if [ "$warm" = 1 ]; then
    # The warm runs with the runner user's normal environment: it needs the
    # network, a package store outside the workspace and, on cell-fleet, ssh to
    # jibotmac. Its exit status is advisory and can never change the verdict
    # (drain journals warm_exit and moves on) — a failed warm surfaces later, if
    # at all, as a store miss in the offline gate, which defers.
    /bin/sh -c "$gate_cmd" > "$log_dir/warm-output.txt" 2>&1
    warm_rc=$?
    echo "run-gate: warm exited $warm_rc (advisory; never changes the verdict)"
    exit 0
fi

# The scoping probe counts ignored files as dirty (test-gate.sh:1646-1658), so a
# workspace carrying a cache from a previous job silently runs the full battery.
# Say so rather than paying it quietly.
if command -v git >/dev/null 2>&1; then
    dirt=$(git status --porcelain --untracked-files=normal --ignored=matching 2>/dev/null | head -5)
    if [ -n "$dirt" ]; then
        echo "run-gate: WARNING workspace is not pristine — the gate will run the FULL battery, not the scoped path:"
        # shellcheck disable=SC2001  # prefixing every line; bash 3.2 has no
        # parameter expansion that does this legibly
        echo "$dirt" | sed 's/^/  /'
    fi
fi

# PREFLIGHT: can the host start the interpreters the gate is written in?
# A gate with its own toolchain probe (cell-fleet's) reports a broken host as
# exit 3 with a marker, which the predicate above defers. A plain gate has no
# such probe: under `set -e` a python3 that cannot start ends it with 126/127,
# which the predicate reads as a FAILURE — a producer bounced for a broken
# runner, which jp90 forbids. So the question is asked here, before the gate
# runs, in the gate's own environment, and the answer "no" is a deferral.
#
# The probe STARTS the interpreter. `python3 --version` is answered during
# argument parsing, before initialization, so an install that has lost its
# standard library passes it and then dies on the gate's first line. -I and -S
# keep the checkout, the environment and site-packages out of the answer.
#
# WHAT THIS DOES NOT CLAIM. This job is the untrusted half: a pull request
# controls gate.yml, PATH and this variable, and could upload an
# `outcome=deferred` artifact without running anything. A pull request can
# therefore always make ITS OWN gate read as deferred — and gains nothing: the
# required check is red, it does not merge, and nobody is bounced. What the
# preflight is for is the honest producer on a broken host.
for tool in ${RUN_GATE_PREFLIGHT-python3}; do
    # shellcheck disable=SC2016  # $0 is the inner shell's, on purpose
    case "$tool" in
        python*) probe='command -v "$0" >/dev/null 2>&1 && "$0" -I -S -c "import encodings, unittest" >/dev/null 2>&1' ;;
        *)       probe='command -v "$0" >/dev/null 2>&1 && "$0" --version >/dev/null 2>&1' ;;
    esac
    if ! env -i PATH="$PATH" HOME="$scratch" TMPDIR="$scratch/tmp" /bin/sh -c "$probe" "$tool"; then
        echo "run-gate: PREFLIGHT '$tool' cannot start on this host — the gate could not run"
        emit deferred
        exit 1
    fi
done

run_once "$log_dir/gate-1.txt"
rc=$?
tail -40 "$log_dir/gate-1.txt"

if [ "$rc" = 0 ]; then
    emit passed
    exit 0
fi

if ! gate_cannot_run "$rc" "$log_dir/gate-1.txt"; then
    emit failed
    exit 1
fi

# Most defers are transient: a lost race, a cold store after a warm that has
# since been repaired. One retry, then stop — a host with a broken toolchain
# must not be able to spin the queue.
echo "run-gate: gate could not run (rc $rc) — retrying once"
run_once "$log_dir/gate-2.txt"
rc2=$?
tail -40 "$log_dir/gate-2.txt"

if [ "$rc2" = 0 ]; then
    emit passed
    exit 0
fi

if ! gate_cannot_run "$rc2" "$log_dir/gate-2.txt"; then
    emit failed
    exit 1
fi

emit deferred
# The non-zero exit is what removes the pull request from the queue. The
# OUTCOME is what the bridge reads, and it is what keeps this off the producer.
exit 1
