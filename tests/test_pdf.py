import sys
from pathlib import Path

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


class FakeProcess:
    """Scripted stand-in for subprocess.Popen: `poll_returns` yields per
    poll() call (None = still running); kill() is recorded."""

    def __init__(self, poll_returns):
        self._poll_returns = list(poll_returns)
        self.killed = False

    def poll(self):
        if len(self._poll_returns) == 1:
            return self._poll_returns[0]
        return self._poll_returns.pop(0)

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


class FakeLauncher:
    """Records argv and the print copy's content at launch time, writes
    scripted bytes to the --print-to-pdf target, and returns a scripted
    FakeProcess."""

    def __init__(self, process, pdf_bytes=b"%PDF-1.4 fake"):
        self.calls = []
        self.copy_contents = []
        self._process = process
        self._pdf_bytes = pdf_bytes
        self.log_handle = None

    def __call__(self, argv, log_handle):
        self.calls.append(argv)
        self.log_handle = log_handle
        target = next(a for a in argv if a.startswith("file://"))
        from urllib.parse import urlparse
        from urllib.request import url2pathname

        copy_path = Path(url2pathname(urlparse(target).path))
        self.copy_contents.append(copy_path.read_text(encoding="utf-8"))
        if self._pdf_bytes is not None:
            for a in argv:
                if a.startswith("--print-to-pdf="):
                    Path(a.split("=", 1)[1]).write_bytes(self._pdf_bytes)
        return self._process


