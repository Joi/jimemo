"""Golden-file harness for seed templates (Tasks 7-11 add the templates
themselves under templates/<name>/{manifest.json,template.html.j2,sample/}
plus tests/goldens/<name>.html). Until then this collects zero cases and
skips cleanly rather than failing.

Usage once goldens exist:
    JIMEMO_UPDATE_GOLDENS=1 python3 -m pytest tests/test_golden.py
rewrites tests/goldens/<name>.html to match current render output.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo._paths import REPO_ROOT
from jimemo.content import load_content
from jimemo.manifest import load_manifest
from jimemo.render import render_page

TEMPLATES_ROOT = REPO_ROOT / "templates"
GOLDENS_DIR = Path(__file__).resolve().parent / "goldens"

CONTENT_SUFFIXES = (".md", ".json", ".yaml", ".yml")


def discover_golden_cases(templates_root: Path = TEMPLATES_ROOT):
    """One case per (template, sample content file): (name, template_dir, content_path)."""
    cases = []
    if not templates_root.is_dir():
        return cases
    for template_dir in sorted(p for p in templates_root.iterdir() if p.is_dir()):
        if not (template_dir / "manifest.json").is_file():
            continue
        sample_dir = template_dir / "sample"
        if not sample_dir.is_dir():
            continue
        for content_path in sorted(sample_dir.iterdir()):
            if content_path.suffix.lower() in CONTENT_SUFFIXES:
                cases.append((template_dir.name, template_dir, content_path))
    return cases


CASES = discover_golden_cases()


@pytest.mark.skipif(not CASES, reason="no seed templates with sample/ content yet (Tasks 7-11)")
@pytest.mark.parametrize(
    "name, template_dir, content_path", CASES, ids=[c[0] + ":" + c[2].name for c in CASES]
)
def test_golden(name, template_dir, content_path):
    manifest = load_manifest(template_dir)
    content = load_content(content_path, manifest)
    html = render_page(template_dir, content, base_dir=content_path.parent)

    golden_path = GOLDENS_DIR / f"{name}.html"

    if os.environ.get("JIMEMO_UPDATE_GOLDENS"):
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(html, encoding="utf-8")
        return

    assert golden_path.is_file(), (
        f"no golden for {name!r} yet; run with JIMEMO_UPDATE_GOLDENS=1 to create it "
        f"at {golden_path}"
    )
    expected = golden_path.read_text(encoding="utf-8")
    assert html == expected, f"rendered output for {name!r} drifted from {golden_path}"


def test_discovery_does_not_raise_when_templates_dir_is_absent(tmp_path):
    """templates/ doesn't exist yet this early in Phase 3 (Tasks 7-11 add
    it); the harness must not raise, just find nothing."""
    assert discover_golden_cases(tmp_path / "no-such-dir") == []
