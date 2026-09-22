#!/bin/bash
# Tests for ci/run-gate.sh and ci/lane-guard.sh (kata jibot-code#q4av).
#
# Everything runs against a fake gate script in a throwaway mktemp scratch —
# no real gate, no network, no kata. Deliberately NO `set -e`: assertions use
# `command; check "desc" "$?"` accounting, and errexit would abort on the first
# failing assertion instead of recording it. bash 3.2 compatible. Same shape as
# tests/test_deploy.sh.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
RUN_GATE="$REPO_ROOT/ci/run-gate.sh"
LANE_GUARD="$REPO_ROOT/ci/lane-guard.sh"
PASS=0; FAIL=0; FAILED=""

ok()   { PASS=$((PASS+1)); echo "  ok: $1"; }
bad()  { FAIL=$((FAIL+1)); FAILED="$FAILED [$1]"; echo "  FAIL: $1"; }
check() { if [ "$2" = 0 ]; then ok "$1"; else bad "$1"; fi }
eq()     { if [ "$1" = "$2" ]; then ok "$3"; else bad "$3 — got '$1', want '$2'"; fi }
has()    { if grep -qF "$2" "$1"; then ok "$3"; else bad "$3 — '$2' not in $1"; fi }
hasnt()  { if grep -qF "$2" "$1"; then bad "$3 — '$2' unexpectedly in $1"; else ok "$3"; fi }
runs()   { if "$@" >/dev/null 2>&1; then echo 0; else echo $?; fi }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/ci-gate-test.XXXXXX")" || exit 1
trap 'rm -rf "$WORK"' EXIT

# A fake gate whose behaviour is driven by FILES in its scratch dir, not by the
# environment — `env -i` is the whole point of run-gate.sh, so a fake gate that
# read its plan from the environment would see nothing. Per attempt N the gate
# reads `rc.N` (falling back to `rc`) and `out.N` (falling back to `out`).
cat > "$WORK/gate.sh" <<'FAKE'
#!/bin/bash
state="$1"
n_file="$state/attempts"
n=$(cat "$n_file" 2>/dev/null || echo 0)
n=$((n+1)); echo "$n" > "$n_file"
rc=$(cat "$state/rc.$n" 2>/dev/null || cat "$state/rc" 2>/dev/null || echo 0)
out=$(cat "$state/out.$n" 2>/dev/null || cat "$state/out" 2>/dev/null || echo "")
[ -n "$out" ] && echo "$out"
echo "fake gate attempt $n exiting $rc"
tail_txt=$(cat "$state/tail.$n" 2>/dev/null || cat "$state/tail" 2>/dev/null || echo "")
[ -n "$tail_txt" ] && echo "$tail_txt"
# Record the environment this gate was handed, for the allowlist assertion.
env | sort > "$state/env-$n"
exit "$rc"
FAKE
chmod +x "$WORK/gate.sh"
GATE="$WORK/gate.sh"

plan() { # $1 = scratch subdir, then key=value pairs written as files
    local sub="$WORK/$1"; shift
    mkdir -p "$sub"
    local kv
    for kv in "$@"; do
        printf '%s' "${kv#*=}" > "$sub/${kv%%=*}"
    done
}

run() { # $1 = scratch subdir (its plan files must already exist)
    local sub="$WORK/$1"
    mkdir -p "$sub"
    # GATE_HOST_LOCK_PATH is unset unless a case sets it deliberately. The
    # workflow DOES set it, and this harness runs inside the gate it is
    # testing, so an inherited value would be passed through to the fixture
    # and fail the allowlist assertion — a locally green suite that goes red
    # the moment the workflow is switched on.
    env -u GATE_HOST_LOCK_PATH GATE_OUTCOME_FILE="$sub/outcome" \
        "$RUN_GATE" "$sub" "$GATE $sub" > "$sub/stdout" 2>&1
    echo $? > "$sub/rc_actual"
}

outcome() { head -n 1 "$WORK/$1/outcome" 2>/dev/null; }
exitrc()  { cat "$WORK/$1/rc_actual" 2>/dev/null; }

echo "== run-gate: the four exit codes =="

plan green rc=0
run green
eq "$(outcome green)" "outcome=passed" "exit 0 -> passed"
eq "$(exitrc green)" 0 "exit 0 -> the step succeeds"

