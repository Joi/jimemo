import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import jimemo
from jimemo.cli import main


def test_version_flag(capsys):
    assert main(["--version"]) == 0
    assert jimemo.__version__ in capsys.readouterr().out


def test_doctor_on_clean_repo(capsys):
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "python" in out.lower()
    assert "vendor" in out.lower()


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
