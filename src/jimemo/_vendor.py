"""Puts the repo's vendor/ directory on sys.path.

Users never pip-install jimemo's dependencies; all runtime imports beyond
the stdlib come from vendor/. Call add_vendor_to_path() before importing
jinja2/markupsafe/markdown anywhere in this package.
"""
import sys
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parents[2] / "vendor"


def add_vendor_to_path() -> None:
    vendor = str(VENDOR_DIR)
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
