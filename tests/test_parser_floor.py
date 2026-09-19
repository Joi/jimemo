"""The boundary that makes deleting jimemo#y9p8's guard safe.

These tests exist to fail if the boundary stops being a boundary. The point
that matters is not that ``assert_parser_is_browser_faithful()`` raises when
called -- it is that **importing a module that lints cannot happen without it
raising**. So the important tests here run a FRESH PROCESS and really import
``jimemo.lint``; deleting the module-level call in ``lint.py`` makes them
fail, which a test that calls the function by hand would not.
"""

import collections
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from jimemo import PYTHON_FLOOR, _parser_floor

from conftest import SUB_FLOOR_PYTHONS

FLOOR_TEXT = ".".join(str(part) for part in PYTHON_FLOOR)

# Asserted by NAME, not derived from _parser_floor.PROBES: parametrising the
# negative tests off the roster alone would let deleting a probe delete its
# own coverage. Each name is one browser disagreement the floor rules out.
EXPECTED_PROBES = {"refs", "style", "attrs"}


def _faked_version_info(major, minor, micro):
    version_info = collections.namedtuple(
        "version_info", "major minor micro releaselevel serial"
    )
    return version_info(major, minor, micro, "final", 0)


def _fresh(code, executable=None):
    """Run `code` in a fresh interpreter with src/ importable."""
    return subprocess.run(
        [executable or sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=90,
        cwd=str(REPO_ROOT),
        env={
            **os.environ,
            "PYTHONPATH": str(REPO_ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )


# --- the roster -----------------------------------------------------------


def test_probe_roster_is_exactly_the_three_expected_checks():
    assert {name for name, _ in _parser_floor.PROBES} == EXPECTED_PROBES


def test_every_probe_passes_on_this_interpreter():
    for name, probe in _parser_floor.PROBES:
        assert probe() is True, name


def test_assertion_is_silent_on_this_interpreter():
    _parser_floor.browser_faithfulness_problem.cache_clear()
    _parser_floor.assert_parser_is_browser_faithful()


# --- importing lint really crosses the boundary (fresh processes) ---------
#
# Each of these raises the floor or breaks a probe in a fresh process and
# then imports jimemo.lint for real. If lint.py's module-level
# assert_parser_is_browser_faithful() call were deleted, every one of them
# would fail -- which is the regression they exist to catch.


IMPORT_LINT_WITH_RAISED_FLOOR = """
import jimemo
jimemo.PYTHON_FLOOR = (99, 0, 0)
import jimemo.lint
"""

IMPORT_LINT_WITH_BROKEN_PROBE = """
import jimemo._parser_floor as floor
floor.PROBES = tuple(
    (name, (lambda: False) if name == {name!r} else probe)
    for name, probe in floor.PROBES
)
floor.browser_faithfulness_problem.cache_clear()
import jimemo.lint
"""


def test_importing_lint_raises_when_the_interpreter_is_below_the_floor():
    result = _fresh(IMPORT_LINT_WITH_RAISED_FLOOR)
    assert result.returncode != 0, result.stdout
    assert "RuntimeError" in result.stderr, result.stderr
    assert "99.0.0" in result.stderr, result.stderr


@pytest.mark.parametrize("probe_name", sorted(EXPECTED_PROBES))
def test_importing_lint_raises_when_any_probe_fails(probe_name):
    result = _fresh(IMPORT_LINT_WITH_BROKEN_PROBE.format(name=probe_name))
    assert result.returncode != 0, result.stdout
    assert "RuntimeError" in result.stderr, result.stderr
    assert repr(probe_name) in result.stderr, result.stderr
    assert FLOOR_TEXT in result.stderr, result.stderr


def test_importing_lint_succeeds_on_this_interpreter():
    result = _fresh("import jimemo.lint")
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(
    not SUB_FLOOR_PYTHONS,
    reason="this machine has no Python below the floor",
)
@pytest.mark.parametrize(
    "version, executable", SUB_FLOOR_PYTHONS, ids=lambda value: str(value)
)
def test_importing_lint_raises_on_a_real_sub_floor_interpreter(version, executable):
    # The strongest form of the same check: a real old interpreter, no
    # patching at all. Skipped only where the machine cannot offer one.
    result = _fresh("import jimemo.lint", executable=executable)
    assert result.returncode != 0, result.stdout
    assert "RuntimeError" in result.stderr, result.stderr
    assert FLOOR_TEXT in result.stderr, result.stderr


@pytest.mark.parametrize(
    "entry",
    [
        "from jimemo.lint import lint_html",
        "from jimemo.lint import lint_standalone",
        "from jimemo import lint",
        "import jimemo.render",
        "from jimemo.cli import main; main(['check', 'nonexistent.html'])",
    ],
)
def test_no_lint_entry_point_is_reachable_below_the_floor(entry):
    # Every documented way into lint, including cli's lazy import and the
    # render pipeline, must cross the boundary. `from jimemo.lint import
    # lint_html` is the form y9p8's own reproduction uses.
    result = _fresh(
        "import jimemo\njimemo.PYTHON_FLOOR = (99, 0, 0)\n" + entry + "\n"
    )
    assert result.returncode != 0, result.stdout
    assert "RuntimeError" in result.stderr, result.stderr


# --- the contract's stated limits ----------------------------------------


def test_doctor_stays_reportable_below_the_floor():
    # jimemo/__init__.py deliberately does NOT enforce the floor: doctor has
    # to be importable on a sub-floor interpreter so it can REPORT the
    # problem in one line (jimemo#gaga item 2) instead of dying in a
    # traceback. This test pins that deliberate exemption, so a future
    # "tighten __init__.py" change has to face it on purpose.
    result = _fresh(
        "import jimemo\n"
        "jimemo.PYTHON_FLOOR = (99, 0, 0)\n"
        "from jimemo.cli import main\n"
        "raise SystemExit(main(['doctor']))\n"
    )
    assert result.returncode != 0, "doctor must fail below the floor"
    assert "Traceback" not in result.stderr, result.stderr
    assert result.stdout.splitlines()[0].startswith("FAIL python "), result.stdout


def test_floor_constant_is_a_three_component_version():
    # A two-component floor is what the first review pass rejected: the
    # fixes jimemo needs landed in 3.13.4 and 3.13.6, so "3.13" would admit
    # interpreters that reopen the y9p8 bypass.
    assert len(PYTHON_FLOOR) == 3, PYTHON_FLOOR
    assert PYTHON_FLOOR >= (3, 13, 6), PYTHON_FLOOR


def test_floor_check_rejects_3_13_5_by_number_not_only_by_probe():
    # 3.13.5 passes 'refs' and 'style' and fails only 'attrs'. The numeric
    # check must reject it on its own, so the boundary does not depend on
    # any single probe to separate it from the floor.
    _parser_floor.browser_faithfulness_problem.cache_clear()
    try:
        original = _parser_floor.sys.version_info
        _parser_floor.sys.version_info = _faked_version_info(3, 13, 5)
        try:
            problem = _parser_floor.browser_faithfulness_problem()
        finally:
            _parser_floor.sys.version_info = original
    finally:
        _parser_floor.browser_faithfulness_problem.cache_clear()
    assert problem is not None
    assert "3.13.5" in problem, problem
    assert FLOOR_TEXT in problem, problem
