import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.errors import PublishError
from jimemo.publish.staging import stage_page


def test_default_hash_is_24_lowercase_hex(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html><body>hi</body></html>")
    work_dir = tmp_path / "work"

    page_hash, staged_dir = stage_page(html, work_dir)

    assert len(page_hash) == 24
    assert page_hash == page_hash.lower()
    assert all(c in "0123456789abcdef" for c in page_hash)
    assert staged_dir == work_dir / page_hash


def test_two_stagings_get_different_hashes(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    work_dir = tmp_path / "work"

    hash_a, _ = stage_page(html, work_dir)
    hash_b, _ = stage_page(html, work_dir)

    assert hash_a != hash_b


def test_injected_token_source_is_deterministic(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    work_dir = tmp_path / "work"

    page_hash, staged_dir = stage_page(html, work_dir, token_source=lambda n: "ab" * n)

    assert page_hash == "ab" * 12
    assert staged_dir == work_dir / ("ab" * 12)


def test_index_html_staged_with_right_bytes(tmp_path):
    html = tmp_path / "page.html"
    content = "<html><body>hello, staged world</body></html>"
    html.write_text(content, encoding="utf-8")
    work_dir = tmp_path / "work"

    page_hash, staged_dir = stage_page(html, work_dir, token_source=lambda n: "cd" * n)

    staged_index = staged_dir / "index.html"
    assert staged_index.is_file()
    assert staged_index.read_text(encoding="utf-8") == content
    assert staged_index.read_bytes() == html.read_bytes()


def test_missing_input_raises_publish_error(tmp_path):
    missing = tmp_path / "does-not-exist.html"
    work_dir = tmp_path / "work"

    with pytest.raises(PublishError) as exc:
        stage_page(missing, work_dir)
    assert str(missing) in str(exc.value)
