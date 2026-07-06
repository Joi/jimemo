import json
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.errors import PublishError
from jimemo.publish.wrangler import NO_WRANGLER_MESSAGE, MockWrangler, Wrangler


class FakeRunner:
    """Records every argv (and the env dict passed alongside it, if any)
    it's called with and returns a scripted result (or the next of
    several, in order) instead of touching a real subprocess."""

    def __init__(self, *results):
        self.calls = []
        self.envs = []
        self._results = list(results)

    def __call__(self, argv, env=None):
        self.calls.append(argv)
        self.envs.append(env)
        if len(self._results) == 1:
            return self._results[0]
        return self._results.pop(0)


class RaisingRunner:
    """Simulates `npx` itself being absent from PATH."""

    def __call__(self, argv, env=None):
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


def test_pages_project_names_builds_expected_argv_and_parses_json():
    runner = FakeRunner(
        CompletedProcess(
            [],
            0,
            stdout=json.dumps([{"name": "one"}, {"name": "two"}]),
            stderr="",
        )
    )
    w = Wrangler(runner=runner)

    names = w.pages_project_names()

    assert runner.calls == [
        ["npx", "wrangler", "pages", "project", "list", "--json"]
    ]
    assert names == ["one", "two"]


def test_pages_project_names_accepts_wrapped_result_shape():
    runner = FakeRunner(
        CompletedProcess(
            [],
            0,
            stdout=json.dumps({"result": [{"name": "wrapped"}]}),
            stderr="",
        )
    )
    w = Wrangler(runner=runner)

    assert w.pages_project_names() == ["wrapped"]


def test_pages_project_names_malformed_json_raises_publish_error():
    runner = FakeRunner(CompletedProcess([], 0, stdout="not json", stderr=""))
    w = Wrangler(runner=runner)

    with pytest.raises(PublishError) as exc:
        w.pages_project_names()
    assert "pages project list" in str(exc.value)


def test_pages_project_create_builds_expected_argv():
    runner = FakeRunner(CompletedProcess([], 0, stdout="", stderr=""))
    w = Wrangler(runner=runner)

    w.pages_project_create("my-project")

    assert runner.calls == [
        [
            "npx", "wrangler", "pages", "project", "create", "my-project",
            "--production-branch", "main",
        ]
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
        lambda: w.pages_project_names(),
        lambda: w.pages_project_create("p"),
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

    assert mock.pages_project_names() == []
    mock.pages_project_create("proj")
    assert mock.pages_project_names() == ["proj"]
    mock.pages_deploy("proj", tmp_path)
    mock.kv_put("ns", "abc123", "2026-07-05T00:00:00.000Z")
    value = mock.kv_get("ns", "abc123")
    entries = mock.kv_list("ns")

    assert value == "2026-07-05T00:00:00.000Z"
    assert entries == [{"name": "abc123"}]
    assert ("pages_project_names",) in mock.calls
    assert ("pages_project_create", "proj", "main") in mock.calls
    assert ("pages_deploy", "proj", str(tmp_path), "main") in mock.calls
    assert ("kv_put", "ns", "abc123", "2026-07-05T00:00:00.000Z") in mock.calls
    assert mock.check_available() is True


def test_mock_wrangler_kv_get_unknown_key_returns_empty_string():
    mock = MockWrangler()
    assert mock.kv_get("ns", "never-written") == ""


def test_account_id_threads_cloudflare_account_id_into_subprocess_env():
    runner = FakeRunner(CompletedProcess([], 0, stdout="", stderr=""))
    w = Wrangler(runner=runner, account_id="acct-123")

    w.kv_get("ns123", "abc123")

    assert len(runner.envs) == 1
    env = runner.envs[0]
    assert env is not None
    assert env["CLOUDFLARE_ACCOUNT_ID"] == "acct-123"


def test_account_id_env_is_inherited_environment_plus_account_id(monkeypatch):
    monkeypatch.setenv("SOME_UNRELATED_VAR", "keep-me")
    runner = FakeRunner(CompletedProcess([], 0, stdout="", stderr=""))
    w = Wrangler(runner=runner, account_id="acct-123")

    w.kv_get("ns123", "abc123")

    env = runner.envs[0]
    assert env["SOME_UNRELATED_VAR"] == "keep-me"
    assert env["CLOUDFLARE_ACCOUNT_ID"] == "acct-123"


def test_no_account_id_passes_no_env_override():
    """Without an account_id, Wrangler must not touch the subprocess
    environment at all -- env stays None, i.e. subprocess.run's own
    default of inheriting the parent process environment unchanged."""
    runner = FakeRunner(CompletedProcess([], 0, stdout="", stderr=""))
    w = Wrangler(runner=runner)

    w.kv_get("ns123", "abc123")

    assert runner.envs == [None]


def test_account_id_settable_after_construction():
    """setup.py's wizard only learns the account id partway through --
    after the Wrangler is already constructed -- so account_id must be
    settable as a plain attribute, not fixed at __init__ time."""
    runner = FakeRunner(CompletedProcess([], 0, stdout="", stderr=""))
    w = Wrangler(runner=runner)

    w.account_id = "acct-456"
    w.kv_get("ns123", "abc123")

    assert runner.envs[0]["CLOUDFLARE_ACCOUNT_ID"] == "acct-456"


def test_account_id_never_carries_the_cf_api_token(monkeypatch):
    """account_id is a non-secret identifier threaded through the
    subprocess env; it must never carry the Cloudflare API token itself
    -- that stays wherever it already was, in the inherited environment,
    untouched and unread by this module."""
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "super-secret-token")
    runner = FakeRunner(CompletedProcess([], 0, stdout="", stderr=""))
    w = Wrangler(runner=runner, account_id="acct-123")

    w.kv_get("ns123", "abc123")

    env = runner.envs[0]
    # Inherited unchanged (subprocess would see it exactly as it exists
    # in this process's own environment) -- not read, copied, or altered
    # by Wrangler itself.
    assert env["CLOUDFLARE_API_TOKEN"] == "super-secret-token"


def test_module_never_reads_the_cf_api_token_value():
    """jimemo must never read the value of CLOUDFLARE_API_TOKEN --
    wrangler resolves its own auth directly, from its own subprocess
    environment (inherited unchanged) or its own credential store.
    Passing CLOUDFLARE_ACCOUNT_ID through the subprocess env for account
    scoping is fine and expected (a non-secret identifier) -- os /
    os.environ are now legitimately used for that -- but nothing in this
    module may ever look up the token's value."""
    import jimemo.publish.wrangler as mod

    src = Path(mod.__file__).read_text()
    for pattern in (
        'os.environ.get("CLOUDFLARE_API_TOKEN")',
        "os.environ.get('CLOUDFLARE_API_TOKEN')",
        'os.environ["CLOUDFLARE_API_TOKEN"]',
        "os.environ['CLOUDFLARE_API_TOKEN']",
        'os.getenv("CLOUDFLARE_API_TOKEN")',
        "os.getenv('CLOUDFLARE_API_TOKEN')",
    ):
        assert pattern not in src
