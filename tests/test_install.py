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


@pytest.mark.skipif(sys.platform == "win32", reason="bash script, POSIX only")
def test_install_sh_is_executable():
    mode = INSTALL_SH.stat().st_mode
    assert mode & 0o111, "install.sh should be executable"
