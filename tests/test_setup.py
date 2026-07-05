import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import jimemo.publish.cloudflare_backend as cloudflare_backend_mod
import jimemo.publish.setup as setup_mod
from jimemo.config import load_config, valid_project_name
from jimemo.errors import PublishError
from jimemo.publish.cloudflare_backend import CloudflarePublisher, _default_state_dir
from jimemo.publish.setup import (
    CLOUDFLARE_ASSETS_DIR,
    DEFAULT_PROJECT_NAME,
    KV_BINDING_NAME,
    SetupIO,
    TOKEN_MISSING_MESSAGE,
    run_setup,
)
from jimemo.publish.wrangler import NO_WRANGLER_MESSAGE, MockWrangler


def test_setup_validates_project_names_via_configs_shared_validator():
    # Both this wizard's input validation and load_config()'s
    # [publish.cloudflare] validation must enforce the exact same rule --
    # via the exact same function, not two regexes that could drift.
    assert setup_mod.valid_project_name is valid_project_name

MIDDLEWARE_SRC = (CLOUDFLARE_ASSETS_DIR / "_middleware.js").read_text(encoding="utf-8")


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


def _patch_home(monkeypatch, home_dir: Path) -> None:
    """run_setup() derives its state directory from cloudflare_backend's
    _default_state_dir(), which is Path.home()-based -- patch it (the
    same technique tests/test_cloudflare_backend.py already uses) so no
    test ever touches the real ~/.jimemo."""
    monkeypatch.setattr(
        cloudflare_backend_mod.Path, "home", classmethod(lambda cls: home_dir)
    )


# ---------------------------------------------------------------------------
# --dry-run: fully offline, no prompts, no wrangler execution, no config, no
# local filesystem writes.
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


def test_dry_run_creates_no_local_state_directory(monkeypatch, tmp_path):
    # dry-run must be fully offline: it prints what it WOULD install into
    # the state dir, but must never actually create it.
    _patch_home(monkeypatch, tmp_path)
    io = FakeIO()

    run_setup(True, MockWrangler(), tmp_path / "config.toml", io)

    state_dir = _default_state_dir(DEFAULT_PROJECT_NAME)
    assert not state_dir.exists()


def test_dry_run_prints_the_deploy_argv(tmp_path):
    io = FakeIO()
    state_dir = _default_state_dir(DEFAULT_PROJECT_NAME)

    run_setup(True, MockWrangler(), tmp_path / "config.toml", io)

    text = io.text()
    assert (
        f"npx wrangler pages deploy {state_dir} "
        f"--project-name {DEFAULT_PROJECT_NAME} --branch main"
    ) in text
    # And NOT the repo's own template dir -- that's the source it installs
    # FROM, never the thing it deploys.
    assert f"pages deploy {CLOUDFLARE_ASSETS_DIR} " not in text


def test_dry_run_prints_the_state_dir_asset_install_plan(tmp_path):
    io = FakeIO()
    state_dir = _default_state_dir(DEFAULT_PROJECT_NAME)

    run_setup(True, MockWrangler(), tmp_path / "config.toml", io)

    text = io.text()
    assert f"{state_dir}/functions/_middleware.js" in text
    assert f"{state_dir}/_headers" in text
    assert f"{state_dir}/index.html" in text


def test_dry_run_prints_single_machine_warning(tmp_path):
    io = FakeIO()

    run_setup(True, MockWrangler(), tmp_path / "config.toml", io)

    text = io.text()
    assert "single-machine limitation" in text.lower()
    assert "ONE machine per project" in text


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
# Path.home() is always patched to tmp_path, so the state directory this
# derives (~/.jimemo/cloudflare/<project>/) never touches the real home.
# ---------------------------------------------------------------------------

def _run_real(tmp_path, monkeypatch, wrangler=None, prompts=None, confirms=None,
              token="fake-token"):
    _patch_home(monkeypatch, tmp_path)
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


def test_real_run_deploys_the_local_state_dir_not_the_repo_template(tmp_path, monkeypatch):
    wrangler, io, cfg_path = _run_real(tmp_path, monkeypatch)

    expected_state_dir = _default_state_dir("friend-notes")
    deploy_call = next(c for c in wrangler.calls if c[0] == "pages_deploy")
    assert deploy_call == ("pages_deploy", "friend-notes", str(expected_state_dir), "main")
    assert deploy_call[2] != str(CLOUDFLARE_ASSETS_DIR)


def test_real_run_installs_middleware_into_state_dir_functions_subdir(tmp_path, monkeypatch):
    wrangler, io, cfg_path = _run_real(tmp_path, monkeypatch)

    state_dir = _default_state_dir("friend-notes")
    installed = state_dir / "functions" / "_middleware.js"
    assert installed.is_file()
    assert installed.read_text(encoding="utf-8") == MIDDLEWARE_SRC
    # NOT flattened at the state dir root -- Cloudflare only picks up
    # Functions from a functions/ directory.
    assert not (state_dir / "_middleware.js").exists()


def test_real_run_installs_headers_and_index_at_state_dir_root(tmp_path, monkeypatch):
    wrangler, io, cfg_path = _run_real(tmp_path, monkeypatch)

    state_dir = _default_state_dir("friend-notes")
    assert (state_dir / "_headers").read_text(encoding="utf-8") == (
        CLOUDFLARE_ASSETS_DIR / "_headers"
    ).read_text(encoding="utf-8")
    assert (state_dir / "index.html").read_text(encoding="utf-8") == (
        CLOUDFLARE_ASSETS_DIR / "index.html"
    ).read_text(encoding="utf-8")


