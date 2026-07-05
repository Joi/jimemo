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


def test_unlisted_native_extension_is_reported(tmp_path):
    vendor = make_vendor(tmp_path)
    (vendor / "pkg" / "_speedups.so").write_bytes(b"\x00junk\x01")
    assert any("unlisted" in p for p in verify_checksums(vendor))


def test_unlisted_pycache_bytecode_is_reported(tmp_path):
    vendor = make_vendor(tmp_path)
    pycache = vendor / "pkg" / "__pycache__"
    pycache.mkdir()
    (pycache / "mod.cpython-39.pyc").write_bytes(b"\x00junk\x01")
    assert any("unlisted" in p for p in verify_checksums(vendor))


def test_symlink_to_listed_file_is_reported(tmp_path):
    vendor = make_vendor(tmp_path)
    (vendor / "alias.py").symlink_to(vendor / "pkg" / "mod.py")
    problems = verify_checksums(vendor)
    assert any("symlink not allowed" in p and "alias.py" in p for p in problems)


def test_symlinked_directory_is_reported(tmp_path):
    vendor = make_vendor(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.py").write_text("import os\n")
    (vendor / "evilpkg").symlink_to(outside)
    problems = verify_checksums(vendor)
    assert any("symlink not allowed" in p and "evilpkg" in p for p in problems)


def test_listed_symlink_is_reported_not_followed(tmp_path):
    vendor = make_vendor(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("import os\n")
    digest = hashlib.sha256(outside.read_bytes()).hexdigest()
    (vendor / "pkg" / "link.py").symlink_to(outside)
    with (vendor / "SHA256SUMS").open("a") as fh:
        fh.write(f"{digest}  ./pkg/link.py\n")
    problems = verify_checksums(vendor)
    link_problems = [p for p in problems if "pkg/link.py" in p]
    assert link_problems == ["symlink not allowed: pkg/link.py"]


def test_missing_sums_file_is_reported(tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    assert any("SHA256SUMS" in p for p in verify_checksums(vendor))


def test_real_repo_vendor_is_clean():
    repo_vendor = Path(__file__).resolve().parents[1] / "vendor"
    assert verify_checksums(repo_vendor) == []
