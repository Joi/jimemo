"""jimemo's Python floor is written down in four places that cannot import
each other: the package constant, the ``./jimemo`` launcher (it runs before
``src/`` is on the path), ``install.sh`` (bash), and the CI matrix (YAML).
These tests pin all four to one value, and drive the two executables under
real sub-floor interpreters, so a floor bump that updates three of the four
fails here instead of on someone's machine.

Why the floor has a MICRO component at all: see ``src/jimemo/__init__.py``.
"""

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo import PYTHON_FLOOR

from conftest import SUB_FLOOR_PYTHONS

REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = REPO_ROOT / "jimemo"
INSTALL_SH = REPO_ROOT / "install.sh"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

FLOOR_TEXT = ".".join(str(part) for part in PYTHON_FLOOR)


# --- the four spellings agree ---------------------------------------------


def _launcher_floor_tuple():
    """The ``(3, 13, 6)`` literal the launcher compares against, read out of
    its source with ast (never by importing it -- importing the launcher
    would run it)."""
    tree = ast.parse(LAUNCHER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        # The tuple assigned to _FLOOR, BY NAME -- not "the first tuple
        # literal in the file", which would silently follow any tuple that
        # appeared above it later.
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_FLOOR"
            for target in node.targets
        ):
            assert isinstance(node.value, ast.Tuple), ast.dump(node.value)
            return tuple(elt.value for elt in node.value.elts)
    raise AssertionError("the launcher no longer assigns _FLOOR")


def test_launcher_literal_matches_the_package_constant():
    assert _launcher_floor_tuple() == PYTHON_FLOOR


def test_install_sh_floor_matches_the_package_constant():
    # install.sh compares PY_MAJOR/PY_MINOR/PY_MICRO against the three
    # components with -lt and -eq; pull every integer it compares them to.
    text = INSTALL_SH.read_text(encoding="utf-8")
    major = re.findall(r'"\$PY_MAJOR" -lt ([0-9]+)', text)
    minor = re.findall(r'"\$PY_MINOR" -lt ([0-9]+)', text)
    micro = re.findall(r'"\$PY_MICRO" -lt ([0-9]+)', text)
    assert major and minor and micro, "install.sh lost a version comparison"
    found = (int(major[0]), int(minor[0]), int(micro[0]))
    assert found == PYTHON_FLOOR
    # The -eq operands matter just as much: a floor bump that updates the
    # three -lt numbers but leaves `-eq 13` behind would accept every
    # release in the OLD minor series above the new micro. The faked-python3
    # tests in test_install.py catch that behaviourally; this catches it in
    # the source, where the mistake is made.
    eq_major = re.findall(r'"\$PY_MAJOR" -eq ([0-9]+)', text)
    eq_minor = re.findall(r'"\$PY_MINOR" -eq ([0-9]+)', text)
    assert eq_major, "install.sh lost its major -eq guard"
    assert eq_minor, "install.sh lost its minor -eq guard"
    assert {int(v) for v in eq_major} == {PYTHON_FLOOR[0]}, eq_major
    assert {int(v) for v in eq_minor} == {PYTHON_FLOOR[1]}, eq_minor
    # And the human-readable floor appears in both error messages.
    assert text.count(FLOOR_TEXT) >= 2, text


def test_ci_matrix_covers_the_floor_boundary():
    # The matrix must name the EXACT floor, not a floating "3.13": a
    # floating minor stops testing the boundary as soon as the runner's
    # 3.13 moves past it, and 3.13.0-3.13.5 are below the floor.
    text = CI_YML.read_text(encoding="utf-8")
    versions = re.search(r"python-version:\s*\[([^\]]*)\]", text)
    assert versions, "ci.yml has no python-version matrix"
    listed = [v.strip().strip('"\'') for v in versions.group(1).split(",")]
    assert FLOOR_TEXT in listed, listed
    for entry in listed:
        parts = tuple(int(part) for part in entry.split("."))
        # A two-component entry ("3.14") is a whole minor series, and is
        # acceptable only when every release in it is at or above the
        # floor; a three-component entry is compared outright.
        bound = PYTHON_FLOOR[: len(parts)]
        assert parts >= bound, (entry, FLOOR_TEXT)


# --- the launcher checks the floor before it imports anything -------------