plan red rc=1
run red
eq "$(outcome red)" "outcome=failed" "exit 1 -> failed"
eq "$(exitrc red)" 1 "exit 1 -> the step fails"

plan timeout rc=124
run timeout
eq "$(outcome timeout)" "outcome=failed" \
   "a bare exit 124 is an ordinary failure"
hasnt "$WORK/timeout/stdout" "retrying once" "a bare 124 is not retried"

echo "== run-gate: the defer predicate =="

plan defer2 rc=2
run defer2
eq "$(outcome defer2)" "outcome=deferred" "two exit 2s -> deferred"
eq "$(exitrc defer2)" 1 \
   "a deferred gate still fails the step (that is what dequeues the PR)"
eq "$(cat "$WORK/defer2/attempts")" 2 "a defer is retried exactly once"

plan defer_then_green rc.1=2 rc.2=0
run defer_then_green
eq "$(outcome defer_then_green)" "outcome=passed" \
   "exit 2 then 0 -> passed (most defers are transient)"

plan bare3 rc=3
run bare3
eq "$(outcome bare3)" "outcome=failed" \
   "a bare exit 3 is an ordinary failure, not a defer"

# drain._toolchain_of (drain.py:131-170): exactly one `test-gate: TOOLCHAIN
# probe=` line, and it must be the LAST nonblank line. The fake gate prints
# `fake gate attempt N exiting R` after `out`, so `out` alone is never last —
# these cases drive the marker through `out.N` with the trailing line suppressed
# by making the marker the final echo instead.
plan toolchain_loose rc=3 "out=test-gate: TOOLCHAIN python3 missing"
run toolchain_loose
eq "$(outcome toolchain_loose)" "outcome=failed" \
   "a loose TOOLCHAIN substring is NOT a defer (no probe=, not last)"

plan toolchain_notlast rc=3 "out=test-gate: TOOLCHAIN probe=python3"
run toolchain_notlast
eq "$(outcome toolchain_notlast)" "outcome=failed" \
   "a well-formed marker that is not the last nonblank line is not a defer"

# A conforming gate: the marker is the last nonblank line (gate_fail3 meets
# this by construction — record, then cause, then exit).
plan toolchain_ok rc=3 "tail=test-gate: TOOLCHAIN probe=python3 missing"
run toolchain_ok
eq "$(outcome toolchain_ok)" "outcome=deferred" \
   "exactly one well-formed marker, last nonblank line -> defer"

plan toolchain_twice rc=3 "tail=test-gate: TOOLCHAIN probe=a
test-gate: TOOLCHAIN probe=b"
run toolchain_twice
eq "$(outcome toolchain_twice)" "outcome=failed" \
   "two markers invalidate the field rather than letting the last one win"

# jp90: a python3 that cannot START is the host's toolchain being broken —
# which is exactly what exit 3 reports, and the gate's own probe names python3.
# Treating the parser's own failure as "invalid marker" would bounce a producer
# for a broken host.
mkdir -p "$WORK/nopython"
printf '#!/bin/sh\nexit 127\n' > "$WORK/nopython/python3"
chmod +x "$WORK/nopython/python3"
plan toolchain_noparser rc=3 "tail=test-gate: TOOLCHAIN probe=python3 missing"
PATH="$WORK/nopython:$PATH" run toolchain_noparser
eq "$(outcome toolchain_noparser)" "outcome=deferred" \
   "a parser that cannot run defers rather than bouncing (jp90)"

plan storemiss rc=1 "out=ERR_PNPM_NO_OFFLINE_TARBALL for foo@1"
run storemiss
eq "$(outcome storemiss)" "outcome=deferred" \
   "a store-miss signature defers whatever the exit code"

# The as-built predicate (drain.py:83-102) checks the store-miss signature
# first and a SUPERVISOR flag for timeouts, never the exit code — so a command
# that genuinely exits 124 with a cold store defers, as it does today. The
# workflow's own timeout cancels the step instead, which never reaches here.
plan storemiss_timeout rc=124 "out=ERR_PNPM_NO_OFFLINE_TARBALL for foo@1"
run storemiss_timeout
eq "$(outcome storemiss_timeout)" "outcome=deferred" \
   "a store miss defers even on exit 124 (the as-built predicate)"

