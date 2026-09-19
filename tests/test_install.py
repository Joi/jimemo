"""install.sh is bash, not Python, so these tests drive the real script
via subprocess with HOME pointed at a temp directory (same technique as
tests/test_setup.py's IO injection, but for a whole-process boundary
instead of a Python object). Never touches the real ~/.claude, ~/.codex,
~/.amplifier, or ~/.local/bin.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"

sys.path.insert(0, str(REPO_ROOT / "src"))

from jimemo import PYTHON_FLOOR  # noqa: E402

# Discovered once in conftest.py, which owns the reasoning.
from conftest import SUB_FLOOR_PYTHONS  # noqa: E402


def run_install(args, home: Path, extra_env=None):
    env = dict(os.environ)
    env["HOME"] = str(home)
    # Never let the real machine's Amplifier install leak into a test.
    env.pop("AMPLIFIER_SKILLS_DIR", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(INSTALL_SH), *args],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def cli_target(home: Path) -> Path:
    return home / ".local" / "bin" / "jimemo"


def claude_target(home: Path) -> Path:
    return home / ".claude" / "skills" / "jimemo"


def codex_target(home: Path) -> Path:
    return home / ".codex" / "skills" / "jimemo"


def amplifier_target(home: Path) -> Path:
    return home / ".amplifier" / "skills" / "jimemo"


def assert_symlink_to(path: Path, expected_target: Path):
    assert path.is_symlink(), f"{path} is not a symlink"
    assert os.readlink(path) == str(expected_target), (
        f"{path} -> {os.readlink(path)}, expected -> {expected_target}"
    )


def test_dry_run_prints_planned_symlinks_and_creates_nothing(tmp_path):
    result = run_install(["--dry-run"], tmp_path)

    assert result.returncode == 0, result.stderr
    assert str(cli_target(tmp_path)) in result.stdout
    assert str(claude_target(tmp_path)) in result.stdout
    assert str(codex_target(tmp_path)) in result.stdout
    assert str(REPO_ROOT / "jimemo") in result.stdout
    assert str(REPO_ROOT / "skill") in result.stdout

    # Nothing was actually created.
    assert not (tmp_path / ".local").exists()
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".codex").exists()


def test_real_run_creates_expected_symlinks(tmp_path):
    result = run_install([], tmp_path)

    assert result.returncode == 0, result.stderr
    assert_symlink_to(cli_target(tmp_path), REPO_ROOT / "jimemo")
    assert_symlink_to(claude_target(tmp_path), REPO_ROOT / "skill")
    assert_symlink_to(codex_target(tmp_path), REPO_ROOT / "skill")
    # No ~/.amplifier in this temp HOME, so it must not be created.
    assert not (tmp_path / ".amplifier").exists()
    assert "jimemo doctor" in result.stdout


def test_real_run_registers_amplifier_when_detected(tmp_path):
    (tmp_path / ".amplifier").mkdir()

    result = run_install([], tmp_path)

    assert result.returncode == 0, result.stderr
    assert_symlink_to(amplifier_target(tmp_path), REPO_ROOT / "skill")


def test_real_run_notes_amplifier_when_not_detected(tmp_path):
    result = run_install([], tmp_path)

    assert result.returncode == 0, result.stderr
    assert "Amplifier not detected" in result.stdout
    assert not (tmp_path / ".amplifier").exists()


def test_running_twice_is_idempotent(tmp_path):
    first = run_install([], tmp_path)
    second = run_install([], tmp_path)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert_symlink_to(cli_target(tmp_path), REPO_ROOT / "jimemo")
    assert_symlink_to(claude_target(tmp_path), REPO_ROOT / "skill")
    assert_symlink_to(codex_target(tmp_path), REPO_ROOT / "skill")


def test_uninstall_removes_exactly_those_symlinks(tmp_path):
    run_install([], tmp_path)
    # An unrelated file that must survive uninstall untouched.
    sentinel = tmp_path / ".claude" / "skills" / "other-skill"
    sentinel.mkdir(parents=True)
    (sentinel / "SKILL.md").write_text("unrelated skill\n")

    result = run_install(["--uninstall"], tmp_path)

    assert result.returncode == 0, result.stderr
    assert not cli_target(tmp_path).exists()
    assert not claude_target(tmp_path).exists()
    assert not codex_target(tmp_path).exists()
    # Parent directories and unrelated content are left alone.
    assert sentinel.is_dir()
    assert (sentinel / "SKILL.md").read_text() == "unrelated skill\n"


def test_uninstall_dry_run_removes_nothing(tmp_path):
    run_install([], tmp_path)

    result = run_install(["--uninstall", "--dry-run"], tmp_path)

    assert result.returncode == 0, result.stderr
    assert "would remove" in result.stdout
    assert_symlink_to(cli_target(tmp_path), REPO_ROOT / "jimemo")
    assert_symlink_to(claude_target(tmp_path), REPO_ROOT / "skill")
    assert_symlink_to(codex_target(tmp_path), REPO_ROOT / "skill")


def test_uninstall_on_empty_home_is_a_harmless_noop(tmp_path):
    result = run_install(["--uninstall"], tmp_path)

    assert result.returncode == 0, result.stderr


def test_pre_existing_non_symlink_is_warned_and_skipped(tmp_path):
    real_dir = claude_target(tmp_path)
    real_dir.mkdir(parents=True)
    (real_dir / "README.txt").write_text("not jimemo's\n")

    result = run_install([], tmp_path)

    assert result.returncode == 0, result.stderr
    assert "leaving it alone" in result.stderr
    # The real directory is untouched.
    assert not real_dir.is_symlink()
    assert (real_dir / "README.txt").read_text() == "not jimemo's\n"
    # The other harnesses still got linked -- one conflict doesn't
    # block the rest of the install.
    assert_symlink_to(cli_target(tmp_path), REPO_ROOT / "jimemo")
    assert_symlink_to(codex_target(tmp_path), REPO_ROOT / "skill")


def test_pre_existing_non_symlink_survives_uninstall(tmp_path):
    real_dir = claude_target(tmp_path)
    real_dir.mkdir(parents=True)
    (real_dir / "README.txt").write_text("not jimemo's\n")
    run_install([], tmp_path)

    result = run_install(["--uninstall"], tmp_path)

    assert result.returncode == 0, result.stderr
    assert not real_dir.is_symlink()
    assert (real_dir / "README.txt").read_text() == "not jimemo's\n"


def test_symlink_pointing_elsewhere_is_not_removed_by_uninstall(tmp_path):
    target = codex_target(tmp_path)
    target.parent.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    target.symlink_to(elsewhere)

    result = run_install(["--uninstall"], tmp_path)

    assert result.returncode == 0, result.stderr
    assert target.is_symlink()
    assert os.readlink(target) == str(elsewhere)


def test_help_flag(tmp_path):
    result = run_install(["--help"], tmp_path)

    assert result.returncode == 0, result.stderr
    assert "Usage: install.sh" in result.stdout
    assert not (tmp_path / ".local").exists()


def test_unknown_option_errors(tmp_path):
    result = run_install(["--bogus"], tmp_path)

    assert result.returncode != 0
    assert not (tmp_path / ".local").exists()


def test_missing_python3_errors_clearly(tmp_path):
    # A PATH that has the coreutils install.sh needs before its own
    # python3 check (dirname, readlink, for resolving the repo root) but
    # nothing named python3 anywhere on it.
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    for name in ("dirname", "readlink", "mkdir", "ln", "rm", "cat", "basename"):
        real = shutil.which(name)
        if real:
            (fake_bin / name).symlink_to(real)

    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env.pop("AMPLIFIER_SKILLS_DIR", None)
    env["PATH"] = str(fake_bin)

    result = subprocess.run(
        ["/bin/bash", str(INSTALL_SH), "--dry-run"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "python3" in result.stderr


def _fake_bin_with_python3(tmp_path, python3_target=None):
    """A PATH directory holding the coreutils install.sh needs, and
    optionally a `python3` pointing at a specific interpreter."""
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    for name in ("dirname", "readlink", "mkdir", "ln", "rm", "cat", "basename"):
        real = shutil.which(name)
        if real:
            (fake_bin / name).symlink_to(real)
    if python3_target is not None:
        (fake_bin / "python3").symlink_to(python3_target)
    return fake_bin


def _fake_python3_reporting(fake_bin, version, releaselevel="final", serial=0):
    """A `python3` on PATH that reports `version`, whatever the real
    interpreter is. install.sh asks three `python3 -c` questions about the
    version and one `platform.python_version()`; this answers all four.

    This makes the MICRO boundary (3.13.5 refused, 3.13.6 accepted) a
    mandatory test everywhere, including CI, instead of depending on the
    machine happening to have an old build installed. An opportunistic
    sub-floor interpreter usually only exercises the major/minor half of
    the comparison (jimemo#gaga).
    """
    dotted = ".".join(str(part) for part in version)
    if releaselevel != "final":
        dotted += "{0}{1}".format(
            {"alpha": "a", "beta": "b", "candidate": "rc"}[releaselevel], serial
        )
    shim = fake_bin / "python3"
    shim.write_text(
        "#!{real}\n"
        "import collections, sys\n"
        "version_info = collections.namedtuple(\n"
        "    'version_info', 'major minor micro releaselevel serial'\n"
        ")\n"
        "sys.version_info = version_info("
        "{major}, {minor}, {micro}, {level!r}, {serial})\n"
        "sys.version = {dotted!r} + ' (faked)'\n"
        "import platform\n"
        "platform.python_version = lambda: {dotted!r}\n"
        "args = sys.argv[1:]\n"
        "if args and args[0] == '-c':\n"
        "    exec(compile(args[1], '<faked>', 'exec'), {{'__name__': '__main__'}})\n"
        "else:\n"
        "    raise SystemExit('faked python3 only answers -c')\n".format(
            real=sys.executable,
            major=version[0],
            minor=version[1],
            micro=version[2],
            level=releaselevel,
            serial=serial,
            dotted=dotted,
        ),
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim


def _isolated_env(tmp_path, fake_bin):
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env.pop("AMPLIFIER_SKILLS_DIR", None)
    env["PATH"] = str(fake_bin)
    return env


ALL_TARGETS = (
    (".local", "bin", "jimemo"),
    (".claude", "skills", "jimemo"),
    (".codex", "skills", "jimemo"),
    (".amplifier", "skills", "jimemo"),
)


def assert_nothing_installed(home: Path):
    for parts in ALL_TARGETS:
        target = home.joinpath(*parts)
        assert not target.exists() and not target.is_symlink(), target


@pytest.mark.skipif(
    not SUB_FLOOR_PYTHONS,
    reason="this machine has no Python below the floor to test the refusal with",
)
@pytest.mark.parametrize("dry_run", [False, True], ids=["real", "dry-run"])
@pytest.mark.parametrize(
    "version, executable", SUB_FLOOR_PYTHONS, ids=lambda value: str(value)
)
def test_sub_floor_python3_is_refused(version, executable, dry_run, tmp_path):
    # Same fake-PATH technique as test_missing_python3_errors_clearly, but
    # with a python3 that EXISTS and is too old (jimemo#gaga).
    #
    # The REAL run (no --dry-run) is the one that proves anything: with
    # --dry-run, install.sh creates nothing regardless, so "nothing was
    # installed" would also hold if the floor check ran AFTER the install
    # actions. Both are parametrised so the dry-run path is covered too.
    fake_bin = _fake_bin_with_python3(tmp_path, executable)
    args = ["--dry-run"] if dry_run else []

    result = subprocess.run(
        ["/bin/bash", str(INSTALL_SH), *args],
        cwd=str(tmp_path),
        env=_isolated_env(tmp_path, fake_bin),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0, result.stdout
    floor_text = ".".join(str(part) for part in PYTHON_FLOOR)
    assert floor_text in result.stderr, result.stderr
    assert ".".join(str(part) for part in version) in result.stderr, result.stderr
    assert "Traceback" not in result.stderr
    assert_nothing_installed(tmp_path)


# Hardcoded historical versions PLUS the release immediately below the
# floor, derived so a floor bump keeps testing its own boundary instead of
# a stale one.
_JUST_BELOW_FLOOR = (
    (PYTHON_FLOOR[0], PYTHON_FLOOR[1], PYTHON_FLOOR[2] - 1)
    if PYTHON_FLOOR[2] > 0
    else (PYTHON_FLOOR[0], PYTHON_FLOOR[1] - 1, 0)
)
SUB_FLOOR_VERSIONS = sorted(
    {(3, 9, 6), (3, 12, 11), (3, 13, 0), (3, 13, 3), _JUST_BELOW_FLOOR}
)


@pytest.mark.parametrize(
    "version", SUB_FLOOR_VERSIONS, ids=lambda v: ".".join(str(p) for p in v)
)
def test_install_refuses_every_version_below_the_floor(version, tmp_path):
    # Mandatory, machine-independent coverage of the version comparison,
    # including 3.13.5 -- one release below the floor, and the case a
    # major/minor comparison would wrongly accept. A real run, not
    # --dry-run: with --dry-run nothing is created either way, so "nothing
    # was installed" would also hold if the check ran after the install.
    fake_bin = _fake_bin_with_python3(tmp_path, None)
    _fake_python3_reporting(fake_bin, version)

    result = subprocess.run(
        ["/bin/bash", str(INSTALL_SH)],
        cwd=str(tmp_path),
        env=_isolated_env(tmp_path, fake_bin),
        capture_output=True,
        text=True,
        timeout=60,
    )

    dotted = ".".join(str(part) for part in version)
    floor_text = ".".join(str(part) for part in PYTHON_FLOOR)
    assert result.returncode != 0, result.stdout
    assert dotted in result.stderr, result.stderr
    assert floor_text in result.stderr, result.stderr
    assert "Traceback" not in result.stderr
    assert_nothing_installed(tmp_path)


@pytest.mark.parametrize(
    "stdout",
    [
        "surprise banner\n",
        "",
        "3 13 6\n",
        "3 13 6 final\n",
        "not a version at all\n",
        # Digits, but not an integer bash 3.2's `test` can represent: it
        # rejects an overflowing number exactly as it rejects a word, with
        # status 2, so a digits-only guard would let this install.
        "3 13 99999999999999999999 final 3.13.5\n",
        # A glob metacharacter: an unquoted `set -- $PY_PARTS` would expand
        # it against the cwd and could take the floor verdict from the
        # filesystem. `read` does not glob.
        "3 13 [0-9] final 3.13.glob\n",
        # More fields than asked for.
        "3 13 6 final 3.13.6 extra\n",
        # A plausible first line followed by more output: `read` takes one
        # line, so the rest would be silently dropped.
        "3 13 6 final 3.13.6\n3 12 11 final 3.12.11\n",
        # A component that is not a bare decimal.
        "3 13 0x6 final 3.13.6\n",
    ],
    ids=[
        "banner",
        "empty",
        "missing-two-fields",
        "missing-version-string",
        "words",
        "overflowing-micro",
        "glob-metachar",
        "extra-field",
        "multiline",
        "hex-micro",
    ],
)
def test_install_refuses_a_python3_whose_version_it_cannot_read(stdout, tmp_path):
    # A floor check must fail CLOSED. If python3 exits 0 but prints
    # something unexpected, the version components are non-numeric; every
    # `[ … -lt … ]` then fails with status 2, the whole `if` evaluates
    # false (set -e does not apply inside an if condition), and without an
    # explicit guard install.sh would proceed to install (jimemo#gaga).
    fake_bin = _fake_bin_with_python3(tmp_path, None)
    shim = fake_bin / "python3"
    shim.write_text(
        '#!/bin/sh\ncat <<"EOF"\n' + stdout + "EOF\nexit 0\n", encoding="utf-8"
    )
    shim.chmod(0o755)

    result = subprocess.run(
        ["/bin/bash", str(INSTALL_SH)],
        cwd=str(tmp_path),
        env=_isolated_env(tmp_path, fake_bin),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0, result.stdout
    # Either the generic parse refusal or the multiline-specific one. Both
    # name the floor, and both must install nothing.
    assert (
        "could not read python3's version" in result.stderr
        or "printed more than one line" in result.stderr
    ), result.stderr
    assert ".".join(str(part) for part in PYTHON_FLOOR) in result.stderr
    assert_nothing_installed(tmp_path)


@pytest.mark.parametrize(
    "version, level, serial",
    [
        ((3, 14, 0), "beta", 1),
        ((3, 15, 0), "alpha", 1),
        (PYTHON_FLOOR, "candidate", 1),
    ],
    ids=["3.14.0b1", "3.15.0a1", "floor-rc1"],
)
def test_install_refuses_a_prerelease_even_above_the_floor(
    version, level, serial, tmp_path
):
    # Measured on real CPython 3.14.0b1: version_info[:3] is ABOVE the
    # floor, yet its html.parser fails all three checks jimemo depends on
    # and jimemo#y9p8's payload lints clean on it (gh-69426 landed in
    # 3.14.0b2). A version number cannot say which pre-release carries
    # which backport, so install.sh takes final releases only.
    fake_bin = _fake_bin_with_python3(tmp_path, None)
    _fake_python3_reporting(fake_bin, version, releaselevel=level, serial=serial)

    result = subprocess.run(
        ["/bin/bash", str(INSTALL_SH)],
        cwd=str(tmp_path),
        env=_isolated_env(tmp_path, fake_bin),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0, result.stdout
    assert "pre-release" in result.stderr, result.stderr
    assert level in result.stderr, result.stderr
    assert_nothing_installed(tmp_path)


def test_install_reports_a_python3_that_exits_non_zero(tmp_path):
    # Under `set -e` a failing command substitution used to abort the
    # script with no output at all -- fail-closed, but the user got
    # nothing to act on (jimemo#gaga).
    fake_bin = _fake_bin_with_python3(tmp_path, None)
    shim = fake_bin / "python3"
    shim.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    shim.chmod(0o755)

    result = subprocess.run(
        ["/bin/bash", str(INSTALL_SH)],
        cwd=str(tmp_path),
        env=_isolated_env(tmp_path, fake_bin),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0
    assert "could not read python3's version" in result.stderr, result.stderr
    assert_nothing_installed(tmp_path)


@pytest.mark.parametrize(
    "version",
    [PYTHON_FLOOR, (PYTHON_FLOOR[0], PYTHON_FLOOR[1], PYTHON_FLOOR[2] + 1)],
    ids=lambda v: ".".join(str(p) for p in v),
)
def test_install_accepts_the_floor_and_above(version, tmp_path):
    # The other half of the boundary: at the floor exactly, and one micro
    # above it, install.sh gets past the version check and installs. Without
    # this, a check that refused EVERYTHING would pass the refusal tests.
    fake_bin = _fake_bin_with_python3(tmp_path, None)
    _fake_python3_reporting(fake_bin, version)

    result = subprocess.run(
        ["/bin/bash", str(INSTALL_SH)],
        cwd=str(tmp_path),
        env=_isolated_env(tmp_path, fake_bin),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "requires Python" not in result.stderr, result.stderr
    assert_symlink_to(cli_target(tmp_path), REPO_ROOT / "jimemo")


@pytest.mark.skipif(
    not SUB_FLOOR_PYTHONS,
    reason="this machine has no Python below the floor to uninstall with",
)
@pytest.mark.parametrize(
    "version, executable", SUB_FLOOR_PYTHONS[:1], ids=lambda value: str(value)
)
def test_uninstall_works_below_the_floor(version, executable, tmp_path):
    # Raising the floor must not take away the way out: a machine whose
    # python3 is too old to INSTALL must still be able to remove the
    # symlinks a previous install left behind, or the user is stuck with
    # dangling links and no supported command to clear them (jimemo#gaga).
    # Uninstall touches no python3 at all.
    fake_bin = _fake_bin_with_python3(tmp_path, executable)
    env = _isolated_env(tmp_path, fake_bin)

    # Plant exactly what a previous install would have left.
    planted = []
    for parts in ALL_TARGETS:
        target = tmp_path.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = REPO_ROOT / "jimemo" if parts[0] == ".local" else REPO_ROOT / "skill"
        target.symlink_to(source)
        planted.append(target)
    # ... plus a file install.sh does not own, which must survive.
    bystander = tmp_path / ".claude" / "skills" / "somebody-elses"
    bystander.write_text("keep me", encoding="utf-8")

    result = subprocess.run(
        ["/bin/bash", str(INSTALL_SH), "--uninstall"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    for target in planted:
        assert not target.is_symlink(), target
    assert bystander.read_text(encoding="utf-8") == "keep me"


def test_uninstall_works_with_no_python3_at_all(tmp_path):
    # Same rule, harsher case: no python3 anywhere on PATH.
    fake_bin = _fake_bin_with_python3(tmp_path, None)
    cli = tmp_path / ".local" / "bin" / "jimemo"
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.symlink_to(REPO_ROOT / "jimemo")

    result = subprocess.run(
        ["/bin/bash", str(INSTALL_SH), "--uninstall"],
        cwd=str(tmp_path),
        env=_isolated_env(tmp_path, fake_bin),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert not cli.is_symlink()


@pytest.mark.skipif(sys.platform == "win32", reason="bash script, POSIX only")
def test_install_sh_is_executable():
    mode = INSTALL_SH.stat().st_mode
    assert mode & 0o111, "install.sh should be executable"
