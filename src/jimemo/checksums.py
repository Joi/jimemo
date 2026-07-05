"""Verify vendor/ against SHA256SUMS. Detects tampered, missing, and
unlisted files. Returns problems as strings; empty list means clean."""
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

    reported_symlinks = set()
    real_files = {}
    for f in sorted(vendor_dir.rglob("*")):
        rel = f.relative_to(vendor_dir)
        if f.is_symlink():
            problems.append(f"symlink not allowed: {rel}")
            reported_symlinks.add(rel)
            continue
        if f.is_dir() or f == sums_file:
            continue
        real_files[rel] = f

    for rel, expected in sorted(listed.items()):
        f = real_files.get(rel)
        if f is None:
            if rel not in reported_symlinks:
                problems.append(f"missing vendored file: {rel}")
            continue
        actual = hashlib.sha256(f.read_bytes()).hexdigest()
        if actual != expected:
            problems.append(f"checksum mismatch: {rel}")

    for rel in sorted(real_files):
        if rel not in listed:
            problems.append(f"unlisted file: {rel}")

    return problems
