"""Verify vendor/ against SHA256SUMS. Detects tampered, missing, and
unlisted-.py files. Returns problems as strings; empty list means clean."""
import hashlib
from pathlib import Path


def verify_checksums(vendor_dir: Path) -> list:
    sums_file = vendor_dir / "SHA256SUMS"
    if not sums_file.is_file():
        return [f"SHA256SUMS not found in {vendor_dir}"]

    problems = []
    listed = {}
    for line in sums_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        expected, rel = line.split(None, 1)
        listed[Path(rel.strip().lstrip("*"))] = expected

    for rel, expected in sorted(listed.items()):
        f = vendor_dir / rel
        if not f.is_file():
            problems.append(f"missing vendored file: {rel}")
            continue
        actual = hashlib.sha256(f.read_bytes()).hexdigest()
        if actual != expected:
            problems.append(f"checksum mismatch: {rel}")

    listed_resolved = {(vendor_dir / rel).resolve() for rel in listed}
    for f in sorted(vendor_dir.rglob("*.py")):
        if f.resolve() not in listed_resolved:
            problems.append(f"unlisted python file: {f.relative_to(vendor_dir)}")

    return problems
