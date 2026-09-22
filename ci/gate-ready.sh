#!/bin/sh
# gate-ready.sh — can the python3 on PATH run jimemo's gate? (kata jibot-code#bden)
#
# Named in gate.yml's RUN_GATE_PREFLIGHT next to python3. The wrapper's own
# python3 probe only asks whether an interpreter STARTS (ci/run-gate.sh, the
# preflight loop), and the runner host's bare PATH carries a /usr/bin/python3
# that starts fine, is 3.9, and has no pytest: if Homebrew's python3 ever
# went missing, that one would answer the probe and `python3 -m pytest` would
# then exit 1 — a producer bounced for a broken runner, which jp90 forbids.
# This asks the question the gate actually needs answered — a final release at
# or above jimemo's floor, with pytest importable — in the gate's own
# environment, and a "no" becomes a deferral. run-gate.sh calls it as
# `ci/gate-ready.sh --version`; the argument is ignored.
#
# Exit 0 = ready; anything else = the gate could not run on this host.
python3 - <<'PY'
import sys
floor = (3, 13, 6)
v = sys.version_info
if v[:3] < floor or v.releaselevel != "final":
    sys.exit("gate-ready: python3 is %s, need a final release >= %s"
             % (sys.version.split()[0], ".".join(map(str, floor))))
try:
    import pytest  # noqa: F401
except ImportError:
    sys.exit("gate-ready: python3 %s has no pytest" % sys.version.split()[0])
PY
