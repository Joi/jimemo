#!/bin/bash
# isolation-check.sh — prove, from inside a gate job, that the gate account
# cannot reach what it must not (kata jibot-code#q4av).
#
# The gate runs code that arrived in a pull request. Everything it could read
# on the runner host, that code can read. So the job states, every time, that
# the operator's home, the bridge account's home and the bridge's credentials
# are closed to it — and fails if any of them is not.
#
# This is EVIDENCE, not enforcement: a pull request controls gate.yml and could
# delete this step. What enforces the boundary is the ACL on /Users/joi and the
# mode-700 homes on the host. What this adds is that a boundary which quietly
# stopped holding (a chmod, a new account, a restored backup) turns the next
# gate red instead of staying invisible.
#
# Three separate questions, because a directory can answer them differently:
# list it (`ls path/`), read it as a file (`cat`), and TRAVERSE it (`test -x`
# on a directory). A mode-0711 directory denies the listing and still lets this
# account reach every descendant whose own mode allows it, so a probe that only
# lists reports a boundary that is not there. NOT `ls -d`, which only stats the
# name and succeeds for a closed directory whose parent is searchable.
#
# DENIED also covers "does not exist", and from inside a closed parent the two
# cannot be told apart — which is the property being proved.
set -uo pipefail

# With no arguments: everything on a runner host the gate account must not read.
if [ "$#" -eq 0 ]; then
  set -- /Users/joi /Users/joi/.zshenv /Users/joi/.ssh /Users/joi/repos \
         /Users/joi/dotfiles-private /Users/joi/switchboard \
         /Users/mujin-bridge /Users/mujin-bridge/.mujin \
         /Users/mujin-bridge/.mujin/review-evidence.pem \
         /Users/mujin-bridge/.mujin/kata-token
fi

fail=0
for path in "$@"; do
  if ls "$path"/ >/dev/null 2>&1 || cat "$path" >/dev/null 2>&1 \
      || { [ -d "$path" ] && [ -x "$path" ]; }; then
    echo "READABLE  $(id -un) -> $path"
    fail=1
  else
    echo "DENIED    $(id -un) -> $path"
  fi
done
# The job must be running as the gate account at all, or the lines above prove
# nothing about it.
if [ "$(id -un)" != "${ISOLATION_EXPECT_USER:-mujin-gate}" ]; then
  echo "WRONG USER  this job runs as $(id -un), expected ${ISOLATION_EXPECT_USER:-mujin-gate}"
  fail=1
fi
exit "$fail"
