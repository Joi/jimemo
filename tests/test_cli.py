import collections
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import jimemo
from jimemo import PYTHON_FLOOR
from jimemo import _parser_floor as cli_parser_floor
from jimemo import cli
from jimemo.cli import main

REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = REPO_ROOT / "jimemo"

# The autouse `hermetic_entry_point` fixture the doctor tests below lean on
# lives in conftest.py: it has to cover every in-process main(["doctor"])
# call, and test_suggest.py makes two of them.


def _write_entry_point(path, python, launcher):
    """A wrapper with install.sh's header (its first five lines are the
    contract test_install.py checks the installer against)."""
    path.write_text(
        "#!/bin/sh\n"
        "# jimemo entry point, written by install.sh. Re-run install.sh to change it.\n"
        "# jimemo-entry-point: 1\n"
        f"# python: {python}\n"
        f"# launcher: {launcher}\n"
        f"JIMEMO_PYTHON='{python}'\n"
        f"JIMEMO_LAUNCHER='{launcher}'\n"
        'JIMEMO_ENTRY_POINT="$0"\n'
        "export JIMEMO_ENTRY_POINT\n"
        'exec "$JIMEMO_PYTHON" "$JIMEMO_LAUNCHER" "$@"\n'
    )
    path.chmod(0o755)
    return path


def _fake_python_reporting(path, version, releaselevel="final", serial=0):
    """An executable at `path` whose sys.version_info is `version`, whatever
    the real interpreter is -- the same shape as test_install.py's
    _fake_python3_reporting; it only needs to answer `-c`."""
    path.write_text(
        f"#!{sys.executable}\n"
        "import collections, sys\n"
        "version_info = collections.namedtuple(\n"
        "    'version_info', 'major minor micro releaselevel serial'\n"
        ")\n"
        f"sys.version_info = version_info({version[0]}, {version[1]}, "
        f"{version[2]}, {releaselevel!r}, {serial})\n"
        "args = sys.argv[1:]\n"
        "if args and args[0] == '-c':\n"
        "    exec(compile(args[1], '<faked>', 'exec'), {'__name__': '__main__'})\n"
        "else:\n"
        "    raise SystemExit('faked python only answers -c')\n"
    )
    path.chmod(0o755)
    return str(path)


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert jimemo.__version__ in capsys.readouterr().out


def test_doctor_on_clean_repo(capsys):
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "python" in out.lower()
    assert "vendor" in out.lower()
    assert "ok   vendored imports (jinja2, markdown, yaml, tomli)" in out
    assert "ok   charts vendored (chart.js 4.5.1)" in out


def _faked_version_info(major, minor, micro):
    # sys.version_info is a structseq; a namedtuple with the same fields
    # compares against a tuple the same way and answers .major/.minor/
    # .micro, which is all cmd_doctor reads.
    version_info = collections.namedtuple(
        "version_info", "major minor micro releaselevel serial"
    )
    return version_info(major, minor, micro, "final", 0)


@pytest.mark.parametrize(
    "version",
    [(3, 9, 6), (3, 12, 11), (3, 13, 0), (3, 13, 3), (3, 13, 5)],
    ids=lambda value: ".".join(str(part) for part in value),
)
def test_doctor_fails_below_the_python_floor(version, capsys, monkeypatch):
    # 3.13.5 is the case a major/minor comparison would call ok: its
    # html.parser still splits attributes where a browser does not
    # (jimemo#gaga). 3.13.0/3.13.3 also still decode semicolonless
    # attribute references and drop unclosed <style> text.
    monkeypatch.setattr(cli.sys, "version_info", _faked_version_info(*version))
    monkeypatch.setattr(
        cli_parser_floor.sys, "version_info", _faked_version_info(*version)
    )
    assert main(["doctor"]) != 0
    out = capsys.readouterr().out
    running = ".".join(str(part) for part in version)
    floor = ".".join(str(part) for part in PYTHON_FLOOR)
    assert f"FAIL python {running} is not supported" in out, out
    assert floor in out.splitlines()[0], out
    # The interpreter line is still the FIRST thing doctor prints.
    assert out.splitlines()[0].startswith("FAIL python "), out


@pytest.mark.parametrize(
    "version, level, serial, shown",
    [
        ((3, 14, 0), "beta", 1, "3.14.0b1"),
        ((3, 15, 0), "alpha", 2, "3.15.0a2"),
        (PYTHON_FLOOR, "candidate", 1, ".".join(map(str, PYTHON_FLOOR)) + "rc1"),
    ],
    ids=["3.14.0b1", "3.15.0a2", "floor-rc1"],
)
def test_doctor_fails_on_a_prerelease_even_above_the_floor(
    version, level, serial, shown, capsys, monkeypatch
):
    # Measured on real CPython 3.14.0b1: above the floor by number, yet its
    # html.parser fails every check jimemo depends on. doctor must not
    # report ok on an interpreter jimemo.lint will refuse to load on.
    faked = collections.namedtuple(
        "version_info", "major minor micro releaselevel serial"
    )(version[0], version[1], version[2], level, serial)
    monkeypatch.setattr(cli.sys, "version_info", faked)
    monkeypatch.setattr(cli_parser_floor.sys, "version_info", faked)
    assert main(["doctor"]) != 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith(f"FAIL python {shown} is not supported"), first
    assert "pre-release" in first, first


def test_doctor_passes_at_the_exact_floor(capsys, monkeypatch):
    monkeypatch.setattr(cli.sys, "version_info", _faked_version_info(*PYTHON_FLOOR))
    monkeypatch.setattr(
        cli_parser_floor.sys, "version_info", _faked_version_info(*PYTHON_FLOOR)
    )
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    floor = ".".join(str(part) for part in PYTHON_FLOOR)
    assert out.splitlines()[0] == f"ok   python {floor}", out


def test_doctor_reports_the_running_interpreter_version(capsys):
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    v = sys.version_info
    assert out.splitlines()[0] == f"ok   python {v.major}.{v.minor}.{v.micro}", out


# --- the entry point section (jimemo#p0nk) --------------------------------


def _entry_point_lines(out):
    return [line for line in out.splitlines() if "entry point" in line]


def test_doctor_skips_when_no_entry_point_is_installed(capsys, hermetic_entry_point):
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    lines = _entry_point_lines(out)
    assert lines == [
        f"skip entry point (none at {hermetic_entry_point}; ./install.sh writes one)"
    ], out
    # The interpreter line stays FIRST; the entry point comes right after.
    assert out.splitlines()[0].startswith("ok   python "), out
    assert out.splitlines()[1].startswith("skip entry point"), out