def test_launcher_checks_the_floor_before_importing_jimemo():
    tree = ast.parse(LAUNCHER.read_text(encoding="utf-8"))
    guard_line = None
    path_insert_line = None
    cli_import_line = None
    for node in ast.walk(tree):
        if (
            guard_line is None
            and isinstance(node, ast.Attribute)
            and node.attr == "version_info"
        ):
            guard_line = node.lineno
        if (
            path_insert_line is None
            and isinstance(node, ast.Attribute)
            and node.attr == "insert"
        ):
            path_insert_line = node.lineno
        if (
            cli_import_line is None
            and isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("jimemo")
        ):
            cli_import_line = node.lineno
    assert guard_line is not None, "the launcher no longer reads sys.version_info"
    assert path_insert_line is not None and cli_import_line is not None
    assert guard_line < path_insert_line, (guard_line, path_insert_line)
    assert guard_line < cli_import_line, (guard_line, cli_import_line)


def test_launcher_uses_no_f_strings():
    # The guard has to be parseable by whatever old python3 the user has,
    # so the whole launcher stays on str.format.
    tree = ast.parse(LAUNCHER.read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)]


def test_launcher_runs_on_this_interpreter():
    # The suite runs at or above the floor, so the guard must not misfire.
    result = subprocess.run(
        [sys.executable, str(LAUNCHER), "--version"],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


# --- real sub-floor interpreters ------------------------------------------


def _run_launcher_as(faked_version, argv):
    """Run the REAL launcher source with ``sys.version_info`` faked, so the
    floor boundary is tested at exact versions instead of at whatever
    interpreters this machine happens to have installed. The micro
    boundary (3.13.5 versus 3.13.6) is the whole point of the floor, and
    almost no machine has a 3.13.5 lying around."""
    faker = (
        "import collections, sys\n"
        "VI = collections.namedtuple("
        "'version_info', 'major minor micro releaselevel serial')\n"
        "sys.version_info = VI(%d, %d, %d, 'final', 0)\n"
        "sys.argv = [%r] + %r\n"
        "path = %r\n"
        "exec(\n"
        "    compile(open(path).read(), path, 'exec'),\n"
        "    {'__name__': '__main__', '__file__': path},\n"
        ")\n"
        % (
            faked_version[0],
            faked_version[1],
            faked_version[2],
            str(LAUNCHER),
            list(argv),
            str(LAUNCHER),
        )
    )
    return subprocess.run(
        [sys.executable, "-c", faker],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


@pytest.mark.parametrize(
    "faked",
    [(3, 9, 6), (3, 12, 11), (3, 13, 0), (3, 13, 3), (3, 13, 5)],
    ids=lambda value: ".".join(str(part) for part in value),
)
def test_launcher_refuses_every_version_below_the_floor(faked):
    # 3.13.5 is the case a major/minor-only check accepts wrongly: its
    # html.parser still splits attributes where a browser does not
    # (jimemo#gaga). 3.13.0 and 3.13.3 additionally still decode
    # semicolonless attribute references and drop unclosed <style> text.
    result = _run_launcher_as(faked, ["doctor"])
    assert result.returncode != 0, result.stdout
    assert result.stdout == "", result.stdout
    lines = [line for line in result.stderr.splitlines() if line.strip()]
    assert len(lines) == 1, result.stderr
    assert "Traceback" not in result.stderr
    assert FLOOR_TEXT in lines[0], lines[0]
    assert ".".join(str(part) for part in faked) in lines[0], lines[0]


def test_launcher_accepts_the_exact_floor():
    result = _run_launcher_as(PYTHON_FLOOR, ["--version"])
    assert result.returncode == 0, result.stderr
    assert result.stderr == "", result.stderr


@pytest.mark.skipif(
    not SUB_FLOOR_PYTHONS,
    reason="this machine has no Python below the floor to test the refusal with",
)
@pytest.mark.parametrize(
    "version, executable", SUB_FLOOR_PYTHONS, ids=lambda value: str(value)
)
def test_launcher_refuses_sub_floor_interpreters(version, executable):
    result = subprocess.run(
        [executable, str(LAUNCHER), "doctor"],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode != 0, result.stdout
    assert result.stdout == "", result.stdout
    # ONE line, no traceback: a sub-floor interpreter gets a sentence, not
    # a stack trace, and never a silent run.
    lines = [line for line in result.stderr.splitlines() if line.strip()]
    assert len(lines) == 1, result.stderr
    assert "Traceback" not in result.stderr
    assert FLOOR_TEXT in lines[0], lines[0]
    assert ".".join(str(part) for part in version) in lines[0], lines[0]
