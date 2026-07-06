import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo._vendor import VENDOR_DIR, add_vendor_to_path


def test_vendor_dir_exists():
    assert VENDOR_DIR.is_dir()
    assert (VENDOR_DIR / "SHA256SUMS").is_file()


def test_vendored_jinja2_is_used():
    add_vendor_to_path()
    import jinja2
    assert Path(jinja2.__file__).resolve().is_relative_to(VENDOR_DIR)


def test_vendored_tomli_is_used():
    add_vendor_to_path()
    import tomli
    assert Path(tomli.__file__).resolve().is_relative_to(VENDOR_DIR)
    assert tomli.loads("a = 1\n") == {"a": 1}


def test_add_vendor_is_idempotent():
    add_vendor_to_path()
    add_vendor_to_path()
    assert sys.path.count(str(VENDOR_DIR)) == 1