has "$WORK/green/outcome" "run_id=" "the outcome artifact carries run_id"
has "$WORK/green/outcome" "run_attempt=" "the outcome artifact carries run_attempt"
has "$WORK/green/outcome" "head_sha=" "the outcome artifact carries head_sha"
has "$WORK/defer2/outcome" "outcome=deferred" "a deferred run still writes an outcome file"
has "$WORK/red/outcome" "outcome=failed" "a failed run still writes an outcome file"

echo "== run-gate: the environment allowlist =="

plan envtest rc=0
GITHUB_TOKEN=should-not-reach-the-gate \
KATA_AUTH_TOKEN=should-not-reach-the-gate \
GITHUB_ACTIONS=true RUNNER_NAME=mujin-gate-1 \
run envtest

envfile="$WORK/envtest/env-1"
if [ -f "$envfile" ]; then ok "the fake gate recorded its environment"
else bad "the fake gate recorded its environment"; fi
hasnt "$envfile" "KATA_AUTH_TOKEN=" "KATA_AUTH_TOKEN does not reach the gate"
hasnt "$envfile" "GITHUB_TOKEN=" "GITHUB_TOKEN does not reach the gate"
hasnt "$envfile" "GITHUB_" "no GITHUB_* variable reaches the gate"
hasnt "$envfile" "RUNNER_" "no RUNNER_* variable reaches the gate"
has "$envfile" "HOME=$WORK/envtest" "HOME is the scratch dir"
has "$envfile" "TMPDIR=$WORK/envtest/tmp" "TMPDIR is under the scratch dir"
has "$envfile" "PATH=" "PATH is passed through"

# marshalcore/sandbox.py:12-28 — PATH, LANG, LC_ALL, HOME, TMPDIR and nothing
# else. PWD, SHLVL and _ are set by the shell that `env -i` starts, not
# inherited, so they are allowed here.
allowed="PATH LANG LC_ALL HOME TMPDIR PWD SHLVL _"
unexpected=""
while IFS='=' read -r name _; do
    case " $allowed " in *" $name "*) ;; *) unexpected="$unexpected $name" ;; esac
done < "$envfile"
eq "$unexpected" "" "nothing outside the allowlist reaches the gate"

plan locktest rc=0
# Set deliberately, and passed through deliberately (agk3). This is the case
# the workflow actually runs, so it is asserted separately from the allowlist.
locktest_run() {
    mkdir -p "$WORK/locktest"
    GATE_HOST_LOCK_PATH=/usr/local/var/test-gate/host.lock \
    GATE_OUTCOME_FILE="$WORK/locktest/outcome" \
        "$RUN_GATE" "$WORK/locktest" "$GATE $WORK/locktest" \
        > "$WORK/locktest/stdout" 2>&1
}
locktest_run
has "$WORK/locktest/env-1" "GATE_HOST_LOCK_PATH=/usr/local/var/test-gate/host.lock" \
   "GATE_HOST_LOCK_PATH is passed through when set (agk3)"

# The harness itself must survive the environment the WORKFLOW supplies.
plan envtest_wf rc=0
GATE_HOST_LOCK_PATH=/usr/local/var/test-gate/host.lock \
GITHUB_ACTIONS=true RUNNER_TEMP=/tmp/x run envtest_wf
hasnt "$WORK/envtest_wf/env-1" "GITHUB_ACTIONS=" \
   "under the workflow's own environment the allowlist still holds"

echo "== run-gate: refusals =="

eq "$(runs "$RUN_GATE" relative/path "true")" 2 "a relative scratch dir is refused"
eq "$(runs "$RUN_GATE" /tmp/gate.abc "true")" 2 \
   "a scratch dir under /tmp/gate.* is refused (the gate sweeps it)"
eq "$(runs "$RUN_GATE")" 2 "no arguments is a usage error"

echo "== run-gate: the warm is advisory =="

mkdir -p "$WORK/warm"
eq "$(runs "$RUN_GATE" --warm "$WORK/warm" "exit 7")" 0 \
   "a failing warm exits 0 (it can never change the verdict)"

echo "== lane-guard =="

guard() { # $1 = subdir, $2 = marker first line (or "" for no marker)
    local d="$WORK/$1"
    mkdir -p "$d"
    [ -n "$2" ] && printf '%s\nprose below\n' "$2" > "$d/.repoman-managed"
    "$LANE_GUARD" "$d" 2>&1
}