def test_real_run_installs_assets_before_deploying(tmp_path, monkeypatch):
    # The deploy call must see the middleware already on disk -- order
    # matters, not just presence, since pages_deploy uploads whatever is
    # in the directory at the moment it's called.
    installed_before_deploy = []

    class RecordingWrangler(MockWrangler):
        def pages_deploy(self, project, directory, branch="main"):
            state_dir = _default_state_dir(project)
            installed_before_deploy.append(
                (state_dir / "functions" / "_middleware.js").is_file()
            )
            return super().pages_deploy(project, directory, branch)

    _run_real(tmp_path, monkeypatch, wrangler=RecordingWrangler())

    assert installed_before_deploy == [True]


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


def test_real_run_prints_single_machine_warning_with_state_dir_path(tmp_path, monkeypatch):
    wrangler, io, cfg_path = _run_real(tmp_path, monkeypatch)

    state_dir = _default_state_dir("friend-notes")
    text = io.text()
    assert "single-machine limitation" in text.lower()
    assert str(state_dir) in text


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
    _patch_home(monkeypatch, tmp_path)

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "fake-token")
    io = FakeIO(prompts=["friend-notes", "acct123", "ns123"], confirms=[False])
    run_setup(False, MockWrangler(), cfg_path, io)

    assert cfg_path.read_text() == original
    assert "aborted" in io.text().lower()


def test_existing_config_declining_overwrite_makes_no_side_effects(tmp_path, monkeypatch):
    """The overwrite confirm now happens near the START of run_setup (right
    after the token/wrangler-availability checks) -- BEFORE any asset
    install, deploy, or KV call -- not just before the final config write.
    Declining must be a total no-op against the friend's Cloudflare
    account: no deploy, no KV write, no local state directory, and the
    existing config left untouched."""
    cfg_path = tmp_path / "config.toml"
    original = '[publish]\nbackend = "command"\ncommand = "notes-publish"\n'
    cfg_path.write_text(original)
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "fake-token")
    wrangler = MockWrangler()
    io = FakeIO(prompts=["friend-notes", "acct123", "ns123"], confirms=[False])

    run_setup(False, wrangler, cfg_path, io)

    assert not any(
        c[0] in ("pages_deploy", "kv_put", "kv_get") for c in wrangler.calls
    )
    state_dir = _default_state_dir("friend-notes")
    assert not state_dir.exists()
    assert cfg_path.read_text() == original


# ---------------------------------------------------------------------------
# Project name validation: Cloudflare Pages project names are lowercase
# alphanumeric + hyphens. Rejected up front, before any side effect, since
# the name flows into a state-dir path join (a "../"-containing name would
# escape ~/.jimemo/cloudflare/), into base_url, and into a raw TOML write
# (an embedded newline would produce invalid TOML).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_name",
    ["../evil", "Has Spaces", "UPPERCASE", "-leading-hyphen",
     "trailing-hyphen-", "embedded\nnewline"],
)
def test_real_run_rejects_invalid_project_name(tmp_path, monkeypatch, bad_name):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "fake-token")
    wrangler = MockWrangler()
    io = FakeIO(prompts=[bad_name, "acct123", "ns123"])
    cfg_path = tmp_path / "config.toml"

    with pytest.raises(PublishError) as exc:
        run_setup(False, wrangler, cfg_path, io)

    assert "invalid Cloudflare Pages project name" in str(exc.value)
    assert not any(
        c[0] in ("pages_deploy", "kv_put", "kv_get") for c in wrangler.calls
    )
    assert not cfg_path.exists()


def test_real_run_accepts_a_valid_hyphenated_project_name(tmp_path, monkeypatch):
    wrangler, io, cfg_path = _run_real(
        tmp_path, monkeypatch, prompts=["a-valid-project-99", "acct123", "ns123"],
    )

    config = load_config(cfg_path)
    assert config.publish.cloudflare.project == "a-valid-project-99"


# ---------------------------------------------------------------------------
# Regression: setup installs the middleware into the state directory, and a
# later publish() (which always redeploys the WHOLE state directory -- see
# cloudflare_backend.py's module docstring) must not silently drop it.
# ---------------------------------------------------------------------------

def test_setup_then_publish_keeps_middleware_and_adds_new_hash(tmp_path, monkeypatch):
    wrangler, io, cfg_path = _run_real(tmp_path, monkeypatch)
    config = load_config(cfg_path)
    state_dir = _default_state_dir("friend-notes")

    html = tmp_path / "page.html"
    html.write_text("<html><body>hi</body></html>")
    publisher = CloudflarePublisher(config.publish, wrangler=wrangler, state_dir=state_dir)
    publisher.publish(html)

    assert (state_dir / "functions" / "_middleware.js").is_file()
    assert (state_dir / "_headers").is_file()
    assert (state_dir / "index.html").is_file()
    hash_dirs = [d for d in state_dir.iterdir() if d.is_dir() and d.name != "functions"]
    assert len(hash_dirs) == 1
    assert (hash_dirs[0] / "index.html").is_file()


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


# ---------------------------------------------------------------------------
# The completion message's example must actually work: `jimemo render`
# writes a file (via -o), it doesn't stream HTML on stdout, and `jimemo
# publish` takes a file path -- so the two commands can never be chained
# with a pipe, only run as two separate steps.
# ---------------------------------------------------------------------------

def test_completion_message_shows_a_working_render_then_publish_example(tmp_path, monkeypatch):
    wrangler, io, cfg_path = _run_real(tmp_path, monkeypatch)

    text = io.text()
    assert "jimemo render ... -o out.html" in text
    assert "jimemo publish out.html" in text
    assert "| jimemo publish" not in text
