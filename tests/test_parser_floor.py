"""The boundary that makes deleting jimemo#y9p8's guard safe.

These tests exist to fail if the boundary stops being a boundary. The point
that matters is not that ``assert_interpreter_is_supported()`` raises when
called -- it is that **importing a module that lints cannot happen without it
raising**. So the important tests here run a FRESH PROCESS and really import
``jimemo.lint``; deleting the module-level call in ``lint.py`` makes them
fail, which a test that calls the function by hand would not.

The detection of a parser that misbehaves at or above the floor is NOT here
-- it is ``tests/test_lint.py``'s 318-case canary and the nine y9p8 payload
vectors. See ``src/jimemo/_parser_floor.py`` for why runtime probing was
tried and dropped.
"""

import ast
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
PACKAGE_INIT = REPO_ROOT / "src" / "jimemo" / "__init__.py"


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


# --- the floor constant ---------------------------------------------------


def test_floor_constant_is_a_three_component_version():
    # A two-component floor is what the first review rejected: the fixes
    # jimemo needs landed in 3.13.4 and 3.13.6, so "3.13" would admit
    # interpreters that reopen the y9p8 bypass.
    assert len(PYTHON_FLOOR) == 3, PYTHON_FLOOR
    assert PYTHON_FLOOR >= (3, 13, 6), PYTHON_FLOOR


def test_problem_is_none_on_this_interpreter():
    assert _parser_floor.unsupported_interpreter_problem() is None
    _parser_floor.assert_interpreter_is_supported()


@pytest.mark.parametrize(
    "version",
    [(3, 9, 6), (3, 12, 11), (3, 13, 0), (3, 13, 3), (3, 13, 5)],
    ids=lambda value: ".".join(str(part) for part in value),
)
def test_problem_names_the_version_and_the_floor(version, monkeypatch):
    # 3.13.5 is the case a major/minor comparison would accept: its
    # html.parser still splits attributes where a browser does not.
    monkeypatch.setattr(_parser_floor.sys, "version_info", version)
    problem = _parser_floor.unsupported_interpreter_problem()
    assert problem is not None
    assert ".".join(str(part) for part in version) in problem, problem
    assert FLOOR_TEXT in problem, problem


def test_exact_floor_is_accepted(monkeypatch):
    monkeypatch.setattr(_parser_floor.sys, "version_info", PYTHON_FLOOR)
    assert _parser_floor.unsupported_interpreter_problem() is None


# --- importing lint really crosses the boundary (fresh processes) ---------
#
# Each of these raises the floor in a fresh process and then imports
# jimemo.lint for real. If lint.py's module-level
# assert_interpreter_is_supported() call were deleted, every one would fail
# -- which is the regression they exist to catch.


RAISE_FLOOR = "import jimemo\njimemo.PYTHON_FLOOR = (99, 0, 0)\n"


def test_importing_lint_raises_when_the_interpreter_is_below_the_floor():
    result = _fresh(RAISE_FLOOR + "import jimemo.lint\n")
    assert result.returncode != 0, result.stdout
    assert "RuntimeError" in result.stderr, result.stderr
    assert "99.0.0" in result.stderr, result.stderr


def test_importing_lint_succeeds_on_this_interpreter():
    result = _fresh("import jimemo.lint")
    assert result.returncode == 0, result.stderr


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
    # render pipeline. `from jimemo.lint import lint_html` is the form
    # y9p8's own reproduction uses.
    result = _fresh(RAISE_FLOOR + entry + "\n")
    assert result.returncode != 0, result.stdout
    assert "RuntimeError" in result.stderr, result.stderr


@pytest.mark.skipif(
    not SUB_FLOOR_PYTHONS,
    reason="this machine has no Python below the floor",
)
@pytest.mark.parametrize(
    "version, executable", SUB_FLOOR_PYTHONS, ids=lambda value: str(value)
)
def test_importing_lint_raises_on_a_real_sub_floor_interpreter(version, executable):
    # The strongest form: a real old interpreter, no patching at all.
    result = _fresh("import jimemo.lint", executable=executable)
    assert result.returncode != 0, result.stdout
    assert "RuntimeError" in result.stderr, result.stderr
    assert FLOOR_TEXT in result.stderr, result.stderr


# --- the contract's stated limits ----------------------------------------


def test_package_root_does_not_check_the_floor():
    # jimemo/__init__.py deliberately holds PYTHON_FLOOR and enforces
    # nothing, so `jimemo doctor` stays importable on a sub-floor
    # interpreter and can REPORT the problem in one line (jimemo#gaga item
    # 2) instead of dying in a traceback. Asserted structurally, because a
    # behavioural test that imports jimemo first cannot see a check added
    # here at all.
    tree = ast.parse(PACKAGE_INIT.read_text(encoding="utf-8"))
    reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "version_info"
    ]
    assert reads == [], (
        "src/jimemo/__init__.py now inspects the interpreter version. That "
        "breaks `jimemo doctor` on a sub-floor interpreter, which has to "
        "report the problem rather than traceback -- see "
        "src/jimemo/_parser_floor.py for the contract."
    )
    assert "PYTHON_FLOOR" in PACKAGE_INIT.read_text(encoding="utf-8")


@pytest.mark.skipif(
    not SUB_FLOOR_PYTHONS,
    reason="this machine has no Python below the floor",
)
@pytest.mark.parametrize(
    "version, executable", SUB_FLOOR_PYTHONS[:1], ids=lambda value: str(value)
)
def test_doctor_reports_on_a_real_sub_floor_interpreter(version, executable):
    # The behavioural half, on a REAL old interpreter reached the way a
    # direct caller reaches it (the launcher would have refused first). No
    # faked version_info, so this also proves the package root really is
    # importable there.
    result = _fresh(
        "from jimemo.cli import main\nraise SystemExit(main(['doctor']))\n",
        executable=executable,
    )
    assert result.returncode != 0, "doctor must fail below the floor"
    assert "Traceback" not in result.stderr, result.stderr
    first = result.stdout.splitlines()[0]
    assert first.startswith("FAIL python "), result.stdout
    assert ".".join(str(part) for part in version) in first, first
    assert FLOOR_TEXT in first, first