def test_render_pdf_builds_expected_argv(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    pdf = tmp_path / "page.pdf"
    launcher = FakeLauncher(FakeProcess([0]))

    render_pdf(html, pdf, "/usr/bin/chromium", launcher=launcher)

    (argv,) = launcher.calls
    assert argv[0] == "/usr/bin/chromium"
    assert "--headless" in argv
    assert "--disable-gpu" in argv
    assert "--no-first-run" in argv
    assert "--no-default-browser-check" in argv
    assert "--disable-background-networking" in argv
    assert "--disable-component-update" in argv
    assert "--virtual-time-budget=10000" in argv
    assert "--no-pdf-header-footer" in argv
    print_to_pdf_args = [a for a in argv if a.startswith("--print-to-pdf=")]
    assert len(print_to_pdf_args) == 1
    temp_target = Path(print_to_pdf_args[0].split("=", 1)[1])
    # Printed to a pid-unique temp name next to the real output, not the
    # output path itself -- see test_render_pdf_ignores_a_preexisting_
    # stale_file_at_pdf_path for why.
    assert temp_target.parent == pdf.resolve().parent
    assert temp_target.name != pdf.name
    assert temp_target != pdf.resolve()
    assert any(a.startswith("--user-data-dir=") for a in argv)
    assert argv[-1].startswith("file://")
    assert Path(argv[-1][len("file://") :]).name != html.name


def test_render_pdf_prints_a_no_animation_copy_not_the_original(tmp_path):
    html = tmp_path / "page.html"
    original = (
        "<html><body><script>LIB</script><canvas id=\"x\"></canvas></body></html>"
    )
    html.write_text(original)
    launcher = FakeLauncher(FakeProcess([0]))

    render_pdf(html, tmp_path / "page.pdf", "/usr/bin/chromium", launcher=launcher)

    (copy_content,) = launcher.copy_contents
    assert (
        copy_content.count(
            "window.Chart && (Chart.defaults.animation = false)"
        )
        == 1
    )
    assert html.read_text() == original


def test_render_pdf_scriptless_page_prints_unmodified_copy(tmp_path):
    html = tmp_path / "page.html"
    original = "<html><body><p>no scripts here</p></body></html>"
    html.write_text(original)
    launcher = FakeLauncher(FakeProcess([0]))

    render_pdf(html, tmp_path / "page.pdf", "/usr/bin/chromium", launcher=launcher)

    (copy_content,) = launcher.copy_contents
    assert copy_content == original


def test_render_pdf_cleans_up_print_copy(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    launcher = FakeLauncher(FakeProcess([0]))

    render_pdf(html, tmp_path / "page.pdf", "/usr/bin/chromium", launcher=launcher)

    assert list(tmp_path.glob(".jimemo-pdf-*")) == []


def test_render_pdf_kills_lingering_browser_once_pdf_is_stable(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    process = FakeProcess([None] * 50)
    launcher = FakeLauncher(process)

    render_pdf(html, tmp_path / "page.pdf", "/usr/bin/chromium", launcher=launcher)

    assert process.killed is True


def test_render_pdf_timeout_with_no_pdf_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("jimemo.pdf.TIMEOUT_SECONDS", 0.6)
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    process = FakeProcess([None] * 50)
    launcher = FakeLauncher(process, pdf_bytes=None)

    with pytest.raises(PdfError) as exc_info:
        render_pdf(html, tmp_path / "page.pdf", "/usr/bin/chromium", launcher=launcher)
    assert "no PDF within" in str(exc_info.value)
    assert process.killed is True


def test_render_pdf_nonzero_exit_raises_with_log_tail(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")

    class LoggingLauncher(FakeLauncher):
        def __call__(self, argv, log_handle):
            log_handle.write("something broke\n")
            return super().__call__(argv, log_handle)

    launcher = LoggingLauncher(FakeProcess([21]), pdf_bytes=None)

    with pytest.raises(PdfError) as exc_info:
        render_pdf(html, tmp_path / "page.pdf", "/usr/bin/chromium", launcher=launcher)
    assert "21" in str(exc_info.value)
    assert "something broke" in str(exc_info.value)


def test_render_pdf_exit_zero_but_no_pdf_raises(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    launcher = FakeLauncher(FakeProcess([0]), pdf_bytes=None)

    with pytest.raises(PdfError) as exc_info:
        render_pdf(html, tmp_path / "page.pdf", "/usr/bin/chromium", launcher=launcher)
    assert "no PDF" in str(exc_info.value)


def test_render_pdf_unrunnable_browser_raises(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")

    def missing_launcher(argv, log_handle):
        raise FileNotFoundError("no such file")

    with pytest.raises(PdfError) as exc_info:
        render_pdf(html, tmp_path / "page.pdf", "/gone/chrome", launcher=missing_launcher)
    assert "/gone/chrome" in str(exc_info.value)
    assert list(tmp_path.glob(".jimemo-pdf-*")) == []


def test_render_pdf_launch_permission_error_wraps_as_pdf_error(tmp_path):
    """A configured browser path that is actually a directory (e.g. the
    .app bundle itself instead of its Contents/MacOS binary) passes
    find_browser's exists() check, then Popen raises PermissionError --
    an OSError subclass, not FileNotFoundError. That must still surface
    as a domain PdfError, not a raw traceback."""
    html = tmp_path / "page.html"
    html.write_text("<html></html>")

    def denied_launcher(argv, log_handle):
        raise PermissionError(13, "Permission denied")

    with pytest.raises(PdfError) as exc_info:
        render_pdf(
            html,
            tmp_path / "page.pdf",
            "/Applications/Google Chrome.app",
            launcher=denied_launcher,
        )
    assert "/Applications/Google Chrome.app" in str(exc_info.value)


class DelayedWriteFakeLauncher(FakeLauncher):
    """Like FakeLauncher, but the PDF bytes are written only once
    ``proc.poll()`` has been called ``after`` times -- modeling a real
    browser that takes several wall-clock seconds to print, so a
    pre-existing stale file already at the poll target sits stable
    through the first couple of polls before the new content lands."""

    def __init__(self, process, pdf_bytes, after):
        super().__init__(process, pdf_bytes=None)
        self._delayed_bytes = pdf_bytes
        self._after = after

    def __call__(self, argv, log_handle):
        proc = super().__call__(argv, log_handle)
        target_arg = next(a for a in argv if a.startswith("--print-to-pdf="))
        target_path = Path(target_arg.split("=", 1)[1])
        real_poll = proc.poll
        count = {"n": 0}

        def poll():
            count["n"] += 1
            if count["n"] == self._after:
                target_path.write_bytes(self._delayed_bytes)
            return real_poll()

        proc.poll = poll
        return proc


def test_render_pdf_ignores_a_preexisting_stale_file_at_pdf_path(tmp_path):
    """Deterministic on a second `jimemo pdf draft.html` at the same
    default output path: a previous run's PDF already sits at pdf_path.
    Its size is stable from the very first poll, so if the poll loop
    watched pdf_path directly it would declare success (and kill the
    browser) before this run ever printed, returning the old bytes.
    render_pdf must poll a pid-unique temp target instead -- which
    cannot pre-exist -- and atomically replace pdf_path only once that
    temp target is confirmed done."""
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    pdf = tmp_path / "page.pdf"
    pdf.write_bytes(b"OLD PDF BYTES")
    process = FakeProcess([None] * 50)
    launcher = DelayedWriteFakeLauncher(process, b"%PDF-1.4 new", after=3)

    render_pdf(html, pdf, "/usr/bin/chromium", launcher=launcher)

    assert pdf.read_bytes() == b"%PDF-1.4 new"
    assert list(tmp_path.glob(".jimemo-pdf-out-*")) == []


def test_render_pdf_creates_output_parent_dir(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    pdf = tmp_path / "sub" / "dir" / "page.pdf"
    launcher = FakeLauncher(FakeProcess([0]))

    render_pdf(html, pdf, "/usr/bin/chromium", launcher=launcher)

    assert pdf.is_file()
