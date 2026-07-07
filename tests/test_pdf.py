import sys
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.errors import PdfError
from jimemo.pdf import NO_BROWSER_MESSAGE, find_browser


def no_which(name):
    return None


def no_exists(path):
    return False


def test_find_browser_prefers_configured_path():
    result = find_browser(
        "/opt/thorium/thorium",
        which=no_which,
        exists=lambda p: p == "/opt/thorium/thorium",
    )
    assert result == "/opt/thorium/thorium"


def test_find_browser_missing_configured_path_errors():
    with pytest.raises(PdfError) as exc_info:
        find_browser("/nope/chrome", which=no_which, exists=no_exists)
    assert "/nope/chrome" in str(exc_info.value)


def test_find_browser_falls_back_to_path_names():
    hits = {"google-chrome": "/usr/bin/google-chrome"}
    assert (
        find_browser(None, which=hits.get, exists=no_exists)
        == "/usr/bin/google-chrome"
    )


def test_find_browser_path_beats_macos_bundle():
    hits = {"chromium": "/usr/bin/chromium"}
    assert find_browser(None, which=hits.get, exists=lambda p: True) == "/usr/bin/chromium"


def test_find_browser_falls_back_to_macos_bundles():
    mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    assert find_browser(None, which=no_which, exists=lambda p: p == mac) == mac


def test_find_browser_returns_none_when_nothing_found():
    assert find_browser(None, which=no_which, exists=no_exists) is None


def test_no_browser_message_names_the_config_key():
    assert "[pdf]" in NO_BROWSER_MESSAGE
    assert "config.toml" in NO_BROWSER_MESSAGE


from jimemo.pdf import TIMEOUT_SECONDS, render_pdf


class FakeRunner:
    """Records every argv. Unless told otherwise, writes fake bytes to
    the --print-to-pdf target so render_pdf's output-exists check
    passes -- the same observable a real browser run produces."""

    def __init__(self, result, write_pdf=True):
        self.calls = []
        self._result = result
        self._write_pdf = write_pdf

    def __call__(self, argv):
        self.calls.append(argv)
        if self._write_pdf:
            for arg in argv:
                if arg.startswith("--print-to-pdf="):
                    Path(arg.split("=", 1)[1]).write_bytes(b"%PDF-1.4 fake")
        return self._result


def _ok_result():
    return CompletedProcess([], 0, stdout="", stderr="")


def test_render_pdf_builds_expected_argv(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    pdf = tmp_path / "page.pdf"
    runner = FakeRunner(_ok_result())

    render_pdf(html, pdf, "/usr/bin/chromium", runner=runner)

    (argv,) = runner.calls
    assert argv[0] == "/usr/bin/chromium"
    assert "--headless" in argv
    assert "--disable-gpu" in argv
    assert "--no-first-run" in argv
    assert "--no-default-browser-check" in argv
    assert "--virtual-time-budget=10000" in argv
    assert "--no-pdf-header-footer" in argv
    assert f"--print-to-pdf={pdf.resolve()}" in argv
    assert argv[-1] == html.resolve().as_uri()
    assert any(a.startswith("--user-data-dir=") for a in argv)


def test_render_pdf_uses_throwaway_profile_and_cleans_it_up(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    runner = FakeRunner(_ok_result())

    render_pdf(html, tmp_path / "page.pdf", "/usr/bin/chromium", runner=runner)

    (argv,) = runner.calls
    profile = next(a for a in argv if a.startswith("--user-data-dir="))
    profile_dir = Path(profile.split("=", 1)[1])
    assert not profile_dir.exists()


def test_render_pdf_creates_output_parent_dir(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    pdf = tmp_path / "sub" / "dir" / "page.pdf"

    render_pdf(html, pdf, "/usr/bin/chromium", runner=FakeRunner(_ok_result()))

    assert pdf.is_file()


def test_render_pdf_nonzero_exit_raises(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    runner = FakeRunner(
        CompletedProcess([], 21, stdout="", stderr="something broke\n"),
        write_pdf=False,
    )

    with pytest.raises(PdfError) as exc_info:
        render_pdf(html, tmp_path / "page.pdf", "/usr/bin/chromium", runner=runner)
    assert "21" in str(exc_info.value)
    assert "something broke" in str(exc_info.value)


def test_render_pdf_missing_output_raises(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    runner = FakeRunner(_ok_result(), write_pdf=False)

    with pytest.raises(PdfError) as exc_info:
        render_pdf(html, tmp_path / "page.pdf", "/usr/bin/chromium", runner=runner)
    assert "no PDF" in str(exc_info.value)


def test_render_pdf_timeout_raises(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")

    def timeout_runner(argv):
        raise TimeoutExpired(argv, TIMEOUT_SECONDS)

    with pytest.raises(PdfError) as exc_info:
        render_pdf(html, tmp_path / "page.pdf", "/usr/bin/chromium", runner=timeout_runner)
    assert str(TIMEOUT_SECONDS) in str(exc_info.value)


def test_render_pdf_unrunnable_browser_raises(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")

    def missing_runner(argv):
        raise FileNotFoundError("no such file")

    with pytest.raises(PdfError) as exc_info:
        render_pdf(html, tmp_path / "page.pdf", "/gone/chrome", runner=missing_runner)
    assert "/gone/chrome" in str(exc_info.value)