def test_doctor_warns_on_an_old_symlink_install(capsys, hermetic_entry_point):
    hermetic_entry_point.symlink_to(LAUNCHER)
    assert main(["doctor"]) == 0
    lines = _entry_point_lines(capsys.readouterr().out)
    assert len(lines) == 1, lines
    assert lines[0].startswith(
        f"WARNING entry point {hermetic_entry_point} is a symlink to {LAUNCHER}: "
    ), lines
    assert "whatever python3 the calling shell resolves" in lines[0]
    assert "re-run install.sh" in lines[0]


def test_doctor_reports_the_bound_interpreter_and_its_version(
    capsys, hermetic_entry_point
):
    _write_entry_point(hermetic_entry_point, sys.executable, str(LAUNCHER))
    assert main(["doctor"]) == 0
    lines = _entry_point_lines(capsys.readouterr().out)
    running = cli_parser_floor.running_version()
    assert lines == [
        f"ok   entry point {hermetic_entry_point} -> {sys.executable} ({running})"
    ], lines


def test_doctor_fails_when_the_bound_interpreter_is_missing(
    capsys, hermetic_entry_point, tmp_path
):
    gone = tmp_path / "venv" / "bin" / "python"
    _write_entry_point(hermetic_entry_point, str(gone), str(LAUNCHER))
    assert main(["doctor"]) == 1
    lines = _entry_point_lines(capsys.readouterr().out)
    assert lines == [
        f"FAIL entry point {hermetic_entry_point}: bound interpreter {gone} "
        "is missing -- re-run install.sh"
    ], lines


def test_doctor_fails_when_the_bound_interpreter_is_below_the_floor(
    capsys, hermetic_entry_point, tmp_path
):
    # The running interpreter is fine; the BOUND one is 3.12.11. Doctor's
    # first line is still ok, the entry point line is the FAIL.
    shim = _fake_python_reporting(tmp_path / "python3", (3, 12, 11))
    _write_entry_point(hermetic_entry_point, shim, str(LAUNCHER))
    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("ok   python "), out
    lines = _entry_point_lines(out)
    assert len(lines) == 1, lines
    floor = ".".join(str(part) for part in PYTHON_FLOOR)
    assert lines[0].startswith(
        f"FAIL entry point {hermetic_entry_point}: bound interpreter {shim} "
        "is 3.12.11, "
    ), lines
    assert "below" in lines[0] and floor in lines[0], lines
    assert lines[0].endswith(" -- re-run install.sh"), lines


def test_doctor_fails_when_the_bound_interpreter_is_a_prerelease(
    capsys, hermetic_entry_point, tmp_path
):
    # Above the floor by number. The releaselevel must come from the BOUND
    # interpreter's tuple, not sys.version_info[3] of the one running doctor.
    shim = _fake_python_reporting(tmp_path / "python3", (3, 14, 0), "beta", 1)
    _write_entry_point(hermetic_entry_point, shim, str(LAUNCHER))
    assert main(["doctor"]) == 1
    lines = _entry_point_lines(capsys.readouterr().out)
    assert len(lines) == 1, lines
    assert lines[0].startswith(
        f"FAIL entry point {hermetic_entry_point}: bound interpreter {shim} "
        "is 3.14.0b1, "
    ), lines
    assert "pre-release" in lines[0], lines
    assert lines[0].endswith(" -- re-run install.sh"), lines


@pytest.mark.parametrize(
    "body, reason",
    [
        ("exit 3\n", "exited 3"),
        (
            "echo banner\necho 3 13 6 final 0\n",
            "printed something other than a version",
        ),
        ("echo 3 13 6 final garbage\n", "printed something other than a version"),
    ],
    ids=["exit-3", "banner", "sixth-field"],
)
def test_doctor_fails_when_the_bound_interpreter_cannot_be_read(
    capsys, hermetic_entry_point, tmp_path, body, reason
):
    shim = tmp_path / "python3"
    shim.write_text("#!/bin/sh\n" + body)
    shim.chmod(0o755)
    _write_entry_point(hermetic_entry_point, str(shim), str(LAUNCHER))
    assert main(["doctor"]) == 1
    lines = _entry_point_lines(capsys.readouterr().out)
    assert lines == [
        f"FAIL entry point {hermetic_entry_point}: bound interpreter {shim} "
        f"could not be read: {reason} -- re-run install.sh"
    ], lines


def test_doctor_fails_when_the_bound_interpreter_is_not_executable(
    capsys, hermetic_entry_point, tmp_path
):
    shim = tmp_path / "python3"
    shim.write_text("#!/bin/sh\necho 3 13 6 final 0\n")
    shim.chmod(0o644)
    if os.access(str(shim), os.X_OK):
        pytest.skip("root can execute anything")
    _write_entry_point(hermetic_entry_point, str(shim), str(LAUNCHER))
    assert main(["doctor"]) == 1
    lines = _entry_point_lines(capsys.readouterr().out)
    assert lines == [
        f"FAIL entry point {hermetic_entry_point}: bound interpreter {shim} "
        "is not executable -- re-run install.sh"
    ], lines


# ---- S1 / S3: the launcher side of the header ----------------------------


def test_doctor_fails_when_the_launcher_is_gone(capsys, hermetic_entry_point, tmp_path):
    # The wrapper's own `[ -f "$JIMEMO_LAUNCHER" ]` check exits 1 at runtime
    # for this case; doctor must not say ok about it.
    # Exactly ONE status line, and it is the FAIL: an entry point that
    # cannot run never gets an ok line first.
    gone = tmp_path / "old-checkout" / "jimemo"
    _write_entry_point(hermetic_entry_point, sys.executable, str(gone))
    assert main(["doctor"]) == 1
    lines = _entry_point_lines(capsys.readouterr().out)
    assert lines == [
        f"FAIL entry point {hermetic_entry_point}: launcher {gone} is gone "
        f"(bound interpreter {sys.executable}) -- re-run install.sh from a "
        "jimemo checkout"
    ], lines


def test_doctor_warns_when_the_entry_point_is_a_fifo(capsys, hermetic_entry_point):
    # A FIFO with no writer would block open() forever; the reader
    # classifies it from lstat and never opens it.
    os.mkfifo(hermetic_entry_point)
    assert main(["doctor"]) == 0
    lines = _entry_point_lines(capsys.readouterr().out)
    assert lines == [
        f"WARNING entry point {hermetic_entry_point} is not a regular file, "
        "not an entry point"
    ], lines


