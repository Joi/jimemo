import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.config import load_config
from jimemo.errors import PublishError
from jimemo.publish.setup import (
    CLOUDFLARE_ASSETS_DIR,
    DEFAULT_PROJECT_NAME,
    KV_BINDING_NAME,
    SetupIO,
    TOKEN_MISSING_MESSAGE,
    run_setup,
)
from jimemo.publish.wrangler import NO_WRANGLER_MESSAGE, MockWrangler

MIDDLEWARE_SRC = (
    Path(__file__).resolve().parents[1] / "publish" / "cloudflare" / "_middleware.js"
).read_text(encoding="utf-8")


class FakeIO(SetupIO):
    """Records everything printed; serves scripted answers to prompt()/
    confirm() in call order, so a test can assert both the wizard's
    narration and (via a wrangler double) its side effects."""

    def __init__(self, prompts=None, confirms=None):
        self.printed = []
        self.prompt_calls = []
        self.confirm_calls = []
        self._prompts = list(prompts or [])
        self._confirms = list(confirms or [])

    def print(self, message: str = "") -> None:
        self.printed.append(message)

    def prompt(self, message: str, default=None) -> str:
        self.prompt_calls.append((message, default))
        if not self._prompts:
            raise AssertionError(f"prompt() called with no scripted answer left: {message!r}")
        return self._prompts.pop(0)

    def confirm(self, message: str, default: bool = False) -> bool:
        self.confirm_calls.append((message, default))
        if not self._confirms:
            raise AssertionError(f"confirm() called with no scripted answer left: {message!r}")
        return self._confirms.pop(0)

    def text(self) -> str:
        return "\n".join(self.printed)


class UnavailableWrangler(MockWrangler):
    def check_available(self):
        self.calls.append(("check_available",))
        return False


class BrokenKVWrangler(MockWrangler):
    """Simulates a KV namespace that silently discards writes (e.g. a
    typo'd namespace id, or a token missing the KV scope): kv_get never
    reflects what kv_put just wrote."""

    def kv_get(self, namespace_id, key):
        self.calls.append(("kv_get", namespace_id, key))
        return ""


# ---------------------------------------------------------------------------
# --dry-run: fully offline, no prompts, no wrangler execution, no config.
# ---------------------------------------------------------------------------

def test_dry_run_never_prompts_or_confirms(tmp_path):
    io = FakeIO()  # no scripted answers -- prompt()/confirm() would raise
    wrangler = MockWrangler()

    run_setup(True, wrangler, tmp_path / "config.toml", io)

    assert io.prompt_calls == []
    assert io.confirm_calls == []


def test_dry_run_needs_no_token(monkeypatch, tmp_path):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    io = FakeIO()

    run_setup(True, MockWrangler(), tmp_path / "config.toml", io)  # must not raise


def test_dry_run_executes_no_wrangler_calls(tmp_path):
    io = FakeIO()
    wrangler = MockWrangler()

    run_setup(True, wrangler, tmp_path / "config.toml", io)

    assert wrangler.calls == []


def test_dry_run_writes_no_config(tmp_path):
    io = FakeIO()
    cfg_path = tmp_path / "config.toml"

    run_setup(True, MockWrangler(), cfg_path, io)

    assert not cfg_path.exists()


def test_dry_run_prints_the_deploy_argv(tmp_path):
    io = FakeIO()

    run_setup(True, MockWrangler(), tmp_path / "config.toml", io)

    text = io.text()
    assert (
        f"npx wrangler pages deploy {CLOUDFLARE_ASSETS_DIR} "
        f"--project-name {DEFAULT_PROJECT_NAME} --branch main"
    ) in text


def test_dry_run_prints_the_kv_check_argv(tmp_path):
    io = FakeIO()

    run_setup(True, MockWrangler(), tmp_path / "config.toml", io)

    text = io.text()
    assert "npx wrangler kv key put __jimemo_setup_check__ ok" in text
    assert "npx wrangler kv key get __jimemo_setup_check__" in text


def test_dry_run_kv_binding_step_names_tombstones(tmp_path):
    io = FakeIO()

    run_setup(True, MockWrangler(), tmp_path / "config.toml", io)

    text = io.text()
    assert KV_BINDING_NAME == "TOMBSTONES"
    assert "binding name TOMBSTONES" in text
    assert "env.TOMBSTONES" in text


def test_dry_run_uses_pages_dev_base_url_for_default_project(tmp_path):
    io = FakeIO()

    run_setup(True, MockWrangler(), tmp_path / "config.toml", io)

    assert f"https://{DEFAULT_PROJECT_NAME}.pages.dev" in io.text()