# The guard prefers the BASE ref's marker when the workflow supplies one: on a
# pull_request event the checkout is the candidate's, and a marker it wrote is
# not the repository's landing authority.
guard_base() { # $1 = subdir, $2 = checkout marker, $3 = base marker
    local d="$WORK/$1"
    mkdir -p "$d"
    ( cd "$d" && git init -q . && git config user.email t@t && git config user.name t
      if [ -n "$3" ]; then printf '%s\n' "$3" > .repoman-managed; else : > .keep; fi
      git add -A && git commit -qm base
      printf '%s\n' "$2" > .repoman-managed )
    local base
    base=$(git -C "$d" rev-parse HEAD)
    LANE_BASE_SHA="$base" "$LANE_GUARD" "$d" 2>&1
}

guard lane_on "lane: pr-queue" | grep -q "active=true"
check "marker 'lane: pr-queue' activates the lane" "$?"

guard lane_off "lane: repoman" | grep -q "active=false"
check "marker 'lane: repoman' does not" "$?"

guard lane_legacy "This repo's main is managed by repoman" | grep -q "active=false"
check "today's marker text (no lane: line) does not activate the lane" "$?"

guard lane_none "" | grep -q "active=false"
check "no marker at all does not activate the lane" "$?"

d="$WORK/lane_marshal"; mkdir -p "$d"
printf 'lane: pr-queue\n' > "$d/.marshal-managed"
"$LANE_GUARD" "$d" 2>&1 | grep -q "active=true"
check "the pre-rename .marshal-managed marker is honoured too" "$?"

d="$WORK/lane_ws"; mkdir -p "$d"
printf '  lane: pr-queue  \r\n' > "$d/.repoman-managed"
"$LANE_GUARD" "$d" 2>&1 | grep -q "active=true"
check "leading/trailing whitespace and CRLF are tolerated" "$?"

eq "$(runs "$LANE_GUARD" "$WORK/lane_none")" 0 \
   "the guard always exits 0 (it skips a job, it does not fail one)"

# The BASE ref is the lane authority. On a pull_request event the checkout is
# the candidate's, so a marker it wrote must not be able to switch the lane on
# for its own pull request — nor keep it on after main has rolled back.
guard_base lane_base_off "lane: pr-queue" "lane: repoman" | grep -q "active=false"
check "a candidate marker cannot switch the lane ON when base says repoman" "$?"

guard_base lane_base_on "lane: repoman" "lane: pr-queue" | grep -q "active=true"
check "a candidate marker cannot switch the lane OFF when base says pr-queue" "$?"

# A marker the base ref does not have is not a reason to fall back to the
# candidate's copy.
guard_base lane_base_absent "lane: pr-queue" "" | grep -q "active=false"
check "no marker on the base ref does not fall back to the checkout" "$?"

# --- run-gate.sh preflight: a host that cannot start the gate's interpreter is
# a DEFERRAL, never a failure (jp90). The gate itself must not run at all.
echo "== run-gate preflight =="
PF="$WORK/preflight"; mkdir -p "$PF/state" "$PF/bin"
echo 0 > "$PF/state/rc"
GATE_OUTCOME_FILE="$PF/outcome" RUN_GATE_PREFLIGHT="no-such-interpreter-q4av" \
    "$RUN_GATE" "$PF/scratch" "sh $WORK/gate.sh $PF/state" > "$PF/log" 2>&1
eq "$?" 1 "a missing interpreter exits non-zero (the pull request leaves the queue)"
has "$PF/outcome" "outcome=deferred" "a missing interpreter is DEFERRED, not failed"
[ ! -e "$PF/state/attempts" ]
check "the gate is not run at all when the preflight fails" "$?"
# An interpreter that exists but cannot start (exit 126/127 territory).
printf '#!/bin/sh\nexit 127\n' > "$PF/bin/broken-python"; chmod +x "$PF/bin/broken-python"
PATH="$PF/bin:$PATH" GATE_OUTCOME_FILE="$PF/outcome2" RUN_GATE_PREFLIGHT="broken-python" \
    "$RUN_GATE" "$PF/scratch2" "sh $WORK/gate.sh $PF/state" > "$PF/log2" 2>&1