def test_doctor_warns_on_a_header_value_with_a_control_character(
    capsys, hermetic_entry_point
):
    # A NUL survives errors="replace" and would make Path.resolve() raise;
    # read_entry_point refuses it first, and doctor reports, not tracebacks.
    hermetic_entry_point.write_bytes(
        b"#!/bin/sh\n# jimemo-entry-point: 1\n# python: /usr/bin/py\x00thon3\n"
        b"# launcher: /repo/jimemo\n"
    )
    assert main(["doctor"]) == 0
    lines = _entry_point_lines(capsys.readouterr().out)
    assert lines == [
        f"WARNING entry point {hermetic_entry_point} has a header value with a "
        "control character -- re-run install.sh"
    ], lines


def test_doctor_warns_on_a_relative_python_path(capsys, hermetic_entry_point):
    _write_entry_point(hermetic_entry_point, "python3", str(LAUNCHER))
    assert main(["doctor"]) == 0
    lines = _entry_point_lines(capsys.readouterr().out)
    assert lines == [
        f"WARNING entry point {hermetic_entry_point} names a relative python "
        "path (python3) -- re-run install.sh"
    ], lines


def test_doctor_survives_a_launcher_path_resolve_cannot_handle(
    capsys, hermetic_entry_point, monkeypatch
):
    # Belt and braces for the resolve()/is_file() calls: even if a value
    # gets past read_entry_point, an OSError/ValueError there is "gone", not
    # a traceback.
    _write_entry_point(hermetic_entry_point, sys.executable, str(LAUNCHER))
    real_is_file = Path.is_file

    def boom(self):
        if self.name == "jimemo" and self != hermetic_entry_point:
            raise OSError(5, "Input/output error")
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", boom)
    assert main(["doctor"]) == 1
    lines = _entry_point_lines(capsys.readouterr().out)
    assert any(
        line.startswith(f"FAIL entry point {hermetic_entry_point}: launcher ")
        and "is gone" in line
        for line in lines
    ), lines


def test_doctor_reports_an_unreadable_symlink_target(
    capsys, hermetic_entry_point, monkeypatch
):
    hermetic_entry_point.symlink_to(LAUNCHER)
    def unreadable_link(path, *args, **kwargs):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(os, "readlink", unreadable_link)
    assert main(["doctor"]) == 0
    lines = _entry_point_lines(capsys.readouterr().out)
    assert len(lines) == 1, lines
    assert lines[0].startswith(
        f"WARNING entry point {hermetic_entry_point} is a symlink to ?: "
    ), lines


def test_doctor_warns_when_the_entry_point_is_a_directory(capsys, hermetic_entry_point):
    hermetic_entry_point.mkdir()
    assert main(["doctor"]) == 0
    lines = _entry_point_lines(capsys.readouterr().out)
    assert lines == [
        f"WARNING entry point {hermetic_entry_point} is a directory, not an entry point"
    ], lines


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads anything")
def test_doctor_warns_when_the_entry_point_is_unreadable(capsys, hermetic_entry_point):
    _write_entry_point(hermetic_entry_point, sys.executable, str(LAUNCHER))
    hermetic_entry_point.chmod(0)
    try:
        rc = main(["doctor"])
    finally:
        hermetic_entry_point.chmod(0o755)
    assert rc == 0
    lines = _entry_point_lines(capsys.readouterr().out)
    assert len(lines) == 1, lines
    assert lines[0].startswith(
        f"WARNING entry point {hermetic_entry_point} could not be read: "
    ), lines


def test_doctor_warns_on_a_file_it_did_not_write(capsys, hermetic_entry_point):
    hermetic_entry_point.write_text("#!/bin/sh\nexec python3 /x/jimemo \"$@\"\n")
    assert main(["doctor"]) == 0
    lines = _entry_point_lines(capsys.readouterr().out)
    assert lines == [
        f"WARNING entry point {hermetic_entry_point} was not written by install.sh "
        "(no marker)"
    ], lines


def test_doctor_warns_on_a_truncated_header(capsys, hermetic_entry_point):
    hermetic_entry_point.write_text("#!/bin/sh\n# jimemo-entry-point: 1\n")
    assert main(["doctor"]) == 0
    lines = _entry_point_lines(capsys.readouterr().out)
    assert lines == [
        f"WARNING entry point {hermetic_entry_point} has a jimemo marker but no "
        "python/launcher lines -- re-run install.sh"
    ], lines


def test_doctor_warns_when_the_entry_point_runs_a_different_checkout(
    capsys, hermetic_entry_point, tmp_path
):
    # Another checkout that EXISTS (a missing one is the FAIL case above).
    elsewhere = tmp_path / "elsewhere" / "jimemo"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(LAUNCHER.read_bytes())
    _write_entry_point(hermetic_entry_point, sys.executable, str(elsewhere))
    assert main(["doctor"]) == 0
    lines = _entry_point_lines(capsys.readouterr().out)
    assert len(lines) == 2, lines
    assert lines[0].startswith(f"ok   entry point {hermetic_entry_point} -> "), lines
    assert lines[1] == (
        f"WARNING entry point {hermetic_entry_point} runs a different checkout: "
        f"{elsewhere}"
    ), lines


def test_doctor_notes_a_run_through_the_entry_point(
    capsys, hermetic_entry_point, monkeypatch
):
    monkeypatch.setenv("JIMEMO_ENTRY_POINT", "/x/bin/jimemo")
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "ok   this run came through the entry point /x/bin/jimemo" in out, out


def test_no_args_shows_help(capsys):
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_list_runs(capsys):
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert ("no templates installed yet" in out) or ("\t" in out)