# ---------------------------------------------------------------------------
# Real-ish run (MockWrangler + FakeIO): the actual provisioning path.
# ---------------------------------------------------------------------------

def _run_real(tmp_path, monkeypatch, wrangler=None, prompts=None, confirms=None,
              token="fake-token"):
    if token is not None:
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", token)
    else:
        monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    wrangler = wrangler if wrangler is not None else MockWrangler()
    io = FakeIO(
        prompts=prompts if prompts is not None else ["friend-notes", "acct123", "ns123"],
        confirms=confirms,
    )
    cfg_path = tmp_path / "config.toml"
    run_setup(False, wrangler, cfg_path, io)
    return wrangler, io, cfg_path


def test_real_run_creates_kv_and_deploys_in_expected_order(tmp_path, monkeypatch):
    wrangler, io, cfg_path = _run_real(tmp_path, monkeypatch)

    assert [c[0] for c in wrangler.calls] == [
        "check_available", "pages_deploy", "kv_put", "kv_get",
    ]


def test_real_run_deploys_the_bundled_cloudflare_assets(tmp_path, monkeypatch):
    wrangler, io, cfg_path = _run_real(tmp_path, monkeypatch)

    deploy_call = next(c for c in wrangler.calls if c[0] == "pages_deploy")
    assert deploy_call == ("pages_deploy", "friend-notes", str(CLOUDFLARE_ASSETS_DIR), "main")


def test_real_run_post_deploy_check_uses_the_supplied_kv_namespace(tmp_path, monkeypatch):
    wrangler, io, cfg_path = _run_real(tmp_path, monkeypatch)

    kv_calls = [c for c in wrangler.calls if c[0] in ("kv_put", "kv_get")]
    assert kv_calls == [
        ("kv_put", "ns123", "__jimemo_setup_check__", "ok"),
        ("kv_get", "ns123", "__jimemo_setup_check__"),
    ]


def test_real_run_writes_correct_config_with_no_token(tmp_path, monkeypatch):
    wrangler, io, cfg_path = _run_real(tmp_path, monkeypatch)

    config = load_config(cfg_path)
    assert config.publish.backend == "cloudflare"
    cf = config.publish.cloudflare
    assert cf.project == "friend-notes"
    assert cf.account_id == "acct123"
    assert cf.kv_namespace_id == "ns123"
    assert cf.base_url == "https://friend-notes.pages.dev"

    assert "fake-token" not in cfg_path.read_text()
    assert "token" not in cfg_path.read_text().lower()


def test_real_run_prompts_project_name_with_default(tmp_path, monkeypatch):
    wrangler, io, cfg_path = _run_real(tmp_path, monkeypatch)

    project_prompt = io.prompt_calls[0]
    assert project_prompt == ("Cloudflare Pages project name", DEFAULT_PROJECT_NAME)


def test_real_run_accepts_the_default_project_name(tmp_path, monkeypatch):
    # An empty string simulates the user hitting enter to accept whatever
    # FakeIO would normally return as the "no input" case; here we just
    # supply the default explicitly since FakeIO always returns its
    # scripted value verbatim (unlike RealIO, which substitutes the
    # default for an empty response).
    wrangler, io, cfg_path = _run_real(
        tmp_path, monkeypatch, prompts=[DEFAULT_PROJECT_NAME, "acct123", "ns123"],
    )
    config = load_config(cfg_path)
    assert config.publish.cloudflare.project == DEFAULT_PROJECT_NAME


def test_module_only_checks_token_presence_never_reads_its_value():
    """setup.py may check *presence* of CLOUDFLARE_API_TOKEN (to fail
    fast with a clear error) but must never capture or reuse its actual
    value: the only os.environ access in the module is the one
    presence-check call, used solely for its truthiness."""
    import jimemo.publish.setup as mod

    src = Path(mod.__file__).read_text()
    environ_lines = [
        line.strip() for line in src.splitlines() if "os.environ" in line
    ]
    assert environ_lines == ['elif not os.environ.get("CLOUDFLARE_API_TOKEN"):']


def test_real_run_never_writes_the_token_to_config(tmp_path, monkeypatch):
    wrangler, io, cfg_path = _run_real(tmp_path, monkeypatch, token="super-secret-token-value")

    assert "super-secret-token-value" not in cfg_path.read_text()
    assert "super-secret-token-value" not in io.text()


# ---------------------------------------------------------------------------
# Missing CLOUDFLARE_API_TOKEN (non-dry-run): clear error, no side effects.
# ---------------------------------------------------------------------------

