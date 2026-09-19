"""install.sh is bash, not Python, so these tests drive the real script
via subprocess with HOME pointed at a temp directory (same technique as
tests/test_setup.py's IO injection, but for a whole-process boundary
instead of a Python object). Never touches the real ~/.claude, ~/.codex,
~/.amplifier, or ~/.local/bin.

Since jimemo#p0nk the CLI target is not a symlink but a generated shell
script bound to one interpreter, so several tests run that entry point
too: the proof that the binding works is `jimemo --version` succeeding
under a PATH whose python3 would refuse.
"""

import os
import re
import shutil
import stat
import subprocess
import sys
import venv
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"
LAUNCHER = REPO_ROOT / "jimemo"

sys.path.insert(0, str(REPO_ROOT / "src"))

import jimemo  # noqa: E402
from jimemo import PYTHON_FLOOR  # noqa: E402

# Discovered once in conftest.py, which owns the reasoning.
from conftest import SUB_FLOOR_PYTHONS  # noqa: E402

FLOOR_TEXT = ".".join(str(part) for part in PYTHON_FLOOR)

# The coreutils install.sh needs on a minimal PATH (dirname/readlink for the
# repo root; mkdir/ln/rm for the skills; cat/chmod/mv for the entry point).
# A fake-bin PATH holds exactly these, so a test proves the script needs
# nothing else.
COREUTILS = ("dirname", "readlink", "mkdir", "ln", "rm", "cat", "chmod", "mv")

# A sentinel default for _fake_python3_reporting's `executable`: "leave
# sys.executable alone" has to be distinguishable from "answer None".
_KEEP = object()

# Variables install.sh and the launcher now read. Every helper that builds
# a subprocess environment drops them first: a developer with JIMEMO_PYTHON
# exported would otherwise turn every candidate-selection test into an
# override test without any test noticing.
INSTALLER_ENV_VARS = ("JIMEMO_PYTHON", "JIMEMO_ENTRY_POINT", "AMPLIFIER_SKILLS_DIR")


def _base_env(home: Path):
    env = dict(os.environ)
    env["HOME"] = str(home)
    for name in INSTALLER_ENV_VARS:
        env.pop(name, None)
    return env