def test_doctor_skips_vendored_imports_on_tampered_checksums(capsys, monkeypatch, tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    f = vendor / "pkg" / "mod.py"
    f.parent.mkdir()
    f.write_text("x = 1\n")
    digest = hashlib.sha256(f.read_bytes()).hexdigest()
    (vendor / "SHA256SUMS").write_text(f"{digest}  ./pkg/mod.py\n")
    f.write_text("x = 2\n")  # tamper after recording the checksum

    monkeypatch.setattr("jimemo.cli.VENDOR_DIR", vendor)

    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "checksum mismatch" in out
    assert "skip vendored imports" in out
    assert "ok   vendored imports" not in out


def test_doctor_reports_tampered_charts_checksums(capsys, monkeypatch, tmp_path):
    charts_vendor = tmp_path / "charts_vendor"
    f = charts_vendor / "chartjs" / "chart.umd.min.js"
    f.parent.mkdir(parents=True)
    f.write_text("/*! Chart.js v4.5.1 */\nvar x = 1;\n")
    digest = hashlib.sha256(f.read_bytes()).hexdigest()
    (charts_vendor / "SHA256SUMS").write_text(f"{digest}  ./chartjs/chart.umd.min.js\n")
    f.write_text("/*! Chart.js v4.5.1 */\nvar x = 2;\n")  # tamper after recording

    monkeypatch.setattr("jimemo.cli.CHARTS_VENDOR_DIR", charts_vendor)

    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "FAIL charts: checksum mismatch" in out
    assert "chartjs/chart.umd.min.js" in out
    # the real (non-tampered) vendor/ should still verify clean independently
    assert "ok   vendor checksums" in out


# The next two checks need a *clean* sys.modules: every other test module in
# this suite imports jimemo.content/render/suggest at its own top (to test
# them directly), and pytest collection imports every test module before any
# test body runs -- so by the time any in-process test executes, jinja2/
# yaml/markdown are already in sys.modules regardless of run order, for
# reasons that have nothing to do with cli.py. Run each check in a fresh
# subprocess instead, where sys.modules starts empty.

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")


def test_importing_cli_does_not_import_vendored_libs():
    # cli.py's own top-level imports must stay vendor-free (see cli.py's
    # module docstring comment): doctor, --version, and list all need to
    # run before the checksum gate can matter, which is moot if merely
    # `import jimemo.cli` already pulled in jinja2/yaml/markdown.
    script = (
        "import sys\n"
        f"sys.path.insert(0, {SRC_DIR!r})\n"
        "import jimemo.cli\n"
        "assert 'jinja2' not in sys.modules, sorted(sys.modules)\n"
        "assert 'yaml' not in sys.modules, sorted(sys.modules)\n"
        "assert 'markdown' not in sys.modules, sorted(sys.modules)\n"
        "assert 'tomli' not in sys.modules, sorted(sys.modules)\n"
        "print('OK')\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_doctor_tampered_checksums_never_imports_vendored_libs(tmp_path):
    # Extends test_doctor_skips_vendored_imports_on_tampered_checksums:
    # on a tampered vendor/, doctor must not just print "skip vendored
    # imports" -- no vendored module may actually land in sys.modules,
    # including via the unconditional stale-suitability-label scan, which
    # pulls in suggest.py (is_stale_labels).
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    f = vendor / "pkg" / "mod.py"
    f.parent.mkdir()
    f.write_text("x = 1\n")
    digest = hashlib.sha256(f.read_bytes()).hexdigest()
    (vendor / "SHA256SUMS").write_text(f"{digest}  ./pkg/mod.py\n")
    f.write_text("x = 2\n")  # tamper after recording the checksum

    script = (
        "import sys\n"
        f"sys.path.insert(0, {SRC_DIR!r})\n"
        "from pathlib import Path\n"
        "import jimemo.cli as cli\n"
        f"cli.VENDOR_DIR = Path({str(vendor)!r})\n"
        "rc = cli.main(['doctor'])\n"
        "assert rc == 1, rc\n"
        "assert 'jinja2' not in sys.modules, sorted(sys.modules)\n"
        "assert 'yaml' not in sys.modules, sorted(sys.modules)\n"
        "assert 'markdown' not in sys.modules, sorted(sys.modules)\n"
        "assert 'tomli' not in sys.modules, sorted(sys.modules)\n"
        "print('OK')\n"
    )
    # HOME under tmp_path: this doctor runs in a fresh process, where the
    # autouse fixture cannot reach, and must not read the developer's real
    # ~/.local/bin/jimemo (jimemo#p0nk).
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "checksum mismatch" in result.stdout
    assert "skip vendored imports" in result.stdout
    assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# publish dispatch (Phase 5 Task 2): a fake command config + a monkeypatched
# subprocess.run stand in for a real notes-publish invocation. Patching
# subprocess.run (rather than CommandPublisher's injectable `runner`) mirrors
# what actually happens end to end -- the CLI's own construction of
# CommandPublisher via get_publisher() never sees a test-injected runner.
# ---------------------------------------------------------------------------

def _write_command_config(tmp_path, command="fake-publish-cli"):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'[publish]\nbackend = "command"\ncommand = "{command}"\n')
    return cfg_file


def test_publish_file_prints_url(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(_write_command_config(tmp_path)))
    html = tmp_path / "page.html"
    html.write_text("<html></html>")

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return CompletedProcess(argv, 0, stdout="https://notes.ito.com/abc123/\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["publish", str(html)]) == 0
    assert capsys.readouterr().out.strip() == "https://notes.ito.com/abc123/"
    assert calls == [["fake-publish-cli", str(html)]]


def test_publish_file_with_title(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(_write_command_config(tmp_path)))
    html = tmp_path / "page.html"
    html.write_text("<html></html>")

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return CompletedProcess(argv, 0, stdout="https://notes.ito.com/abc123/\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["publish", str(html), "--title", "Q3 Briefing"]) == 0
    assert calls == [["fake-publish-cli", str(html), "--title", "Q3 Briefing"]]


def test_publish_missing_file_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(_write_command_config(tmp_path)))
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: (_ for _ in ()).throw(
        AssertionError("subprocess.run should not be called for a missing file")
    ))

    missing = tmp_path / "nope.html"
    assert main(["publish", str(missing)]) == 1
    assert "file not found" in capsys.readouterr().err


def test_publish_purge_dispatches(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(_write_command_config(tmp_path)))

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return CompletedProcess(argv, 0, stdout="purged: abc123\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["publish", "purge", "abc123"]) == 0
    assert calls == [["fake-publish-cli", "purge", "abc123"]]
    assert "abc123" in capsys.readouterr().out


def test_publish_purge_missing_arg_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(_write_command_config(tmp_path)))
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: (_ for _ in ()).throw(
        AssertionError("subprocess.run should not be called without a purge target")
    ))

    assert main(["publish", "purge"]) == 2
    assert "missing hash or URL" in capsys.readouterr().err


def test_publish_list_dispatches(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(_write_command_config(tmp_path)))

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return CompletedProcess(argv, 0, stdout="HASH  TITLE\nabc123  Note\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["publish", "list"]) == 0
    assert calls == [["fake-publish-cli", "list"]]
    out = capsys.readouterr().out
    assert "HASH  TITLE" in out
    assert "abc123  Note" in out


def test_publish_gc_dispatches(tmp_path, monkeypatch):
    monkeypatch.setenv("JIMEMO_CONFIG", str(_write_command_config(tmp_path)))

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["publish", "gc"]) == 0
    assert calls == [["fake-publish-cli", "gc"]]


def test_publish_no_target_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(_write_command_config(tmp_path)))

    assert main(["publish"]) == 2
    assert "provide a file" in capsys.readouterr().err


