import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.config import PublishConfig
from jimemo.errors import PublishError
from jimemo.publish.command_backend import CommandPublisher


class FakeRunner:
    """Records every argv it's called with and returns a scripted result
    (or the next of several, in order) instead of touching a real
    subprocess."""

    def __init__(self, *results):
        self.calls = []
        self._results = list(results)

    def __call__(self, argv):
        self.calls.append(argv)
        if len(self._results) == 1:
            return self._results[0]
        return self._results.pop(0)


def _config(command="notes-publish"):
    return PublishConfig(backend="command", command=command)


def test_publish_runs_expected_argv_and_parses_url(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    runner = FakeRunner(CompletedProcess([], 0, stdout="https://notes.ito.com/abc123/\n", stderr=""))

    publisher = CommandPublisher(_config(), runner=runner)
    url = publisher.publish(html)

    assert url == "https://notes.ito.com/abc123/"
    assert runner.calls == [["notes-publish", str(html)]]


def test_publish_passes_title_flag(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    runner = FakeRunner(CompletedProcess([], 0, stdout="https://notes.ito.com/abc123/\n", stderr=""))

    publisher = CommandPublisher(_config(), runner=runner)
    publisher.publish(html, title="Q3 Briefing")

    assert runner.calls == [["notes-publish", str(html), "--title", "Q3 Briefing"]]


def test_publish_parses_url_from_multiline_stdout_with_trailing_whitespace(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    stdout = "staged: public/abc123/ (theme: visual)\n\nhttps://notes.ito.com/abc123/  \n\n"
    runner = FakeRunner(CompletedProcess([], 0, stdout=stdout, stderr=""))

    publisher = CommandPublisher(_config(), runner=runner)
    url = publisher.publish(html)

    assert url == "https://notes.ito.com/abc123/"


def test_publish_non_url_last_line_raises_publish_error(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    runner = FakeRunner(CompletedProcess([], 0, stdout="not a url\n", stderr=""))

    publisher = CommandPublisher(_config(), runner=runner)
    with pytest.raises(PublishError) as exc:
        publisher.publish(html)
    assert "not a url" in str(exc.value)


def test_publish_empty_stdout_raises_publish_error(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    runner = FakeRunner(CompletedProcess([], 0, stdout="   \n\n", stderr=""))

    publisher = CommandPublisher(_config(), runner=runner)
    with pytest.raises(PublishError):
        publisher.publish(html)


def test_publish_nonzero_exit_raises_publish_error_with_stderr(tmp_path):
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    runner = FakeRunner(CompletedProcess([], 1, stdout="", stderr="boom: disk full"))

    publisher = CommandPublisher(_config(), runner=runner)
    with pytest.raises(PublishError) as exc:
        publisher.publish(html)
    assert "boom: disk full" in str(exc.value)


def test_purge_runs_expected_argv():
    runner = FakeRunner(CompletedProcess([], 0, stdout="purged: abc123\n", stderr=""))
    publisher = CommandPublisher(_config(), runner=runner)

    publisher.purge("abc123")

    assert runner.calls == [["notes-publish", "purge", "abc123"]]


def test_purge_nonzero_exit_raises_publish_error():
    runner = FakeRunner(CompletedProcess([], 1, stdout="", stderr="not a valid hash"))
    publisher = CommandPublisher(_config(), runner=runner)

    with pytest.raises(PublishError) as exc:
        publisher.purge("garbage")
    assert "not a valid hash" in str(exc.value)


def test_list_runs_expected_argv_and_returns_lines():
    runner = FakeRunner(CompletedProcess([], 0, stdout="HASH  TITLE\nabc123  Note\n", stderr=""))
    publisher = CommandPublisher(_config(), runner=runner)

    entries = publisher.list()

    assert runner.calls == [["notes-publish", "list"]]
    assert entries == ["HASH  TITLE", "abc123  Note"]


def test_gc_runs_expected_argv():
    runner = FakeRunner(CompletedProcess([], 0, stdout="", stderr=""))
    publisher = CommandPublisher(_config(), runner=runner)

    publisher.gc()

    assert runner.calls == [["notes-publish", "gc"]]


def test_uses_configured_command_name():
    runner = FakeRunner(CompletedProcess([], 0, stdout="", stderr=""))
    publisher = CommandPublisher(_config(command="my-publish-cli"), runner=runner)

    publisher.gc()

    assert runner.calls == [["my-publish-cli", "gc"]]


def test_invoke_missing_command_raises_clean_publish_error(tmp_path):
    """A configured command that doesn't exist on PATH makes the real
    subprocess.run raise FileNotFoundError -- that must surface as a
    clean PublishError, not an uncaught traceback."""
    html = tmp_path / "page.html"
    html.write_text("<html></html>")
    publisher = CommandPublisher(_config(command="definitely-not-a-real-command-xyz"))

    with pytest.raises(PublishError) as exc:
        publisher.publish(html)

    assert "definitely-not-a-real-command-xyz" in str(exc.value)
    assert "not found" in str(exc.value).lower()


def test_default_runner_is_real_subprocess_argv_no_shell():
    import jimemo.publish.command_backend as mod

    result = mod._run([sys.executable, "-c", "print('hello from subprocess')"])
    assert result.returncode == 0
    assert "hello from subprocess" in result.stdout