def run_install(args, home: Path, extra_env=None, script=INSTALL_SH):
    env = _base_env(home)
    if extra_env:
        env.update(extra_env)
    # /bin/bash explicitly: on a Mac with Homebrew's bash first on PATH,
    # `bash` would skip the 3.2 the script promises to run on.
    return subprocess.run(
        ["/bin/bash", str(script), *args],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
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


# --- the entry point ---------------------------------------------------------

MARKER = "# jimemo-entry-point:"


def read_entry_point_header(path: Path):
    """(python, launcher) from the comment header install.sh writes. Reads
    the file the way the installer's own --uninstall and `jimemo doctor`
    do: marker first, then the two value lines, all within ten lines."""
    lines = path.read_text(encoding="utf-8").splitlines()[:10]
    assert any(line.startswith(MARKER) for line in lines), lines
    python = launcher = None
    for line in lines:
        if line.startswith("# python: "):
            python = line[len("# python: "):]
        elif line.startswith("# launcher: "):
            launcher = line[len("# launcher: "):]
    assert python is not None and launcher is not None, lines
    return python, launcher


def assert_entry_point(home: Path, launcher: Path = LAUNCHER):
    """The CLI target is a regular, executable, marker-carrying script bound
    to an absolute, existing interpreter and to `launcher`."""
    target = cli_target(home)
    assert target.is_file() and not target.is_symlink(), target
    assert target.stat().st_mode & stat.S_IXUSR, "entry point is not executable"
    assert target.read_text(encoding="utf-8").startswith("#!/bin/sh\n")
    python, recorded = read_entry_point_header(target)
    assert recorded == str(launcher), (recorded, launcher)
    assert python.startswith("/"), python
    assert os.access(python, os.X_OK), python
    return python


def run_entry_point(home: Path, args, env=None):
    """Run the installed entry point exactly as a shell would."""
    run_env = _base_env(home) if env is None else env
    return subprocess.run(
        [str(cli_target(home)), *args],
        cwd=str(home),
        env=run_env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def bare_env(home: Path):
    """A launchd-style environment: HOME and a PATH with no user python."""
    return {"HOME": str(home), "PATH": "/usr/bin:/bin"}


def make_venv(where: Path) -> Path:
    """A real venv (no pip) whose bin/python reports ITSELF as
    sys.executable -- the one kind of interpreter path a test can create,
    bind, and then delete or replace."""
    venv.EnvBuilder(with_pip=False, symlinks=True).create(str(where))
    python = where / "bin" / "python"
    assert python.exists(), python
    return python


def replace_venv_python(venv_python: Path, shim_text: str):
    """Replace a venv's python with a shim. With symlinks=True that path IS
    a symlink to the base interpreter, so it is unlinked FIRST: opening it
    for writing would follow the link and truncate the Python running this
    suite."""
    assert venv_python.is_symlink(), venv_python
    venv_python.unlink()
    venv_python.write_text(shim_text, encoding="utf-8")
    venv_python.chmod(0o755)


def faking_shim(version, releaselevel="final", serial=0):
    """Text of an interpreter shim that fakes `version` and can both answer
    `-c` (the installer's questions) and run a script (the launcher)."""
    return (
        "#!{real}\n"
        "import collections, sys\n"
        "VI = collections.namedtuple('version_info', 'major minor micro releaselevel serial')\n"
        "sys.version_info = VI({major}, {minor}, {micro}, {level!r}, {serial})\n"
        "sys.version = {dotted!r} + ' (faked)'\n"
        "args = sys.argv[1:]\n"
        "if args and args[0] == '-c':\n"
        "    exec(compile(args[1], '<faked>', 'exec'), {{'__name__': '__main__'}})\n"
        "else:\n"
        "    path = args[0]\n"
        "    sys.argv = args\n"
        "    exec(compile(open(path).read(), path, 'exec'),\n"
        "         {{'__name__': '__main__', '__file__': path}})\n"
    ).format(
        real=sys.executable,
        major=version[0],
        minor=version[1],
        micro=version[2],
        level=releaselevel,
        serial=serial,
        dotted=".".join(str(part) for part in version),
    )


# --- the unchanged half: skills, dry-run, idempotence -----------------------


def test_dry_run_prints_planned_actions_and_creates_nothing(tmp_path):
    result = run_install(["--dry-run"], tmp_path)

    assert result.returncode == 0, result.stderr
    assert "would write entry point" in result.stdout
    assert str(cli_target(tmp_path)) in result.stdout
    assert str(claude_target(tmp_path)) in result.stdout
    assert str(codex_target(tmp_path)) in result.stdout
    assert str(LAUNCHER) in result.stdout
    assert str(REPO_ROOT / "skill") in result.stdout

    # Nothing was actually created.
    assert not (tmp_path / ".local").exists()
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".codex").exists()


def test_real_run_writes_the_entry_point_and_skill_symlinks(tmp_path):
    result = run_install([], tmp_path)

    assert result.returncode == 0, result.stderr
    python = assert_entry_point(tmp_path)
    # The binding is announced, with the interpreter it chose.
    assert f"-> {python}" in result.stdout, result.stdout
    assert_symlink_to(claude_target(tmp_path), REPO_ROOT / "skill")
    assert_symlink_to(codex_target(tmp_path), REPO_ROOT / "skill")
    # No ~/.amplifier in this temp HOME, so it must not be created.
    assert not (tmp_path / ".amplifier").exists()
    assert "jimemo doctor" in result.stdout


def test_installed_entry_point_runs(tmp_path):
    run_install([], tmp_path)

    result = run_entry_point(tmp_path, ["--version"])
    assert result.returncode == 0, result.stderr
    assert jimemo.__version__ in result.stdout
    assert result.stderr == ""

    # And with no user python on PATH at all: the binding is absolute.
    result = run_entry_point(tmp_path, ["--version"], env=bare_env(tmp_path))
    assert result.returncode == 0, result.stderr
    assert jimemo.__version__ in result.stdout


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
    assert_entry_point(tmp_path)
    assert not list(cli_target(tmp_path).parent.glob("jimemo.tmp*"))
    assert_symlink_to(claude_target(tmp_path), REPO_ROOT / "skill")
    assert_symlink_to(codex_target(tmp_path), REPO_ROOT / "skill")


def test_uninstall_removes_exactly_what_it_wrote(tmp_path):
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
    assert_entry_point(tmp_path)
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
    # The other targets still got installed -- one conflict doesn't block
    # the rest of the install.
    assert_entry_point(tmp_path)
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
    # The interpreter contract is documented where the user looks first.
    for name in ("python3", "python3.13", "python3.14"):
        assert name in result.stdout, name
    assert "--python PATH" in result.stdout
    assert "JIMEMO_PYTHON" in result.stdout
    assert not (tmp_path / ".local").exists()


def test_unknown_option_errors(tmp_path):
    result = run_install(["--bogus"], tmp_path)

    assert result.returncode != 0
    assert not (tmp_path / ".local").exists()


def test_python_flag_without_a_value_is_a_usage_error(tmp_path):
    result = run_install(["--python"], tmp_path)

    assert result.returncode != 0
    assert "--python needs a path" in result.stderr
    assert not (tmp_path / ".local").exists()


# --- ownership: what install replaces and what uninstall removes ------------


def test_upgrade_from_symlink_install_replaces_the_symlink(tmp_path):
    # Every pre-p0nk install left this exact symlink.
    old = cli_target(tmp_path)
    old.parent.mkdir(parents=True)
    old.symlink_to(LAUNCHER)

    result = run_install([], tmp_path)

    assert result.returncode == 0, result.stderr
    assert not old.is_symlink()
    assert_entry_point(tmp_path)


def test_install_replaces_a_symlink_to_a_directory_at_the_cli_target(tmp_path):
    # `mv new target` onto a symlink-to-directory would move the new file
    # INTO the directory and leave the symlink installed; the installer
    # removes the owned symlink first.
    somedir = tmp_path / "somedir"
    somedir.mkdir()
    old = cli_target(tmp_path)
    old.parent.mkdir(parents=True)
    old.symlink_to(somedir)

    result = run_install([], tmp_path)

    assert result.returncode == 0, result.stderr
    assert not old.is_symlink()
    assert_entry_point(tmp_path)
    assert list(somedir.iterdir()) == [], "the wrapper landed inside the directory"


def test_install_leaves_a_foreign_regular_file_at_the_cli_target(tmp_path):
    foreign = cli_target(tmp_path)
    foreign.parent.mkdir(parents=True)
    foreign.write_text("#!/bin/sh\necho somebody else's jimemo\n")

    result = run_install([], tmp_path)

    assert result.returncode == 0, result.stderr
    assert "leaving it alone" in result.stderr
    assert foreign.read_text() == "#!/bin/sh\necho somebody else's jimemo\n"
    # The skills were still linked.
    assert_symlink_to(codex_target(tmp_path), REPO_ROOT / "skill")


BINARY_WITH_MARKER_TEXT = (
    b"\x7fELF\x00\x00# jimemo-entry-point: 1\n"
    + f"# launcher: {LAUNCHER}\n".encode()
    + b"\x00\x01\x02 not a script at all\n"
)


def test_ownership_check_does_not_mistake_a_binary_for_its_own_file(tmp_path):
    # bash's `read` silently drops NUL bytes, so a plain line-by-line scan
    # would see the marker text in this binary and call it ours -- then
    # overwrite it on install and delete it on uninstall. The header read
    # stops at the first NUL instead and the file is left alone both ways.
    foreign = cli_target(tmp_path)
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(BINARY_WITH_MARKER_TEXT)

    result = run_install([], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "leaving it alone" in result.stderr
    assert foreign.read_bytes() == BINARY_WITH_MARKER_TEXT

    result = run_install(["--uninstall"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "skip" in result.stderr
    assert foreign.read_bytes() == BINARY_WITH_MARKER_TEXT


def test_ownership_check_is_bounded_on_a_huge_unterminated_line(tmp_path):
    # 8 MiB with no newline and the marker text at the very end: the
    # header read is capped at 4 KiB, so this costs nothing and is not ours.
    foreign = cli_target(tmp_path)
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"x" * (8 * 1024 * 1024) + MARKER.encode() + b" 1\n")

    result = run_install([], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "leaving it alone" in result.stderr
    assert foreign.stat().st_size > 8 * 1024 * 1024

    result = run_install(["--uninstall"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert foreign.exists()


def test_install_does_not_write_through_a_symlink_at_its_temp_name(tmp_path):
    # The wrapper is assembled at <target>.tmp. A symlink planted there
    # must be removed as a path, never opened: `>` would follow it and
    # truncate whatever it points at.
    victim = tmp_path / "victim"
    victim.write_text("precious\n")
    tmp = cli_target(tmp_path).with_name("jimemo.tmp")
    tmp.parent.mkdir(parents=True)
    tmp.symlink_to(victim)

    result = run_install([], tmp_path)

    assert result.returncode == 0, result.stderr
    assert victim.read_text() == "precious\n"
    assert not tmp.exists() and not tmp.is_symlink()
    assert_entry_point(tmp_path)


def test_uninstall_leaves_a_foreign_regular_file_at_the_cli_target(tmp_path):
    foreign = cli_target(tmp_path)
    foreign.parent.mkdir(parents=True)
    foreign.write_text("#!/bin/sh\necho somebody else's jimemo\n")

    result = run_install(["--uninstall"], tmp_path)

    assert result.returncode == 0, result.stderr
    assert "skip" in result.stderr
    assert foreign.exists()


def test_uninstall_leaves_an_entry_point_from_another_checkout(tmp_path):
    # Same marker, different `# launcher:` -- another clone wrote it.
    run_install([], tmp_path)
    target = cli_target(tmp_path)
    text = target.read_text(encoding="utf-8").replace(
        f"# launcher: {LAUNCHER}", "# launcher: /elsewhere/jimemo"
    )
    target.write_text(text, encoding="utf-8")

    result = run_install(["--uninstall"], tmp_path)

    assert result.returncode == 0, result.stderr
    assert target.exists()
    assert "/elsewhere/jimemo" in result.stderr
    assert "leaving it alone" in result.stderr


def test_uninstall_leaves_a_cli_symlink_to_another_checkout(tmp_path):
    # An old-style install from a different clone.
    target = cli_target(tmp_path)
    target.parent.mkdir(parents=True)
    target.symlink_to("/elsewhere/jimemo")

    result = run_install(["--uninstall"], tmp_path)

    assert result.returncode == 0, result.stderr
    assert target.is_symlink()
    assert os.readlink(target) == "/elsewhere/jimemo"
    assert "skip" in result.stderr


def test_entry_point_header_and_assignments_agree(tmp_path):
    run_install([], tmp_path)
    target = cli_target(tmp_path)
    python, launcher = read_entry_point_header(target)
    text = target.read_text(encoding="utf-8")

    def assigned(name):
        m = re.search(rf"^{name}='(.*)'$", text, re.M)
        assert m, text
        return m.group(1).replace("'\\''", "'")

    assert assigned("JIMEMO_PYTHON") == python
    assert assigned("JIMEMO_LAUNCHER") == launcher
    # The wrapper is valid sh, and it exports what the launcher reads.
    assert subprocess.run(["/bin/sh", "-n", str(target)]).returncode == 0
    assert 'JIMEMO_ENTRY_POINT="$0"' in text
    assert 'exec "$JIMEMO_PYTHON" "$JIMEMO_LAUNCHER" "$@"' in text


# --- interpreter selection: verified by running, never by name ---------------


def _fake_bin(tmp_path, python3_target=None):
    """A PATH directory holding the coreutils install.sh needs, and
    optionally a `python3` pointing at a specific interpreter."""
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    for name in COREUTILS:
        real = shutil.which(name)
        assert real, f"{name} not found on this machine's PATH"
        (fake_bin / name).symlink_to(real)
    if python3_target is not None:
        (fake_bin / "python3").symlink_to(python3_target)
    return fake_bin


def _fake_python3_reporting(
    fake_bin, version, releaselevel="final", serial=0, name="python3",
    executable=_KEEP,
):
    """A `NAME` on PATH that reports `version`, whatever the real
    interpreter is, and -- when `executable` is given -- that answer for
    sys.executable. install.sh asks two `-c` questions per candidate (the
    version, then sys.executable) and re-asks the version of the path it
    is about to bind; this shim answers all of them.

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
    executable_line = (
        "" if executable is _KEEP else "sys.executable = {0!r}\n".format(executable)
    )
    shim = fake_bin / name
    shim.write_text(
        "#!{real}\n"
        "import collections, sys\n"
        "version_info = collections.namedtuple(\n"
        "    'version_info', 'major minor micro releaselevel serial'\n"
        ")\n"
        "sys.version_info = version_info("
        "{major}, {minor}, {micro}, {level!r}, {serial})\n"
        "sys.version = {dotted!r} + ' (faked)'\n"
        "{executable_line}"
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
            executable_line=executable_line,
        ),
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim


def _isolated_env(tmp_path, fake_bin):
    env = _base_env(tmp_path)
    env["PATH"] = str(fake_bin)
    return env


def _run_isolated(tmp_path, fake_bin, args=(), extra_env=None):
    env = _isolated_env(tmp_path, fake_bin)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["/bin/bash", str(INSTALL_SH), *args],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


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
    assert not list(home.glob(".local/bin/jimemo.tmp.*"))


def test_missing_python3_errors_clearly(tmp_path):
    # A PATH with the coreutils install.sh needs but nothing named
    # python3, python3.13 or python3.14 anywhere on it.
    fake_bin = _fake_bin(tmp_path)

    result = _run_isolated(tmp_path, fake_bin, ["--dry-run"])

    assert result.returncode != 0
    assert "python3" in result.stderr
    assert "not found on PATH" in result.stderr
    assert_nothing_installed(tmp_path)


def test_install_binds_a_versioned_python_when_python3_is_below_the_floor(tmp_path):
    # The p0nk case: the shell's python3 is too old, a supported
    # python3.13 sits next to it, and nothing on PATH changes.
    fake_bin = _fake_bin(tmp_path)
    _fake_python3_reporting(fake_bin, (3, 12, 11))
    (fake_bin / "python3.13").symlink_to(sys.executable)

    result = _run_isolated(tmp_path, fake_bin)

    assert result.returncode == 0, result.stderr
    assert "python3: is 3.12.11" in result.stderr, result.stderr
    assert "python   python3.13 ->" in result.stdout, result.stdout
    assert_entry_point(tmp_path)
    # (Whether the bound path spells the symlink or its target is the
    # interpreter's call -- stock CPython reports argv[0], Homebrew's
    # reports its opt path -- so the binding itself is not asserted here.)

    # The proof: the installed command works under the same bad PATH ...
    run = run_entry_point(tmp_path, ["--version"], env=_isolated_env(tmp_path, fake_bin))
    assert run.returncode == 0, run.stderr
    assert jimemo.__version__ in run.stdout
    # ... and under a bare one.
    run = run_entry_point(tmp_path, ["--version"], env=bare_env(tmp_path))
    assert run.returncode == 0, run.stderr
    assert jimemo.__version__ in run.stdout


def test_install_falls_through_to_python3_14(tmp_path):
    fake_bin = _fake_bin(tmp_path)
    _fake_python3_reporting(fake_bin, (3, 12, 11))
    _fake_python3_reporting(fake_bin, (3, 13, 5), name="python3.13")
    (fake_bin / "python3.14").symlink_to(sys.executable)

    result = _run_isolated(tmp_path, fake_bin)

    assert result.returncode == 0, result.stderr
    assert "python3: is 3.12.11" in result.stderr, result.stderr
    assert "python3.13: is 3.13.5" in result.stderr, result.stderr
    assert "python   python3.14 ->" in result.stdout, result.stdout
    assert_entry_point(tmp_path)
    run = run_entry_point(tmp_path, ["--version"], env=bare_env(tmp_path))
    assert run.returncode == 0, run.stderr


def test_install_refuses_when_no_candidate_is_good(tmp_path):
    fake_bin = _fake_bin(tmp_path)
    _fake_python3_reporting(fake_bin, (3, 12, 11))
    _fake_python3_reporting(fake_bin, (3, 13, 5), name="python3.13")

    result = _run_isolated(tmp_path, fake_bin)

    assert result.returncode != 0, result.stdout
    # One rejection line per candidate, each with ITS reason.
    assert "python3: is 3.12.11" in result.stderr, result.stderr
    assert "python3.13: is 3.13.5" in result.stderr, result.stderr
    assert "python3.14: not found on PATH" in result.stderr, result.stderr
    assert FLOOR_TEXT in result.stderr
    assert "Traceback" not in result.stderr
    assert_nothing_installed(tmp_path)


def test_python_override_flag_and_env(tmp_path):
    fake_bin = _fake_bin(tmp_path)
    _fake_python3_reporting(fake_bin, (3, 12, 11))  # a python3 that would be refused
    venv_python = make_venv(tmp_path / "venv")

    # --python PATH
    result = _run_isolated(tmp_path, fake_bin, ["--python", str(venv_python)])
    assert result.returncode == 0, result.stderr
    assert assert_entry_point(tmp_path) == str(venv_python)
    run_install(["--uninstall"], tmp_path)

    # --python=PATH
    result = _run_isolated(tmp_path, fake_bin, [f"--python={venv_python}"])
    assert result.returncode == 0, result.stderr
    assert assert_entry_point(tmp_path) == str(venv_python)
    run_install(["--uninstall"], tmp_path)

    # JIMEMO_PYTHON=PATH
    result = _run_isolated(tmp_path, fake_bin, extra_env={"JIMEMO_PYTHON": str(venv_python)})
    assert result.returncode == 0, result.stderr
    assert assert_entry_point(tmp_path) == str(venv_python)
    run_install(["--uninstall"], tmp_path)

    # The flag wins over the variable: the variable names the refused
    # shim, the flag names the venv.
    result = _run_isolated(
        tmp_path,
        fake_bin,
        ["--python", str(venv_python)],
        extra_env={"JIMEMO_PYTHON": str(fake_bin / "python3")},
    )
    assert result.returncode == 0, result.stderr
    assert assert_entry_point(tmp_path) == str(venv_python)


def test_python_override_that_fails_does_not_fall_back(tmp_path):
    # A good python3 IS on PATH; the named interpreter is not good. Naming
    # one means that one: refuse, and never bind the good one instead.
    fake_bin = _fake_bin(tmp_path, sys.executable)
    old = _fake_python3_reporting(fake_bin, (3, 12, 11), name="old-python")

    result = _run_isolated(tmp_path, fake_bin, ["--python", str(old)])

    assert result.returncode != 0, result.stdout
    assert "3.12.11" in result.stderr
    assert str(old) in result.stderr
    assert "no other is tried" in result.stderr, result.stderr
    assert "python   " not in result.stdout
    assert_nothing_installed(tmp_path)

    # Same through the environment.
    result = _run_isolated(tmp_path, fake_bin, extra_env={"JIMEMO_PYTHON": str(old)})
    assert result.returncode != 0, result.stdout
    assert "JIMEMO_PYTHON" in result.stderr
    assert_nothing_installed(tmp_path)


def test_install_refuses_an_interpreter_that_changes_between_probes(tmp_path):
    # The path written into the entry point must be the path that answered
    # the version question LAST. This shim reports itself as its own
    # sys.executable and answers 3.13.6 to the first version question and
    # 3.12.11 to every later one -- an interpreter re-pointed mid-install.
    # Skipping the re-probe when the paths spell the same would bind it.
    fake_bin = _fake_bin(tmp_path)
    shim = fake_bin / "python3"
    counter = tmp_path / "calls"
    shim.write_text(
        "#!{real}\n"
        "import collections, sys\n"
        "counter = {counter!r}\n"
        "calls = int(open(counter).read()) if __import__('os').path.exists(counter) else 0\n"
        "code = sys.argv[2]\n"
        "if 'version_info[0]' in code:\n"
        "    open(counter, 'w').write(str(calls + 1))\n"
        "    print('3 13 6 final 3.13.6' if calls == 0 else '3 12 11 final 3.12.11')\n"
        "elif 'sys.executable' in code:\n"
        "    print({shim!r})\n"
        "else:\n"
        "    raise SystemExit('unexpected question: ' + code)\n".format(
            real=sys.executable, counter=str(counter), shim=str(shim)
        ),
        encoding="utf-8",
    )
    shim.chmod(0o755)

    result = _run_isolated(tmp_path, fake_bin)

    assert result.returncode != 0, result.stdout
    assert counter.read_text() == "2", "the bound path was not probed again"
    assert "3.12.11" in result.stderr, result.stderr
    assert_nothing_installed(tmp_path)


def test_python_override_not_on_path_errors(tmp_path):
    fake_bin = _fake_bin(tmp_path, sys.executable)

    result = _run_isolated(tmp_path, fake_bin, ["--python", "no-such-python"])

    assert result.returncode != 0
    assert "no-such-python" in result.stderr
    assert_nothing_installed(tmp_path)

    # A path that is not an executable file (a typo, a directory) gets that
    # said outright rather than "could not read its version (got '')".
    for bad in (str(tmp_path / "nonexistent" / "python3"), str(tmp_path)):
        result = _run_isolated(tmp_path, fake_bin, ["--python", bad])
        assert result.returncode != 0
        assert "not an executable file" in result.stderr, result.stderr
        assert bad in result.stderr
        assert_nothing_installed(tmp_path)


@pytest.mark.parametrize(
    "answer, reason",
    [
        ("", "reported no sys.executable"),
        (None, "reported no sys.executable"),
        ("python3", "relative sys.executable"),
        ("./python3", "relative sys.executable"),
        ("/tmp/a\tb/python3", "control character"),
        ("banner\n/usr/bin/python3", "more than one line"),
        ("/usr/bin/python3\n\n", "more than one line"),
    ],
    ids=["empty", "None", "bare-name", "dot-relative", "tab", "banner", "trailing-blank-lines"],
)
def test_install_refuses_unusable_sys_executable_answers(answer, reason, tmp_path):
    # The version passes; the interpreter's idea of where it lives does
    # not. Nothing relative, empty, multi-line or unprintable is bound --
    # the entry point runs one absolute path or nothing.
    fake_bin = _fake_bin(tmp_path)
    _fake_python3_reporting(fake_bin, PYTHON_FLOOR, executable=answer)

    result = _run_isolated(tmp_path, fake_bin)

    assert result.returncode != 0, result.stdout
    assert reason in result.stderr, result.stderr
    assert "python3:" in result.stderr
    assert_nothing_installed(tmp_path)


def test_install_refuses_a_checkout_path_with_a_control_character(tmp_path):
    # The launcher path is written into the entry point's header and
    # diagnostics one per line; a control character in it would corrupt
    # both. Shares the guard the interpreter path gets.
    checkout = tmp_path / "a\tb"
    checkout.mkdir()
    shutil.copy(INSTALL_SH, checkout / "install.sh")
    shutil.copy(LAUNCHER, checkout / "jimemo")
    (checkout / "skill").mkdir()
    shutil.copy(REPO_ROOT / "skill" / "SKILL.md", checkout / "skill" / "SKILL.md")

    result = run_install([], tmp_path, script=checkout / "install.sh")

    assert result.returncode != 0, result.stdout
    assert "control character" in result.stderr
    assert_nothing_installed(tmp_path)


# --- the entry point's own failure modes -------------------------------------


def test_entry_point_reports_a_removed_interpreter_in_one_line(tmp_path):
    venv_python = make_venv(tmp_path / "venv")
    run_install(["--python", str(venv_python)], tmp_path)
    shutil.rmtree(tmp_path / "venv")

    result = run_entry_point(tmp_path, ["--version"])

    assert result.returncode == 1, result
    assert result.stdout == ""
    lines = [line for line in result.stderr.splitlines() if line.strip()]
    assert len(lines) == 1, result.stderr
    assert "install.sh" in lines[0]
    assert str(venv_python) in lines[0]
    assert "Traceback" not in result.stderr


def test_entry_point_reports_a_downgraded_interpreter_in_one_line(tmp_path):
    # The bound path still exists but now runs something below the floor
    # (a re-pointed install, a rebased venv). The launcher's own floor
    # check catches it; still one line, still no traceback, still exit 1,
    # and because the wrapper exported JIMEMO_ENTRY_POINT the line names
    # the entry point and says to re-run install.sh instead of giving the
    # PATH advice, which would be wrong here.
    venv_python = make_venv(tmp_path / "venv")
    run_install(["--python", str(venv_python)], tmp_path)
    replace_venv_python(venv_python, faking_shim((3, 12, 11)))

    result = run_entry_point(tmp_path, ["doctor"])

    assert result.returncode == 1, result
    assert result.stdout == ""
    lines = [line for line in result.stderr.splitlines() if line.strip()]
    assert len(lines) == 1, result.stderr
    assert "3.12.11" in lines[0]
    assert FLOOR_TEXT in lines[0]
    assert "install.sh" in lines[0], lines[0]
    assert str(cli_target(tmp_path)) in lines[0], lines[0]
    assert "PATH" not in lines[0], lines[0]
    assert "Traceback" not in result.stderr


def test_entry_point_reports_a_removed_checkout_in_one_line(tmp_path):
    checkout = tmp_path / "clone"
    checkout.mkdir()
    shutil.copy(INSTALL_SH, checkout / "install.sh")
    shutil.copy(LAUNCHER, checkout / "jimemo")
    (checkout / "skill").mkdir()
    shutil.copy(REPO_ROOT / "skill" / "SKILL.md", checkout / "skill" / "SKILL.md")
    (checkout / "src").symlink_to(REPO_ROOT / "src")
    result = run_install([], tmp_path, script=checkout / "install.sh")
    assert result.returncode == 0, result.stderr
    shutil.rmtree(checkout)

    result = run_entry_point(tmp_path, ["--version"])

    assert result.returncode == 1, result
    lines = [line for line in result.stderr.splitlines() if line.strip()]
    assert len(lines) == 1, result.stderr
    assert "install.sh" in lines[0]
    assert str(checkout / "jimemo") in lines[0]


ODD_NAME = "sp ace'q\"d$ollar`tick\\bs"  # space ' " $ ` \


def test_entry_point_survives_the_admitted_character_set_in_both_paths(tmp_path):
    # Both stored paths carry every character the installer admits, and
    # both round-trip through the sh single-quoting AND the comment header.
    venv_python = make_venv(tmp_path / ODD_NAME / "venv")
    checkout = tmp_path / ("clone " + ODD_NAME)
    checkout.mkdir()
    shutil.copy(INSTALL_SH, checkout / "install.sh")
    shutil.copy(LAUNCHER, checkout / "jimemo")
    (checkout / "skill").mkdir()
    shutil.copy(REPO_ROOT / "skill" / "SKILL.md", checkout / "skill" / "SKILL.md")
    (checkout / "src").symlink_to(REPO_ROOT / "src")

    result = run_install(
        ["--python", str(venv_python)], tmp_path, script=checkout / "install.sh"
    )
    assert result.returncode == 0, result.stderr

    target = cli_target(tmp_path)
    python, launcher = read_entry_point_header(target)
    assert python == str(venv_python)
    assert launcher == str(checkout / "jimemo")
    text = target.read_text(encoding="utf-8")
    for name, value in (("JIMEMO_PYTHON", python), ("JIMEMO_LAUNCHER", launcher)):
        m = re.search(rf"^{name}='(.*)'$", text, re.M)
        assert m, text
        assert m.group(1).replace("'\\''", "'") == value
    assert subprocess.run(["/bin/sh", "-n", str(target)]).returncode == 0

    run = run_entry_point(tmp_path, ["--version"], env=bare_env(tmp_path))
    assert run.returncode == 0, run.stderr
    assert jimemo.__version__ in run.stdout

    # The diagnostic prints the path verbatim -- backslash and backtick
    # included -- which `echo` could not promise.
    shutil.rmtree(tmp_path / ODD_NAME)
    run = run_entry_point(tmp_path, ["--version"])
    assert run.returncode == 1
    lines = [line for line in run.stderr.splitlines() if line.strip()]
    assert len(lines) == 1, run.stderr
    assert str(venv_python) in lines[0], lines[0]

    # And uninstall recognises its own work under those paths.
    result = run_install(["--uninstall"], tmp_path, script=checkout / "install.sh")
    assert result.returncode == 0, result.stderr
    assert not target.exists()


# --- the floor, at the installer boundary (jimemo#gaga) ----------------------


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
    # with a python3 that EXISTS and is too old (jimemo#gaga), and no
    # versioned candidate to fall through to.
    #
    # The REAL run (no --dry-run) is the one that proves anything: with
    # --dry-run, install.sh creates nothing regardless, so "nothing was
    # installed" would also hold if the floor check ran AFTER the install
    # actions. Both are parametrised so the dry-run path is covered too.
    fake_bin = _fake_bin(tmp_path, executable)
    args = ["--dry-run"] if dry_run else []

    result = _run_isolated(tmp_path, fake_bin, args)

    assert result.returncode != 0, result.stdout
    assert FLOOR_TEXT in result.stderr, result.stderr
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
    fake_bin = _fake_bin(tmp_path)
    _fake_python3_reporting(fake_bin, version)

    result = _run_isolated(tmp_path, fake_bin)

    dotted = ".".join(str(part) for part in version)
    assert result.returncode != 0, result.stdout
    assert dotted in result.stderr, result.stderr
    assert FLOOR_TEXT in result.stderr, result.stderr
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
        # A plausible line followed by BLANK lines: command substitution
        # strips trailing newlines, so without the sentinel this reads as
        # one clean line.
        "3 13 6 final 3.13.6\n\n",
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
        "trailing-blank-line",
        "hex-micro",
    ],
)
def test_install_refuses_a_python3_whose_version_it_cannot_read(stdout, tmp_path):
    # A floor check must fail CLOSED. If python3 exits 0 but prints
    # something unexpected, the version components are non-numeric; every
    # `[ … -lt … ]` then fails with status 2, the whole `if` evaluates
    # false (set -e does not apply inside an if condition), and without an
    # explicit guard install.sh would proceed to install (jimemo#gaga).
    fake_bin = _fake_bin(tmp_path)
    shim = fake_bin / "python3"
    shim.write_text(
        '#!/bin/sh\ncat <<"EOF"\n' + stdout + "EOF\nexit 0\n", encoding="utf-8"
    )
    shim.chmod(0o755)

    result = _run_isolated(tmp_path, fake_bin)

    assert result.returncode != 0, result.stdout
    # Either the generic parse refusal or the multiline-specific one. Both
    # name the floor, and both must install nothing.
    assert (
        "could not read python3's version" in result.stderr
        or "printed more than one line" in result.stderr
    ), result.stderr
    assert FLOOR_TEXT in result.stderr
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
    fake_bin = _fake_bin(tmp_path)
    _fake_python3_reporting(fake_bin, version, releaselevel=level, serial=serial)

    result = _run_isolated(tmp_path, fake_bin)

    assert result.returncode != 0, result.stdout
    assert "pre-release" in result.stderr, result.stderr
    assert level in result.stderr, result.stderr
    assert_nothing_installed(tmp_path)


def test_install_reports_a_python3_that_exits_non_zero(tmp_path):
    # Under `set -e` a failing command substitution used to abort the
    # script with no output at all -- fail-closed, but the user got
    # nothing to act on (jimemo#gaga).
    fake_bin = _fake_bin(tmp_path)
    shim = fake_bin / "python3"
    shim.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    shim.chmod(0o755)

    result = _run_isolated(tmp_path, fake_bin)

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
    # (The shim reports the real interpreter as its sys.executable, so that
    # is what gets bound -- the boundary under test is the version check.)
    fake_bin = _fake_bin(tmp_path)
    _fake_python3_reporting(fake_bin, version)

    result = _run_isolated(tmp_path, fake_bin)

    assert result.returncode == 0, result.stderr
    assert "requires Python" not in result.stderr, result.stderr
    assert_entry_point(tmp_path)


@pytest.mark.skipif(
    not SUB_FLOOR_PYTHONS,
    reason="this machine has no Python below the floor to uninstall with",
)
@pytest.mark.parametrize(
    "version, executable", SUB_FLOOR_PYTHONS[:1], ids=lambda value: str(value)
)
def test_uninstall_works_below_the_floor(version, executable, tmp_path):
    # Raising the floor must not take away the way out: a machine whose
    # python3 is too old to INSTALL must still be able to remove what a
    # previous install left behind, or the user is stuck with a dangling
    # command and no supported way to clear it (jimemo#gaga). Uninstall
    # touches no python3 at all -- it reads the entry point's marker with
    # the shell.
    fake_bin = _fake_bin(tmp_path, executable)

    # Plant exactly what a previous install would have left: the old
    # symlink form for the CLI here (the new form is covered by
    # test_uninstall_removes_exactly_what_it_wrote), skill symlinks.
    planted = []
    for parts in ALL_TARGETS:
        target = tmp_path.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = LAUNCHER if parts[0] == ".local" else REPO_ROOT / "skill"
        target.symlink_to(source)
        planted.append(target)
    # ... plus a file install.sh does not own, which must survive.
    bystander = tmp_path / ".claude" / "skills" / "somebody-elses"
    bystander.write_text("keep me", encoding="utf-8")

    result = _run_isolated(tmp_path, fake_bin, ["--uninstall"])

    assert result.returncode == 0, result.stderr
    for target in planted:
        assert not target.is_symlink(), target
    assert bystander.read_text(encoding="utf-8") == "keep me"


def test_uninstall_of_a_wrapper_works_with_no_python3_at_all(tmp_path):
    # Same rule, harsher case, new form: install with a real interpreter,
    # then uninstall on a PATH with no python3 anywhere.
    run_install([], tmp_path)
    assert_entry_point(tmp_path)
    fake_bin = _fake_bin(tmp_path)

    result = _run_isolated(tmp_path, fake_bin, ["--uninstall"])

    assert result.returncode == 0, result.stderr
    assert not cli_target(tmp_path).exists()


def test_uninstall_works_with_no_python3_at_all(tmp_path):
    fake_bin = _fake_bin(tmp_path)
    cli = cli_target(tmp_path)
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.symlink_to(LAUNCHER)

    result = _run_isolated(tmp_path, fake_bin, ["--uninstall"])

    assert result.returncode == 0, result.stderr
    assert not cli.is_symlink()


@pytest.mark.skipif(sys.platform == "win32", reason="bash script, POSIX only")
def test_install_sh_is_executable():
    mode = INSTALL_SH.stat().st_mode
    assert mode & 0o111, "install.sh should be executable"
