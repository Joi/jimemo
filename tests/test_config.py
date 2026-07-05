import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.config import CloudflareConfig, Config, PublishConfig, config_path, load_config
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
