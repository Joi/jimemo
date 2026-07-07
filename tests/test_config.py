import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.config import CloudflareConfig, Config, PdfConfig, PublishConfig, config_path, load_config
from jimemo.errors import ConfigError


def test_valid_command_config_loads(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[publish]\n'
        'backend = "command"\n'
        'command = "notes-publish"\n'
    )
    config = load_config(cfg_file)
    assert config.publish.backend == "command"
    assert config.publish.command == "notes-publish"
    assert config.publish.cloudflare is None


def test_valid_cloudflare_config_loads(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[publish]\n'
        'backend = "cloudflare"\n'
        '\n'
        '[publish.cloudflare]\n'
        'project = "friend-notes"\n'
        'account_id = "abc123"\n'
        'kv_namespace_id = "def456"\n'
        'base_url = "https://friend-notes.pages.dev"\n'
    )
    config = load_config(cfg_file)
    assert config.publish.backend == "cloudflare"
    assert config.publish.command is None
    assert config.publish.cloudflare == CloudflareConfig(
        project="friend-notes",
        account_id="abc123",
        kv_namespace_id="def456",
        base_url="https://friend-notes.pages.dev",
    )


def test_config_without_publish_section(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("# nothing configured yet\n")
    config = load_config(cfg_file)
    assert config == Config(publish=None)


def test_missing_config_file_raises_setup_hint(tmp_path):
    missing = tmp_path / "config.toml"
    with pytest.raises(ConfigError) as exc:
        load_config(missing)
    assert "jimemo publish setup" in str(exc.value)


def test_invalid_toml_raises_config_error(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("this is not [ valid toml\n")
    with pytest.raises(ConfigError):
        load_config(cfg_file)


def test_missing_backend_field_is_named(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[publish]\ncommand = "notes-publish"\n')
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_file)
    assert "backend" in str(exc.value)


def test_invalid_backend_value_is_named(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[publish]\nbackend = "ftp"\n')
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_file)
    msg = str(exc.value)
    assert "ftp" in msg
    assert "backend" in msg


def test_missing_command_field_is_named(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[publish]\nbackend = "command"\n')
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_file)
    msg = str(exc.value)
    assert "command" in msg
    assert "backend" in msg


def test_missing_cloudflare_section_is_named(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[publish]\nbackend = "cloudflare"\n')
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_file)
    msg = str(exc.value)
    assert "cloudflare" in msg
    assert "backend" in msg


def test_missing_cloudflare_field_is_named(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[publish]\n'
        'backend = "cloudflare"\n'
        '\n'
        '[publish.cloudflare]\n'
        'project = "friend-notes"\n'
        'account_id = "abc123"\n'
        'kv_namespace_id = "def456"\n'
        # base_url deliberately omitted
    )
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_file)
    msg = str(exc.value)
    assert "base_url" in msg
    assert "cloudflare" in msg


def test_config_path_uses_env_override(monkeypatch, tmp_path):
    override = tmp_path / "custom.toml"
    monkeypatch.setenv("JIMEMO_CONFIG", str(override))
    assert config_path() == override


def test_config_path_default_without_override(monkeypatch):
    monkeypatch.delenv("JIMEMO_CONFIG", raising=False)
    assert config_path() == Path.home() / ".jimemo" / "config.toml"


def test_load_config_respects_env_override(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[publish]\nbackend = "command"\ncommand = "notes-publish"\n')
    monkeypatch.setenv("JIMEMO_CONFIG", str(cfg_file))
    config = load_config()
    assert config.publish.backend == "command"


def test_publish_config_dataclass_defaults():
    # Config/PublishConfig are usable directly (not only via load_config) --
    # the seam and its tests build them this way to bypass file I/O.
    assert PublishConfig(backend="command").cloudflare is None
    assert PublishConfig(backend="command").command is None


# ---------------------------------------------------------------------------
# A hand-edited config.toml bypasses `jimemo publish setup`'s own input
# validation entirely -- load_config() is the only remaining gate. These
# mirror tests/test_setup.py's project-name rejections so a value the
# wizard would never accept can't sneak in by hand-editing the file instead.
# ---------------------------------------------------------------------------

def _cloudflare_config_text(project) -> str:
    project_line = (
        f'project = {project}\n' if not isinstance(project, str)
        else f'project = "{project}"\n'
    )
    return (
        '[publish]\n'
        'backend = "cloudflare"\n'
        '\n'
        '[publish.cloudflare]\n'
        f'{project_line}'
        'account_id = "abc123"\n'
        'kv_namespace_id = "def456"\n'
        'base_url = "https://friend-notes.pages.dev"\n'
    )


@pytest.mark.parametrize(
    "bad_project",
    ["../evil", "Has Spaces", "UPPERCASE", "-leading-hyphen", "trailing-hyphen-"],
)
def test_invalid_project_name_raises_at_load(tmp_path, bad_project):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(_cloudflare_config_text(bad_project))
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_file)
    assert "project" in str(exc.value)


def test_non_string_project_raises_at_load(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(_cloudflare_config_text(123))
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_file)
    assert "project" in str(exc.value)


def test_non_string_cloudflare_field_raises_at_load(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[publish]\n'
        'backend = "cloudflare"\n'
        '\n'
        '[publish.cloudflare]\n'
        'project = "friend-notes"\n'
        'account_id = 123\n'
        'kv_namespace_id = "def456"\n'
        'base_url = "https://friend-notes.pages.dev"\n'
    )
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_file)
    assert "account_id" in str(exc.value)