def test_missing_token_raises_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    io = FakeIO()
    wrangler = MockWrangler()

    with pytest.raises(PublishError) as exc:
        run_setup(False, wrangler, tmp_path / "config.toml", io)

    assert str(exc.value) == TOKEN_MISSING_MESSAGE
    assert "CLOUDFLARE_API_TOKEN" in str(exc.value)


def test_missing_token_makes_no_wrangler_calls(tmp_path, monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    wrangler = MockWrangler()

    with pytest.raises(PublishError):
        run_setup(False, wrangler, tmp_path / "config.toml", FakeIO())

    assert wrangler.calls == []


def test_missing_token_writes_no_config(tmp_path, monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    cfg_path = tmp_path / "config.toml"

    with pytest.raises(PublishError):
        run_setup(False, MockWrangler(), cfg_path, FakeIO())

    assert not cfg_path.exists()


# ---------------------------------------------------------------------------
# Missing npx/wrangler (non-dry-run): clear error, no side effects.
# ---------------------------------------------------------------------------

def test_missing_wrangler_raises_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "fake-token")
    wrangler = UnavailableWrangler()

    with pytest.raises(PublishError) as exc:
        run_setup(False, wrangler, tmp_path / "config.toml", FakeIO())

    assert str(exc.value) == NO_WRANGLER_MESSAGE


def test_missing_wrangler_writes_no_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "fake-token")
    cfg_path = tmp_path / "config.toml"

    with pytest.raises(PublishError):
        run_setup(False, UnavailableWrangler(), cfg_path, FakeIO())

    assert not cfg_path.exists()


# ---------------------------------------------------------------------------
# Post-deploy binding check: warns loudly on a simulated broken binding.
# ---------------------------------------------------------------------------

def test_post_deploy_check_warns_on_broken_kv_roundtrip(tmp_path, monkeypatch):
    wrangler, io, cfg_path = _run_real(tmp_path, monkeypatch, wrangler=BrokenKVWrangler())

    text = io.text()
    assert "WARNING" in text
    assert "namespace id" in text.lower() or "kv scope" in text.lower()


def test_post_deploy_check_still_writes_config_after_a_warning(tmp_path, monkeypatch):
    # A failed best-effort check is a loud warning, not a hard stop --
    # the human may still want the config written so they can go fix the
    # binding and try `jimemo publish` afterward without rerunning setup.
    wrangler, io, cfg_path = _run_real(tmp_path, monkeypatch, wrangler=BrokenKVWrangler())

    assert cfg_path.is_file()


def test_post_deploy_check_ok_path_notes_binding_still_unverified(tmp_path, monkeypatch):
    wrangler, io, cfg_path = _run_real(tmp_path, monkeypatch)

    text = io.text()
    assert "does not yet prove" in text
    assert "env.TOMBSTONES" in text


# ---------------------------------------------------------------------------
# Existing config: confirm before overwriting.
# ---------------------------------------------------------------------------

def test_existing_config_prompts_before_overwrite(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[publish]\nbackend = "command"\ncommand = "notes-publish"\n')

    wrangler, io, _ = _run_real(tmp_path, monkeypatch, confirms=[True])

    assert io.confirm_calls
    config = load_config(cfg_path)
    assert config.publish.backend == "cloudflare"


def test_existing_config_declining_overwrite_leaves_it_untouched(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    original = '[publish]\nbackend = "command"\ncommand = "notes-publish"\n'
    cfg_path.write_text(original)

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "fake-token")
    io = FakeIO(prompts=["friend-notes", "acct123", "ns123"], confirms=[False])
    run_setup(False, MockWrangler(), cfg_path, io)

    assert cfg_path.read_text() == original
    assert "aborted" in io.text().lower()


# ---------------------------------------------------------------------------
# Cross-check against the shipped middleware (Task 3): the binding name
# this wizard tells a friend to use must match what the deployed Worker
# actually reads.
# ---------------------------------------------------------------------------

def test_kv_binding_name_matches_middleware():
    assert f"env.{KV_BINDING_NAME}" in MIDDLEWARE_SRC


def test_cloudflare_assets_dir_is_the_bundled_middleware_directory():
    assert CLOUDFLARE_ASSETS_DIR.is_dir()
    assert (CLOUDFLARE_ASSETS_DIR / "_middleware.js").is_file()
    assert (CLOUDFLARE_ASSETS_DIR / "_headers").is_file()
    assert (CLOUDFLARE_ASSETS_DIR / "index.html").is_file()
