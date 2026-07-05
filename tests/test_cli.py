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
