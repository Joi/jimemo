import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.checksums import verify_checksums


def make_vendor(tmp_path: Path) -> Path:
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    f = vendor / "pkg" / "mod.py"
    f.parent.mkdir()
    f.write_text("x = 1\n")
    digest = hashlib.sha256(f.read_bytes()).hexdigest()
    (vendor / "SHA256SUMS").write_text(f"{digest}  ./pkg/mod.py\n")
    return vendor


def test_clean_vendor_verifies(tmp_path):
    assert verify_checksums(make_vendor(tmp_path)) == []


def test_tampered_file_is_reported(tmp_path):
    vendor = make_vendor(tmp_path)
    (vendor / "pkg" / "mod.py").write_text("x = 2\n")
    problems = verify_checksums(vendor)
    assert len(problems) == 1
    assert "mismatch" in problems[0]
    assert "pkg/mod.py" in problems[0]


def test_missing_file_is_reported(tmp_path):
    vendor = make_vendor(tmp_path)
    (vendor / "pkg" / "mod.py").unlink()
    assert any("missing" in p for p in verify_checksums(vendor))


def test_unlisted_python_file_is_reported(tmp_path):
    vendor = make_vendor(tmp_path)
    (vendor / "pkg" / "sneaky.py").write_text("import os\n")
    assert any("unlisted" in p for p in verify_checksums(vendor))


def test_missing_sums_file_is_reported(tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    assert any("SHA256SUMS" in p for p in verify_checksums(vendor))


def test_real_repo_vendor_is_clean():
    repo_vendor = Path(__file__).resolve().parents[1] / "vendor"
    assert verify_checksums(repo_vendor) == []
