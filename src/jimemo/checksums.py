"""Verify vendor/ against SHA256SUMS. Detects tampered, missing, and
unlisted files. Returns problems as strings; empty list means clean."""
import hashlib
from pathlib import Path


def verify_checksums(vendor_dir: Path) -> list[str]:
    sums_file = vendor_dir / "SHA256SUMS"
    if not sums_file.is_file():
        return [f"SHA256SUMS not found in {vendor_dir}"]

    problems: list[str] = []
    listed: dict[Path, str] = {}
    for line in sums_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            expected, rel = line.split(None, 1)
        except ValueError:
            problems.append(f"malformed SHA256SUMS line: {line!r}")
            continue
        listed[Path(rel.strip().lstrip("*"))] = expected

    excluded: set[Path] = set()
    real_files: dict[Path, Path] = {}
    for f in sorted(vendor_dir.rglob("*")):
        rel = f.relative_to(vendor_dir)
        if f.is_symlink():
            problems.append(f"symlink not allowed: {rel}")
            excluded.add(rel)
            continue
        if f.is_dir() or f == sums_file:
            continue
        if not f.is_file():
            problems.append(f"special file not allowed: {rel}")
            excluded.add(rel)
            continue
        real_files[rel] = f

    for rel, expected in sorted(listed.items()):
        f = real_files.get(rel)
        if f is None:
            if rel not in excluded:
                problems.append(f"missing vendored file: {rel}")
            continue
        actual = hashlib.sha256(f.read_bytes()).hexdigest()
        if actual != expected:
            problems.append(f"checksum mismatch: {rel}")

    for rel in sorted(real_files):
        if rel not in listed:
            problems.append(f"unlisted file: {rel}")

    return problems
