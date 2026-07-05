import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import jimemo
from jimemo.cli import main


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
    assert "ok   vendored imports (jinja2, markdown, yaml)" in out


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
        "print('OK')\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "checksum mismatch" in result.stdout
    assert "skip vendored imports" in result.stdout
    assert "OK" in result.stdout