def test_publish_missing_config_errors_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "does-not-exist.toml"))
    html = tmp_path / "page.html"
    html.write_text("<html></html>")

    assert main(["publish", str(html)]) == 1
    assert "jimemo publish setup" in capsys.readouterr().err


def test_publish_command_failure_surfaces_stderr(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(_write_command_config(tmp_path)))
    html = tmp_path / "page.html"
    html.write_text("<html></html>")

    def fake_run(argv, **kwargs):
        return CompletedProcess(argv, 1, stdout="", stderr="wrangler deploy failed")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["publish", str(html)]) == 1
    assert "wrangler deploy failed" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# publish setup (Phase 5 Task 5): only --dry-run is exercised through the
# real CLI entry point (a non-dry-run run needs interactive stdin and a
# real/mock Wrangler -- that path is covered directly against run_setup()
# in tests/test_setup.py instead). The key thing to prove here is wiring:
# `jimemo publish setup --dry-run` reaches jimemo.publish.setup.run_setup
# and never touches config loading, even with no config present at all.
# ---------------------------------------------------------------------------

def test_publish_setup_dry_run_dispatches_and_needs_no_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "does-not-exist.toml"))
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)

    assert main(["publish", "setup", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "jimemo publish setup" in out
    assert "[dry-run]" in out
    assert "TOMBSTONES" in out
    assert not (tmp_path / "does-not-exist.toml").exists()


def test_publish_setup_without_dry_run_flag_defaults_false(monkeypatch):
    # Sanity check on argparse wiring: --dry-run must default to False so
    # a bare `jimemo publish setup` attempts the real (non-dry-run) path
    # rather than silently behaving like --dry-run.
    seen_dry_run = None

    def fake_run_setup(dry_run, wrangler, config_path, io, **kw):
        nonlocal seen_dry_run
        seen_dry_run = dry_run

    monkeypatch.setattr("jimemo.publish.setup.run_setup", fake_run_setup)

    assert main(["publish", "setup"]) == 0
    assert seen_dry_run is False


# A minimal page that satisfies lint_standalone, and one that violates
# it (remote image). Reused by the pdf/publish gate tests below.
GOOD_PAGE = (
    "<!doctype html><html><head><style>body{color:#111}</style></head>"
    "<body><p>hi</p></body></html>"
)
BAD_PAGE = '<html><body><img src="https://cdn.example/x.png"></body></html>'


def test_check_passes_clean_file(tmp_path, capsys):
    f = tmp_path / "draft.html"
    f.write_text(GOOD_PAGE)
    assert main(["check", str(f)]) == 0
    assert "ok" in capsys.readouterr().out


def test_check_fails_file_with_remote_reference(tmp_path, capsys):
    f = tmp_path / "draft.html"
    f.write_text(BAD_PAGE)
    assert main(["check", str(f)]) == 1
    assert "cdn.example" in capsys.readouterr().err


def test_check_missing_file(tmp_path, capsys):
    assert main(["check", str(tmp_path / "nope.html")]) == 1
    assert "not found" in capsys.readouterr().err


def _fake_pdf_seam(monkeypatch, browser="/usr/bin/chromium"):
    """Stub jimemo.pdf's two entry points (cli imports them lazily
    inside each handler, so patching the module attributes works) and
    record render_pdf calls. No test launches a real browser."""
    calls = []
    monkeypatch.setattr(
        "jimemo.pdf.find_browser", lambda configured=None, **kw: browser
    )

    def fake_render_pdf(html_path, pdf_path, browser_, launcher=None):
        calls.append((Path(html_path), Path(pdf_path), browser_))
        Path(pdf_path).parent.mkdir(parents=True, exist_ok=True)
        Path(pdf_path).write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr("jimemo.pdf.render_pdf", fake_render_pdf)
    return calls


def test_pdf_default_output_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    f = tmp_path / "draft.html"
    f.write_text(GOOD_PAGE)

    assert main(["pdf", str(f)]) == 0

    assert calls == [(f, tmp_path / "draft.pdf", "/usr/bin/chromium")]
    assert f"wrote {tmp_path / 'draft.pdf'}" in capsys.readouterr().out


def test_pdf_explicit_output_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    f = tmp_path / "draft.html"
    f.write_text(GOOD_PAGE)
    out = tmp_path / "final" / "brief.pdf"

    assert main(["pdf", str(f), "-o", str(out)]) == 0
    assert calls[0][1] == out


def test_pdf_refuses_unverified_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    f = tmp_path / "draft.html"
    f.write_text(BAD_PAGE)

    assert main(["pdf", str(f)]) == 1

    err = capsys.readouterr().err
    assert "cdn.example" in err
    assert "--no-verify" in err
    assert calls == []


def test_pdf_no_verify_skips_the_check(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    f = tmp_path / "draft.html"
    f.write_text(BAD_PAGE)

    assert main(["pdf", str(f), "--no-verify"]) == 0
    assert len(calls) == 1


def test_pdf_without_browser_fails_with_remedy(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setattr(
        "jimemo.pdf.find_browser", lambda configured=None, **kw: None
    )
    f = tmp_path / "draft.html"
    f.write_text(GOOD_PAGE)

    assert main(["pdf", str(f)]) == 1
    assert "[pdf]" in capsys.readouterr().err


def test_pdf_missing_input_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    assert main(["pdf", str(tmp_path / "nope.html")]) == 1
    assert "not found" in capsys.readouterr().err


def test_pdf_refuses_output_path_equal_to_input(tmp_path, monkeypatch, capsys):
    """`jimemo pdf draft.html -o draft.html` must refuse before doing
    anything -- see render_pdf's own backstop in test_pdf.py. This must
    fire before the verify gate too: no point linting a file we're about
    to refuse to overwrite."""
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    f = tmp_path / "draft.html"
    original = GOOD_PAGE
    f.write_text(original)

    assert main(["pdf", str(f), "-o", str(f)]) == 1

    assert f.read_text() == original
    assert calls == []
    assert str(f) in capsys.readouterr().err


REPO = Path(__file__).resolve().parents[1]
BRIEFING_SAMPLE = REPO / "templates" / "briefing" / "sample" / "content.md"


def test_render_pdf_flag_writes_both(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    out = tmp_path / "brief.html"

    assert main(["render", "briefing", str(BRIEFING_SAMPLE), "-o", str(out), "--pdf"]) == 0

    assert out.is_file()
    assert calls == [(out, tmp_path / "brief.pdf", "/usr/bin/chromium")]
    stdout = capsys.readouterr().out
    assert f"wrote {out}" in stdout
    assert f"wrote {tmp_path / 'brief.pdf'}" in stdout


def test_render_pdf_flag_with_explicit_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    out = tmp_path / "brief.html"
    pdf_out = tmp_path / "elsewhere" / "final.pdf"

    assert main([
        "render", "briefing", str(BRIEFING_SAMPLE),
        "-o", str(out), "--pdf", str(pdf_out),
    ]) == 0
    assert calls[0][1] == pdf_out


def test_render_pdf_only_via_out_extension(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    out = tmp_path / "brief.pdf"

    assert main(["render", "briefing", str(BRIEFING_SAMPLE), "-o", str(out)]) == 0

    assert out.is_file()
    assert not (tmp_path / "brief.html").exists()
    # The intermediate HTML lived in a temp dir, not next to the PDF.
    intermediate_html = calls[0][0]
    assert intermediate_html.parent != tmp_path
    assert not intermediate_html.exists()


def test_render_pdf_only_conflicts_with_pdf_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    _fake_pdf_seam(monkeypatch)
    out = tmp_path / "brief.pdf"

    assert main([
        "render", "briefing", str(BRIEFING_SAMPLE), "-o", str(out), "--pdf",
    ]) == 2
    assert "--pdf" in capsys.readouterr().err


def test_render_pdf_fails_closed_before_rendering_without_browser(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setattr(
        "jimemo.pdf.find_browser", lambda configured=None, **kw: None
    )
    out = tmp_path / "brief.html"

    assert main(["render", "briefing", str(BRIEFING_SAMPLE), "-o", str(out), "--pdf"]) == 1

    assert not out.exists()  # refused whole invocation, wrote nothing
    assert "[pdf]" in capsys.readouterr().err


def test_render_without_pdf_never_touches_the_browser_seam(tmp_path, monkeypatch):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))

    def boom(*a, **kw):
        raise AssertionError("find_browser must not be called")

    monkeypatch.setattr("jimemo.pdf.find_browser", boom)
    out = tmp_path / "brief.html"
    assert main(["render", "briefing", str(BRIEFING_SAMPLE), "-o", str(out)]) == 0
    assert out.is_file()


def test_render_pdf_flag_value_must_end_in_dot_pdf(tmp_path, monkeypatch, capsys):
    """argparse's nargs='?' --pdf silently swallows the next positional
    as its value when --pdf appears before the positionals: `jimemo
    render --pdf briefing content.md` parses as --pdf=briefing and then
    dies with a baffling "content" required-argument error. A value
    that doesn't end in .pdf is never a real intended path, so reject
    it with a clear, actionable message instead."""
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    _fake_pdf_seam(monkeypatch)
    out = tmp_path / "x.html"

    assert main([
        "render", "briefing", str(BRIEFING_SAMPLE),
        "-o", str(out), "--pdf", "notapdf.txt",
    ]) == 2

    err = capsys.readouterr().err
    assert "--pdf" in err
    assert ".pdf" in err


def test_render_pdf_flag_before_positionals_with_real_path_works(tmp_path, monkeypatch, capsys):
    """Pins the fix's other side: --pdf PATH.pdf placed BEFORE the
    positionals (a real, well-formed path) must still work -- the new
    validation only rejects values that don't end in .pdf, not every
    --pdf-before-positionals invocation."""
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    out = tmp_path / "out.pdf"
    html_out = tmp_path / "x.html"

    assert main([
        "render", "--pdf", str(out),
        "briefing", str(BRIEFING_SAMPLE), "-o", str(html_out),
    ]) == 0

    assert calls == [(html_out, out, "/usr/bin/chromium")]


def test_render_pdf_flag_refuses_content_file_as_pdf_target(tmp_path, monkeypatch, capsys):
    """--pdf can point anywhere, including (by accident) at the content
    file being rendered. That must be refused before any conversion --
    BRIEFING_SAMPLE is a real repo file, so the guard must fire before
    render_pdf ever runs (and with _fake_pdf_seam installed nothing real
    would run anyway); assert the sample is untouched either way."""
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    original = BRIEFING_SAMPLE.read_bytes()
    out = tmp_path / "x.html"

    assert main([
        "render", "briefing", str(BRIEFING_SAMPLE),
        "-o", str(out), "--pdf", str(BRIEFING_SAMPLE),
    ]) == 2

    assert BRIEFING_SAMPLE.read_bytes() == original
    assert calls == []
    assert str(BRIEFING_SAMPLE) in capsys.readouterr().err


# ---------------------------------------------------------------------------
# publish verify gate (Phase 5 Task 8): a fake publisher + _publish_env helper
# stand in for a real jimemo.publish backend. The monkeypatch of
# jimemo.publish.get_publisher mirrors what actually happens end to end --
# the CLI's own lazy import of get_publisher at call time sees the monkeypatched
# version.
# ---------------------------------------------------------------------------

class FakePublisher:
    def __init__(self):
        self.published = []

    def publish(self, path, title=None):
        self.published.append(Path(path))
        return "https://pages.example/abc123/"


def _publish_env(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[publish]\nbackend = "command"\ncommand = "true"\n')
    monkeypatch.setenv("JIMEMO_CONFIG", str(cfg))
    fake = FakePublisher()
    monkeypatch.setattr("jimemo.publish.get_publisher", lambda cfg, **kw: fake)
    return fake


def test_publish_refuses_unverified_html(tmp_path, monkeypatch, capsys):
    fake = _publish_env(tmp_path, monkeypatch)
    f = tmp_path / "draft.html"
    f.write_text(BAD_PAGE)

    assert main(["publish", str(f)]) == 1

    err = capsys.readouterr().err
    assert "cdn.example" in err
    assert "--no-verify" in err
    assert fake.published == []


def test_publish_verifies_then_publishes_clean_html(tmp_path, monkeypatch, capsys):
    fake = _publish_env(tmp_path, monkeypatch)
    f = tmp_path / "draft.html"
    f.write_text(GOOD_PAGE)

    assert main(["publish", str(f)]) == 0
    assert fake.published == [f]
    assert "https://pages.example/abc123/" in capsys.readouterr().out


def test_publish_no_verify_skips_the_check(tmp_path, monkeypatch, capsys):
    fake = _publish_env(tmp_path, monkeypatch)
    f = tmp_path / "draft.html"
    f.write_text(BAD_PAGE)

    assert main(["publish", str(f), "--no-verify"]) == 0
    assert fake.published == [f]


def test_publish_non_html_passes_through_unverified(tmp_path, monkeypatch, capsys):
    fake = _publish_env(tmp_path, monkeypatch)
    f = tmp_path / "brief.pdf"
    f.write_bytes(b"%PDF-1.4 fake")

    assert main(["publish", str(f)]) == 0
    assert fake.published == [f]


def test_doctor_stale_labels_warning_names_the_manifest_procedure(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setattr("jimemo.suggest.is_stale_labels", lambda m, d: True)
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "WARNING stale suitability labels" in out
    # the remedy is a manifest edit, not a CLI command that doesn't exist
    assert "labeled_hash" in out
    assert "sha256" in out
    assert "suggest tuning" not in out


def test_doctor_labels_config_errors_as_config_not_pdf(
    tmp_path, monkeypatch, capsys
):
    bad = tmp_path / "config.toml"
    bad.write_text('[publish]\nbackend = "carrier-pigeon"\n')
    monkeypatch.setenv("JIMEMO_CONFIG", str(bad))
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "WARNING config:" in out
    assert "WARNING pdf config:" not in out


def test_doctor_reports_pdf_browser_found(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setattr(
        "jimemo.pdf.find_browser", lambda configured=None, **kw: "/usr/bin/chromium"
    )
    assert main(["doctor"]) == 0
    assert "ok   pdf browser (/usr/bin/chromium)" in capsys.readouterr().out


def test_doctor_reports_pdf_browser_missing_without_failing(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setattr(
        "jimemo.pdf.find_browser", lambda configured=None, **kw: None
    )
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "info pdf browser not found" in out
    assert "jimemo pdf unavailable" in out


def test_scaffold_prints_md_skeleton(capsys):
    assert main(["scaffold", "briefing"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("---\n")
    assert 'title: ""' in out


def test_scaffold_writes_file_and_renders_after_fill(tmp_path, capsys):
    out = tmp_path / "skeleton.yaml"
    assert main(["scaffold", "ops-board", "-o", str(out)]) == 0
    assert out.is_file()
    # The unfilled skeleton itself must render: proof nobody has to
    # reverse-engineer anything before seeing a first page.
    html_out = tmp_path / "page.html"
    assert main(["render", "ops-board", str(out), "-o", str(html_out)]) == 0
    assert html_out.is_file()


def test_scaffold_unknown_template(capsys):
    assert main(["scaffold", "nope"]) == 1
    assert "unknown template" in capsys.readouterr().err


# --- render --figure NAME=FILE (jimemo#4smm) ------------------------------

FIGURE_SVG = (
    '<svg viewBox="0 0 760 100" role="img" aria-label="A flow." onload="evil()">'
    '<script>evil()</script>'
    '<rect width="200" height="60" style="fill:var(--jm-accent)"/></svg>'
)


def _figure_inputs(tmp_path, body="Intro.\n\n[[DIAGRAM:FLOW]]\n\nOutro.\n"):
    content = tmp_path / "note.md"
    content.write_text('---\ntitle: "T"\ndate: "18 September 2026"\n---\n' + body)
    svg = tmp_path / "flow.svg"
    svg.write_text(FIGURE_SVG)
    return content, svg


def test_render_figure_splices_sanitized_svg(tmp_path, capsys):
    content, svg = _figure_inputs(tmp_path)
    out = tmp_path / "note.html"

    assert main(["render", "briefing", str(content), "-o", str(out),
                 "--figure", f"FLOW={svg}"]) == 0

    html = out.read_text()
    assert '<figure class="jm-figure" style="contain:paint"><svg viewBox="0 0 760 100"' in html
    assert 'style="fill:var(--jm-accent)"' in html
    assert "[[DIAGRAM:" not in html and "evil" not in html
    # the written page is self-contained: `jimemo check` agrees
    assert main(["check", str(out)]) == 0


def test_render_figure_file_path_may_contain_equals(tmp_path, capsys):
    content, svg = _figure_inputs(tmp_path)
    odd = tmp_path / "a=b.svg"
    odd.write_text(svg.read_text())
    out = tmp_path / "note.html"
    assert main(["render", "briefing", str(content), "-o", str(out),
                 "--figure", f"FLOW={odd}"]) == 0
    assert "jm-figure" in out.read_text()


def test_render_figure_unknown_placeholder_is_an_error_not_a_noop(tmp_path, capsys):
    content, svg = _figure_inputs(tmp_path)
    out = tmp_path / "note.html"

    assert main(["render", "briefing", str(content), "-o", str(out),
                 "--figure", f"FLOW={svg}", "--figure", f"NOPE={svg}"]) == 1

    assert "[[DIAGRAM:NOPE]]" in capsys.readouterr().err
    assert not out.exists()


def test_render_figure_that_is_not_svg_is_an_error_naming_it(tmp_path, capsys):
    content, svg = _figure_inputs(tmp_path)
    svg.write_text("<p>not an svg</p>")
    out = tmp_path / "note.html"
    assert main(["render", "briefing", str(content), "-o", str(out),
                 "--figure", f"FLOW={svg}"]) == 1
    assert "--figure FLOW: no <svg> root" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize("value, needle", [
    ("FLOW", "expected NAME=FILE"),                 # no "="
    ("=flow.svg", "NAME must be"),                  # empty NAME
    ("BAD NAME=flow.svg", "NAME must be"),          # NAME outside [A-Za-z0-9_-]
    ("FLOW\n=flow.svg", "NAME must be"),            # "$" alone would accept a trailing newline
    ("N" * 65 + "=flow.svg", "NAME must be 1-64"),  # length cap
    ("FLOW=", "FILE is empty"),
    ("FLOW=missing.svg", "cannot read 'missing.svg'"),
    ("FLOW=.", "cannot read '.'"),                  # a directory
    ("FLOW=no-such\nfile.svg", "cannot read 'no-such\\nfile.svg'"),  # newline in FILE: still one line
    ("FLOW=nul\x00.svg", "cannot read"),            # NUL in FILE
])
def test_render_figure_malformed_value_exits_2_with_one_line(
    tmp_path, capsys, monkeypatch, value, needle
):
    content, _svg = _figure_inputs(tmp_path)
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "note.html"

    assert main(["render", "briefing", str(content), "-o", str(out),
                 "--figure", value]) == 2

    err = capsys.readouterr().err
    assert needle in err and "--figure" in err
    assert len(err.strip().splitlines()) == 1
    assert not out.exists()


def test_render_figure_name_with_terminal_escapes_is_refused(tmp_path, capsys):
    # The CLI's own --figure messages print NAME bare, but only AFTER the
    # allowlist above has accepted it ([A-Za-z0-9_-], 1-64), and a rejected
    # value is echoed through repr(), which escapes what a terminal acts on.
    # That is why jimemo#v72t filtered render.py's messages and not these;
    # this pins the CLI half of that. ESC, U+009B (CSI) and U+202E (RLO).
    content, svg = _figure_inputs(tmp_path)
    assert main(["render", "briefing", str(content), "-o", str(tmp_path / "o.html"),
                 "--figure", "FL\x1b[31mOW\x9b2K\u202e=" + str(svg)]) == 2
    err = capsys.readouterr().err
    assert "NAME must be" in err
    for ch in ("\x1b", "\x9b", "\u202e"):
        assert ch not in err, hex(ord(ch))
    assert len(err.strip().splitlines()) == 1


def test_render_figure_duplicate_name_exits_2(tmp_path, capsys):
    content, svg = _figure_inputs(tmp_path)
    assert main(["render", "briefing", str(content), "-o", str(tmp_path / "o.html"),
                 "--figure", f"FLOW={svg}", "--figure", f"FLOW={svg}"]) == 2
    err = capsys.readouterr().err
    assert "FLOW" in err and "more than once" in err
    assert len(err.strip().splitlines()) == 1


def test_render_figure_non_utf8_file_exits_2(tmp_path, capsys):
    content, svg = _figure_inputs(tmp_path)
    svg.write_bytes(b"<svg>\xff\xfe</svg>")
    assert main(["render", "briefing", str(content), "-o", str(tmp_path / "o.html"),
                 "--figure", f"FLOW={svg}"]) == 2
    err = capsys.readouterr().err
    assert "UTF-8" in err and len(err.strip().splitlines()) == 1


@pytest.mark.parametrize("argv_head", [
    ["render", "auto"],                      # auto-selection prints diagnostics
    ["render", "no-such-template"],          # unknown template would exit 1
])
def test_render_figure_is_validated_before_anything_else(tmp_path, capsys, argv_head):
    """A malformed --figure is reported alone, first: not after
    auto-selection chatter, and not masked by an earlier exit-1 check."""
    content, _svg = _figure_inputs(tmp_path)
    assert main([*argv_head, str(content), "--figure", "FLOW"]) == 2
    err = capsys.readouterr().err
    assert "--figure" in err and len(err.strip().splitlines()) == 1

    assert main([*argv_head, str(tmp_path / "absent.md"), "--figure", "FLOW"]) == 2
    err = capsys.readouterr().err
    assert "--figure" in err and len(err.strip().splitlines()) == 1


def test_render_help_states_the_viewbox_non_goal(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["render", "--help"])
    assert exc_info.value.code == 0
    out = " ".join(capsys.readouterr().out.split())  # argparse re-wraps
    assert "--figure NAME=FILE" in out
    assert "[[DIAGRAM:NAME]]" in out and "sanitized" in out
    assert "viewBox" in out and "cannot be detected" in out
    assert "docs/diagrams.md" in out


def test_render_figure_error_echo_is_bounded(tmp_path, capsys):
    content, _svg = _figure_inputs(tmp_path)
    assert main(["render", "briefing", str(content), "--figure", "x" * 200000]) == 2
    assert len(capsys.readouterr().err) < 400


@pytest.mark.parametrize("out_name, extra", [
    ("flow.svg", []),                       # -o names the figure source
    ("note.html", ["--pdf=flow.svg.pdf"]),  # control: a different pdf path is fine
])
def test_render_refuses_to_overwrite_a_figure_file(tmp_path, monkeypatch, capsys, out_name, extra):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    _fake_pdf_seam(monkeypatch)
    content, svg = _figure_inputs(tmp_path)
    before = svg.read_text()
    code = main(["render", "briefing", str(content), "-o", str(tmp_path / out_name),
                 "--figure", f"FLOW={svg}", *extra])
    assert svg.read_text() == before
    if out_name == "flow.svg":
        assert code == 2
        err = capsys.readouterr().err
        assert "is a --figure file; refusing to overwrite it" in err
    else:
        assert code == 0


def test_render_figure_pdf_target_equal_to_figure_file_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    content, _svg = _figure_inputs(tmp_path)
    fig = tmp_path / "flow.pdf"           # an SVG file with an unlucky name
    fig.write_text(FIGURE_SVG)
    assert main(["render", "briefing", str(content), "-o", str(tmp_path / "n.html"),
                 f"--pdf={fig}", "--figure", f"FLOW={fig}"]) == 2
    assert fig.read_text() == FIGURE_SVG and calls == []


def test_render_figure_with_pdf_only_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    content, svg = _figure_inputs(tmp_path)
    assert main(["render", "briefing", str(content), "-o", str(tmp_path / "n.pdf"),
                 "--figure", f"FLOW={svg}"]) == 0
    assert len(calls) == 1


def test_render_figure_content_errors_exit_1_and_write_nothing(tmp_path, capsys):
    body = "[[DIAGRAM:A]]\n\n[[DIAGRAM:B]]\n"
    content, _svg = _figure_inputs(tmp_path, body)
    a = tmp_path / "a.svg"
    a.write_text('<svg><defs><linearGradient id="grad"/></defs></svg>')
    b = tmp_path / "b.svg"
    b.write_text('<svg><defs><radialGradient id="grad"/></defs></svg>')
    out = tmp_path / "o.html"
    assert main(["render", "briefing", str(content), "-o", str(out),
                 "--figure", f"A={a}", "--figure", f"B={b}"]) == 1
    assert "both define id='grad'" in capsys.readouterr().err and not out.exists()

    b.write_text('<svg><defs><g id="sym"/></defs><use href="#sym"/></svg>')  # lint refuses <use href>
    assert main(["render", "briefing", str(content), "-o", str(out),
                 "--figure", f"A={a}", "--figure", f"B={b}"]) == 1
    err = capsys.readouterr().err
    assert "use href" in err and "this page includes --figure SVG" in err and not out.exists()


def test_render_refuses_to_overwrite_a_figure_file_under_another_name(tmp_path, capsys):
    """File identity, not path spelling: a hard link is the same file, and
    on a case-insensitive filesystem so is FLOW.SVG."""
    import os

    content, svg = _figure_inputs(tmp_path)
    before = svg.read_text()
    aliases = []
    link = tmp_path / "alias.svg"
    try:
        os.link(svg, link)
        aliases.append(link)
    except OSError:
        pass  # filesystem without hard links
    upper = tmp_path / "FLOW.SVG"
    if upper.exists():  # only true on a case-insensitive filesystem
        aliases.append(upper)
    if not aliases:
        pytest.skip("no way to alias a file on this filesystem")

    for alias in aliases:
        assert main(["render", "briefing", str(content), "-o", str(alias),
                     "--figure", f"FLOW={svg}"]) == 2
        assert "refusing to overwrite it" in capsys.readouterr().err
        assert svg.read_text() == before
