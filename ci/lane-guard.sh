#!/bin/bash
# lane-guard.sh — the first step of every PR-lane workflow (kata jibot-code#q4av).
#
# The workflows land in the repository before the repository is flipped to the
# pull-request lane, and a file in .github/workflows runs as soon as a runner
# exists whether or not anybody meant it to: triggers carry no lane condition.
# So each workflow asks this first, and skips every later step unless the
# repository's own marker says the lane is live.
#
# The marker is the same file that already decides whether a repo is managed at
# all (.repoman-managed, or .marshal-managed until 2026-09-28). Its FIRST LINE
# carries the lane:
#
#     lane: pr-queue     → this lane is live
#     lane: repoman      → the old lane; skip
#     anything else      → the old lane; skip
#
# Readers that only test for the file's existence keep working unchanged, which
# is the point: both lanes forbid pushing main, so every /do-it, codex-work and
# finishing-a-development-branch ending is unaffected.
#
# Output: `active=true|false` on $GITHUB_OUTPUT (and on stdout when there is no
# GITHUB_OUTPUT, so it is runnable by hand). Always exits 0 — a guard that
# fails the job is a guard that pages somebody at 3am for a repo that was never
# meant to be running these workflows.
set -uo pipefail

repo_root="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)}"

# THE BASE REF IS THE AUTHORITY, NOT THE CHECKOUT. On a `pull_request` event
# the checked-out tree is the candidate's, and a marker the candidate wrote is
# not the repository's landing authority — it could switch the lane on for its
# own pull request, or keep it on after main has rolled back. The workflow
# passes the base sha, whose objects are present from `fetch-depth: 0`, so this
# is a local read of trusted content and needs no credential.
#
# Falling back to the checkout when no base sha is supplied is deliberate: it
# is how this runs by hand and in the repo's own tests, and by then the only
# marker available IS the checkout's.
lane=""
read_lane() { # $1 = a `git show`-able prefix, or empty for the working tree
    local at="$1" f raw
    for f in .repoman-managed .marshal-managed; do
        if [ -n "$at" ]; then
            # Read the whole blob, THEN take its first line. `git show | head`
            # closes the pipe early, and under pipefail a SIGPIPE'd git would
            # discard a perfectly good marker — which here means silently
            # reporting active=false and skipping the gate.
            raw=$(git -C "$repo_root" show "$at:$f" 2>/dev/null) || continue
            raw=$(printf '%s\n' "$raw" | sed -n '1p')
        else
            [ -f "$repo_root/$f" ] || continue
            raw=$(sed -n '1p' "$repo_root/$f" 2>/dev/null) || continue
        fi
        [ -n "$raw" ] || continue
        printf '%s' "$raw" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
        return 0
    done
    return 1
}

source_of="the checkout"
if [ -n "${LANE_BASE_SHA:-}" ]; then
    if lane=$(read_lane "$LANE_BASE_SHA"); then
        source_of="the base ref ${LANE_BASE_SHA}"
    else
        lane=""
        source_of="the base ref ${LANE_BASE_SHA} (no marker there)"
    fi
else
    lane=$(read_lane "") || lane=""
fi

if [ "$lane" = "lane: pr-queue" ]; then
    active=true
    why="$source_of says lane: pr-queue"
else
    active=false
    if [ -z "$lane" ]; then
        why="no marker found via $source_of"
    else
        why="$source_of says '${lane}', not 'lane: pr-queue'"
    fi
fi

echo "lane-guard: active=$active ($why)"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "active=$active" >> "$GITHUB_OUTPUT"
fi
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    echo "lane-guard: active=\`$active\` — $why" >> "$GITHUB_STEP_SUMMARY"
fi
exit 0
