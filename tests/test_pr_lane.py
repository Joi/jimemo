"""The PR-lane machinery under ci/ runs inside the registry gate (kata jibot-code#bden).

The row is `python3 -m pytest tests -q` and stays so; these three tests are how
that row also gates a change to a workflow or to ci/, on both lanes. Each one
is the exact command jibot-ops's gate runs. The bridge suite is verbatim from
jibot-ops 8f46f7e and needs nothing beyond the stdlib; the shell suite wants
/bin/bash 3.2, which every Mac has.
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _run(*argv):
    proc = subprocess.run(argv, cwd=REPO, capture_output=True, text=True,
                          timeout=300)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]


@pytest.mark.parametrize("suite", ["ci/scripts", "ci/bridge"])
def test_pr_lane_unit_suites(suite):
    _run(sys.executable, "-m", "unittest", "discover", "-s", suite, "-t", suite)


def test_pr_lane_gate_wrapper_and_lane_guard():
    _run("/bin/bash", "tests/test_ci_gate.sh")


# ci/gate-ready.sh: the readiness probe gate.yml names in RUN_GATE_PREFLIGHT.
# run-gate.sh calls it as `ci/gate-ready.sh --version` under `env -i` with the
# gate's PATH, so it is exercised the same way here, with a PATH whose python3
# is a shim.

def _ready_with(tmp_path, python3_body):
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "python3").write_text("#!/bin/sh\n" + python3_body)
    (shim / "python3").chmod(0o755)
    return subprocess.run(
        ["env", "-i", "PATH=%s:/usr/bin:/bin" % shim, "HOME=%s" % tmp_path,
         "ci/gate-ready.sh", "--version"],
        cwd=REPO, capture_output=True, text=True, timeout=60)


def test_gate_ready_accepts_a_python_that_can_run_the_gate(tmp_path):
    # This interpreter, with pytest reachable the way the gate host reaches it
    # (in site-packages, not under a HOME the probe's scratch HOME hides): the
    # shim names pytest's own location, so the test holds wherever this Mac
    # keeps it.
    site = str(Path(pytest.__file__).resolve().parent.parent)
    proc = _ready_with(tmp_path, 'PYTHONPATH="%s" exec "%s" "$@"\n'
                       % (site, sys.executable))
    assert proc.returncode == 0, proc.stderr


def test_gate_ready_refuses_a_python_below_the_floor(tmp_path):
    # The runner host's /usr/bin/python3: starts, is 3.9, satisfies the
    # wrapper's own probe, cannot run this gate. Skipped on a host whose
    # system python is already at the floor (jimemo's hosted CI, one day).
    if not Path("/usr/bin/python3").exists():
        pytest.skip("no /usr/bin/python3")
    ver = subprocess.run(["/usr/bin/python3", "-c",
                          "import sys; print(sys.version_info >= (3, 13, 6))"],
                         capture_output=True, text=True).stdout.strip()
    if ver == "True":
        pytest.skip("/usr/bin/python3 satisfies the floor here")
    proc = _ready_with(tmp_path, 'exec /usr/bin/python3 "$@"\n')
    assert proc.returncode != 0
    assert "need a final release" in proc.stderr


def test_gate_ready_refuses_a_python_without_pytest(tmp_path):
    # Same interpreter, pytest hidden: the other way a host ends up with a
    # python3 that starts and a gate that exits 1.
    proc = _ready_with(
        tmp_path,
        'exec "%s" -S -c "import sys; sys.argv[0]=\'-\'; exec(sys.stdin.read())"\n'
        % sys.executable)
    assert proc.returncode != 0
    assert "has no pytest" in proc.stderr