has "$PF/outcome2" "outcome=deferred" "an interpreter that cannot start is DEFERRED"
# A python that answers --version and cannot initialize (a lost standard
# library): --version is handled before start-up, so a --version probe passes it.
printf '#!/bin/sh\n[ "$1" = --version ] && { echo "Python 3.99"; exit 0; }\necho "Fatal Python error: init_fs_encoding" >&2; exit 1\n' > "$PF/bin/python3-hollow"
chmod +x "$PF/bin/python3-hollow"
PATH="$PF/bin:$PATH" GATE_OUTCOME_FILE="$PF/outcome4" RUN_GATE_PREFLIGHT="python3-hollow" \
    "$RUN_GATE" "$PF/scratch4" "sh $WORK/gate.sh $PF/state" > "$PF/log4" 2>&1
has "$PF/outcome4" "outcome=deferred" "a python that answers --version but cannot start is DEFERRED"
# And the control: with a working preflight a FAILING gate is still a failure.
echo 1 > "$PF/state/rc"
GATE_OUTCOME_FILE="$PF/outcome3" RUN_GATE_PREFLIGHT="sh" \
    "$RUN_GATE" "$PF/scratch3" "sh $WORK/gate.sh $PF/state" > "$PF/log3" 2>&1
has "$PF/outcome3" "outcome=failed" "a working host and a failing gate is still FAILED"

# --- isolation-check.sh: every way a path can be reachable is READABLE.
echo "== isolation check =="
ISO="$REPO_ROOT/ci/isolation-check.sh"
ME="$(id -un)"
IS="$WORK/iso"; mkdir -p "$IS/open" "$IS/traverse-only/inner" "$IS/closed/inner"
echo secret > "$IS/open/file"; echo secret > "$IS/traverse-only/inner/file"
# 0311, not 0711: this test OWNS the fixture, and with 0711 the owner keeps
# read, so `ls` succeeds and the traversal branch is never reached — the test
# would pass with that branch deleted. 0311 is the same shape for the account
# that matters here: it cannot list, and it can pass through.
chmod 311 "$IS/traverse-only"; chmod 000 "$IS/closed"
ISOLATION_EXPECT_USER="$ME" "$ISO" "$IS/closed" "$IS/closed/inner/file" "$IS/nope" > "$IS/out1" 2>&1
eq "$?" 0 "closed and absent paths pass"
hasnt "$IS/out1" "READABLE" "nothing is reported readable for them"
ISOLATION_EXPECT_USER="$ME" "$ISO" "$IS/open/file" > "$IS/out2" 2>&1
eq "$?" 1 "a readable FILE fails the job"
ISOLATION_EXPECT_USER="$ME" "$ISO" "$IS/open" > "$IS/out3" 2>&1
eq "$?" 1 "a listable DIRECTORY fails the job"
if [ "$(id -u)" != 0 ]; then
    # Listing is denied, `cat dir` fails — and every descendant is still
    # reachable. A probe that only lists calls this DENIED. The fixture's own
    # shape is asserted first, or the test proves nothing about the probe.
    ls "$IS/traverse-only"/ >/dev/null 2>&1; [ "$?" != 0 ]
    check "fixture: the traverse-only directory cannot be LISTED by this account" "$?"
    [ "$(cat "$IS/traverse-only/inner/file" 2>/dev/null)" = secret ]
    check "fixture: and a known descendant is still READABLE through it" "$?"
    ISOLATION_EXPECT_USER="$ME" "$ISO" "$IS/traverse-only" > "$IS/out4" 2>&1
    eq "$?" 1 "a traverse-only (0711) directory fails the job"
    has "$IS/out4" "READABLE" "and is reported READABLE"
fi
ISOLATION_EXPECT_USER="somebody-else-q4av" "$ISO" "$IS/closed" > "$IS/out5" 2>&1
eq "$?" 1 "running as the wrong account fails the job even when nothing is readable"
has "$IS/out5" "WRONG USER" "and says so"
chmod 700 "$IS/closed" "$IS/traverse-only"

echo
echo "test_ci_gate: $PASS passed, $FAIL failed"
if [ "$FAIL" != 0 ]; then
    echo "failed:$FAILED"
    exit 1
fi
exit 0
