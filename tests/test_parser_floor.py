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
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from jimemo import PYTHON_FLOOR, _parser_floor

from conftest import SUB_FLOOR_PYTHONS

FLOOR_TEXT = ".".join(str(part) for part in PYTHON_FLOOR)
PACKAGE_INIT = REPO_ROOT / "src" / "jimemo" / "__init__.py"


# One empty HOME for every _fresh() call, removed when the interpreter
# exits (TemporaryDirectory's finalizer), instead of one leaked mkdtemp per
# call.
_FRESH_HOME = tempfile.TemporaryDirectory(prefix="jimemo-fresh-home-")


def _fresh(code, executable=None):
    """Run `code` in a fresh interpreter with src/ importable. HOME is an
    empty temp dir: `jimemo doctor` reads -- and runs -- the interpreter
    bound in ~/.local/bin/jimemo (jimemo#p0nk), and a stale wrapper on the
    developer's machine must not change what these tests see."""
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": _FRESH_HOME.name,
    }
    env.pop("JIMEMO_ENTRY_POINT", None)
    return subprocess.run(
        [executable or sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=90,
        cwd=str(REPO_ROOT),
        env=env,
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
    [
        (3, 9, 6, "final", 0),
        (3, 12, 11, "final", 0),
        (3, 13, 0, "final", 0),
        (3, 13, 3, "final", 0),
        (3, 13, 5, "final", 0),
    ],
    ids=lambda value: ".".join(str(part) for part in value[:3]),
)
def test_problem_names_the_version_and_the_floor(version, monkeypatch):
    # 3.13.5 is the case a major/minor comparison would accept: its
    # html.parser still splits attributes where a browser does not.
    monkeypatch.setattr(_parser_floor.sys, "version_info", version)
    problem = _parser_floor.unsupported_interpreter_problem()
    assert problem is not None
    assert ".".join(str(part) for part in version[:3]) in problem, problem
    assert FLOOR_TEXT in problem, problem


def test_exact_floor_is_accepted(monkeypatch):
    monkeypatch.setattr(
        _parser_floor.sys, "version_info", PYTHON_FLOOR + ("final", 0)
    )
    assert _parser_floor.unsupported_interpreter_problem() is None


# A PRE-RELEASE must be refused whatever its numbers say. Measured on real
# CPython 3.14.0b1: version_info[:3] == (3, 14, 0), which is ABOVE the
# floor, yet its html.parser fails all three disagreements the floor exists
# to rule out and jimemo#y9p8's payload lints clean on it. gh-69426 landed
# in 3.14.0b2.
PRERELEASES = [
    (3, 14, 0, "beta", 1),
    (3, 14, 0, "alpha", 7),
    (3, 15, 0, "candidate", 1),
    PYTHON_FLOOR + ("candidate", 1),
    PYTHON_FLOOR + ("beta", 2),
]


@pytest.mark.parametrize(
    "version", PRERELEASES, ids=lambda v: _parser_floor.running_version(v)
)
def test_prereleases_are_refused_even_above_the_floor(version, monkeypatch):
    monkeypatch.setattr(_parser_floor.sys, "version_info", version)
    problem = _parser_floor.unsupported_interpreter_problem()
    assert problem is not None, version
    assert "pre-release" in problem, problem
    assert version[3] in problem, problem
    assert _parser_floor.running_version(version) in problem, problem
    with pytest.raises(RuntimeError):
        _parser_floor.assert_interpreter_is_supported()


@pytest.mark.parametrize(
    "version, expected",
    [
        ((3, 14, 7, "final", 0), "3.14.7"),
        ((3, 13, 6, "final", 0), "3.13.6"),
        ((3, 14, 0, "beta", 1), "3.14.0b1"),
        ((3, 14, 0, "alpha", 3), "3.14.0a3"),
        ((3, 13, 6, "candidate", 2), "3.13.6rc2"),
    ],
)
def test_running_version_spells_it_as_cpython_does(version, expected):
    # platform.python_version() reports 3.14.0b1 as "3.14.0", which is the
    # ambiguity that let a pre-release look supported. This builds the
    # string from version_info so the releaselevel survives.
    assert _parser_floor.running_version(version) == expected


# --- importing lint really crosses the boundary (fresh processes) ---------
#
# Each of these raises the floor in a fresh process and then imports
# jimemo.lint for real. lint imports jimemo.sanitize before it reaches its
# own module-level assert_interpreter_is_supported() call, so since
# jimemo#dexg sanitize's call is the one that fires first: these tests
# guard the boundary at lint's import, not lint's own line. Deleting BOTH
# calls would make every one fail -- the regression they exist to catch.


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


# --- importing sanitize crosses the same boundary (jimemo#dexg) ---------
#
# sanitize.py parses HTML with html.parser exactly as lint does (and lint
# itself parses through sanitize's normalizers), so the floor lint calls
# at import is sanitize's boundary too: a direct caller -- `from
# jimemo.sanitize import sanitize_html` -- must not get answers from a
# parser that disagrees with the browser. Same RAISE_FLOOR form as the
# lint tests above, and the same proof that a supported interpreter
# imports cleanly.


def test_importing_sanitize_raises_when_the_interpreter_is_below_the_floor():
    result = _fresh(RAISE_FLOOR + "import jimemo.sanitize\n")
    assert result.returncode != 0, result.stdout
    assert "RuntimeError" in result.stderr, result.stderr
    assert "99.0.0" in result.stderr, result.stderr
    # The SAME RuntimeError lint's boundary raises, not a reworded copy
    # that can drift: one message from one function (assert_interpreter_
    # is_supported), whichever module's import trips it.
    raised = [
        line for line in result.stderr.splitlines()
        if line.startswith("RuntimeError:")
    ]
    lint_raised = [
        line
        for line in _fresh(RAISE_FLOOR + "import jimemo.lint\n").stderr.splitlines()
        if line.startswith("RuntimeError:")
    ]
    assert raised == lint_raised, (raised, lint_raised)


def test_importing_sanitize_succeeds_on_this_interpreter():
    result = _fresh("import jimemo.sanitize")
    assert result.returncode == 0, result.stderr


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


@pytest.mark.skipif(
    not SUB_FLOOR_PYTHONS,
    reason="this machine has no Python below the floor",
)
@pytest.mark.parametrize(
    "version, executable", SUB_FLOOR_PYTHONS[:1], ids=lambda value: str(value)
)
def test_doctor_reports_the_sanitize_refusal_on_a_real_sub_floor_interpreter(
    version, executable
):
    # jimemo#dexg moves the import boundary into jimemo.sanitize, which
    # `jimemo doctor` reaches through content.py (its module-level `from
    # .sanitize import sanitize_html`) inside the markdown-render
    # try/except. The boundary must surface there as one more FAIL line,
    # never as a traceback: a doctor that dies below the floor cannot
    # report the problem it exists to report.
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
    # And the new boundary itself is REPORTED: importing jimemo.content
    # trips jimemo.sanitize's floor call, whose RuntimeError lands in
    # doctor's except -- one FAIL line naming the floor, not a crash.
    markdown = [
        line for line in result.stdout.splitlines()
        if "markdown render path" in line
    ]
    assert markdown, result.stdout
    assert markdown[0].startswith("FAIL markdown render path: "), markdown[0]
    assert FLOOR_TEXT in markdown[0], markdown[0]