def test_non_table_publish_section_raises_config_error(tmp_path):
    """A hand-edited config.toml can write `publish = "cloudflare"` (a bare
    key-value, not a `[publish]` table) -- data.get("backend") on a plain
    string would otherwise blow up with an unrelated AttributeError deep
    inside _parse_publish instead of a clear ConfigError here."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('publish = "cloudflare"\n')
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_file)
    assert "publish" in str(exc.value)


@pytest.mark.parametrize(
    "command_line",
    ['command = ["notes-publish"]', "command = 5", 'command = ""'],
)
def test_invalid_command_type_or_empty_raises_config_error(tmp_path, command_line):
    # A list is truthy, so a hand-edited command = ["notes-publish"] used
    # to pass load_config()'s old `if not command` check and only
    # TypeError later, deep inside the command backend's subprocess call.
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'[publish]\nbackend = "command"\n{command_line}\n')
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_file)
    msg = str(exc.value)
    assert "command" in msg
    assert "backend" in msg


def test_non_url_base_url_raises_at_load(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[publish]\n'
        'backend = "cloudflare"\n'
        '\n'
        '[publish.cloudflare]\n'
        'project = "friend-notes"\n'
        'account_id = "abc123"\n'
        'kv_namespace_id = "def456"\n'
        'base_url = "ftp://friend-notes.pages.dev"\n'
    )
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_file)
    assert "base_url" in str(exc.value)


def test_pdf_table_parses_browser(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[pdf]\nbrowser = "/usr/bin/chromium"\n')
    cfg = load_config(cfg_file)
    assert cfg.pdf is not None
    assert cfg.pdf.browser == "/usr/bin/chromium"


def test_pdf_table_absent_means_none(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[publish]\nbackend = "command"\ncommand = "notes-publish"\n')
    cfg = load_config(cfg_file)
    assert cfg.pdf is None


def test_pdf_table_without_browser_key_is_valid(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[pdf]\n")
    cfg = load_config(cfg_file)
    assert cfg.pdf is not None
    assert cfg.pdf.browser is None


def test_pdf_not_a_table_errors(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('pdf = "yes"\n')
    with pytest.raises(ConfigError) as exc_info:
        load_config(cfg_file)
    assert "[pdf]" in str(exc_info.value)


def test_pdf_browser_not_a_string_errors(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[pdf]\nbrowser = 3\n")
    with pytest.raises(ConfigError) as exc_info:
        load_config(cfg_file)
    assert "browser" in str(exc_info.value)
