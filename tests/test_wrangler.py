import json
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.errors import PublishError
from jimemo.publish.wrangler import NO_WRANGLER_MESSAGE, MockWrangler, Wrangler


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


class RaisingRunner:
    """Simulates `npx` itself being absent from PATH."""

    def __call__(self, argv):
        raise FileNotFoundError("npx: command not found")


def test_pages_deploy_builds_expected_argv(tmp_path):
    runner = FakeRunner(CompletedProcess([], 0, stdout="Deployment complete!\n", stderr=""))
    w = Wrangler(runner=runner)

    w.pages_deploy("my-project", tmp_path)

    assert runner.calls == [
        ["npx", "wrangler", "pages", "deploy", str(tmp_path),
         "--project-name", "my-project", "--branch", "main"]
    ]


def test_pages_deploy_accepts_a_different_branch(tmp_path):
    runner = FakeRunner(CompletedProcess([], 0, stdout="", stderr=""))
    w = Wrangler(runner=runner)

    w.pages_deploy("my-project", tmp_path, branch="preview")

    assert runner.calls == [
        ["npx", "wrangler", "pages", "deploy", str(tmp_path),
         "--project-name", "my-project", "--branch", "preview"]
    ]


def test_kv_put_builds_expected_argv():
    runner = FakeRunner(CompletedProcess([], 0, stdout="", stderr=""))
    w = Wrangler(runner=runner)

    w.kv_put("ns123", "abc123", "2026-07-05T00:00:00.000Z")

    assert runner.calls == [
        ["npx", "wrangler", "kv", "key", "put", "abc123", "2026-07-05T00:00:00.000Z",
         "--namespace-id", "ns123", "--remote"]
    ]


def test_kv_get_builds_expected_argv_and_returns_stripped_value():
    runner = FakeRunner(CompletedProcess([], 0, stdout="2026-07-05T00:00:00.000Z\n", stderr=""))
    w = Wrangler(runner=runner)

    value = w.kv_get("ns123", "abc123")

    assert runner.calls == [
        ["npx", "wrangler", "kv", "key", "get", "abc123",
         "--namespace-id", "ns123", "--text", "--remote"]
    ]
    assert value == "2026-07-05T00:00:00.000Z"


def test_kv_list_builds_expected_argv_and_parses_json():
    stdout = json.dumps([{"name": "abc123"}, {"name": "def456"}])
    runner = FakeRunner(CompletedProcess([], 0, stdout=stdout, stderr=""))
    w = Wrangler(runner=runner)

    entries = w.kv_list("ns123")

    assert runner.calls == [
        ["npx", "wrangler", "kv", "key", "list", "--namespace-id", "ns123", "--remote"]
    ]
    assert entries == [{"name": "abc123"}, {"name": "def456"}]


def test_kv_list_malformed_json_raises_publish_error():
    runner = FakeRunner(CompletedProcess([], 0, stdout="not json", stderr=""))
    w = Wrangler(runner=runner)

    with pytest.raises(PublishError) as exc:
        w.kv_list("ns123")
    assert "kv key list" in str(exc.value)


def test_nonzero_exit_raises_publish_error_with_action_and_stderr():
    runner = FakeRunner(CompletedProcess([], 3, stdout="", stderr="boom: not authorized"))
    w = Wrangler(runner=runner)

    with pytest.raises(PublishError) as exc:
        w.pages_deploy("p", "/tmp/whatever")
    msg = str(exc.value)
    assert "pages deploy" in msg
    assert "boom: not authorized" in msg


def test_missing_npx_raises_clear_error_from_every_method(tmp_path):
    w = Wrangler(runner=RaisingRunner())

    for call in (
        lambda: w.pages_deploy("p", tmp_path),
        lambda: w.kv_put("ns", "abc123", "ts"),
        lambda: w.kv_get("ns", "abc123"),
        lambda: w.kv_list("ns"),
    ):
        with pytest.raises(PublishError) as exc:
            call()
        assert str(exc.value) == NO_WRANGLER_MESSAGE


def test_check_available_true_when_npx_resolves(monkeypatch):
    import jimemo.publish.wrangler as mod

    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/npx")
    assert Wrangler().check_available() is True


def test_check_available_false_when_npx_missing(monkeypatch):
    import jimemo.publish.wrangler as mod

    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    assert Wrangler().check_available() is False


def test_default_runner_is_real_subprocess_argv_no_shell():
    import jimemo.publish.wrangler as mod

    result = mod._run([sys.executable, "-c", "print('hello from subprocess')"])
    assert result.returncode == 0
    assert "hello from subprocess" in result.stdout


def test_mock_wrangler_records_calls_and_round_trips_kv(tmp_path):
    mock = MockWrangler()

    mock.pages_deploy("proj", tmp_path)
    mock.kv_put("ns", "abc123", "2026-07-05T00:00:00.000Z")
    value = mock.kv_get("ns", "abc123")
    entries = mock.kv_list("ns")

    assert value == "2026-07-05T00:00:00.000Z"
    assert entries == [{"name": "abc123"}]
    assert ("pages_deploy", "proj", str(tmp_path), "main") in mock.calls
    assert ("kv_put", "ns", "abc123", "2026-07-05T00:00:00.000Z") in mock.calls
    assert mock.check_available() is True


def test_mock_wrangler_kv_get_unknown_key_returns_empty_string():
    mock = MockWrangler()
    assert mock.kv_get("ns", "never-written") == ""


def test_module_never_reads_or_logs_cf_token():
    """jimemo must never touch the Cloudflare API token itself -- wrangler
    resolves its own auth from the environment/its credential store. This
    greps the actual source for env access (the module has no legitimate
    reason to import `os` or touch `os.environ` at all) so the invariant
    can't silently regress. (CLOUDFLARE_API_TOKEN itself is named in a
    docstring, purely as documentation of wrangler's own behavior.)"""
    import jimemo.publish.wrangler as mod

    src = Path(mod.__file__).read_text()
    assert "import os" not in src
    assert "os.environ" not in src
    assert "getenv" not in src
