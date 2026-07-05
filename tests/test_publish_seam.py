import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.config import CloudflareConfig, Config, PublishConfig
from jimemo.errors import PublishError
from jimemo.publish import Publisher, get_publisher


def test_publisher_is_abstract():
    with pytest.raises(TypeError):
        Publisher()


def test_not_configured_raises():
    config = Config(publish=None)
    with pytest.raises(PublishError) as exc:
        get_publisher(config)
    assert "publish setup" in str(exc.value) or "publish" in str(exc.value)


def test_unknown_backend_raises():
    config = Config(publish=PublishConfig(backend="ftp"))
    with pytest.raises(PublishError) as exc:
        get_publisher(config)
    msg = str(exc.value)
    assert "ftp" in msg
    assert "command" in msg
    assert "cloudflare" in msg


def test_registry_resolves_command_backend_by_name(monkeypatch):
    fake_mod = types.ModuleType("jimemo.publish.command_backend")

    class FakeCommandPublisher:
        def __init__(self, publish_config):
            self.publish_config = publish_config

    fake_mod.CommandPublisher = FakeCommandPublisher
    monkeypatch.setitem(sys.modules, "jimemo.publish.command_backend", fake_mod)

    config = Config(publish=PublishConfig(backend="command", command="notes-publish"))
    publisher = get_publisher(config)
    assert isinstance(publisher, FakeCommandPublisher)
    assert publisher.publish_config is config.publish


def test_registry_resolves_cloudflare_backend_by_name(monkeypatch):
    fake_mod = types.ModuleType("jimemo.publish.cloudflare_backend")

    class FakeCloudflarePublisher:
        def __init__(self, publish_config):
            self.publish_config = publish_config

    fake_mod.CloudflarePublisher = FakeCloudflarePublisher
    monkeypatch.setitem(sys.modules, "jimemo.publish.cloudflare_backend", fake_mod)

    cloudflare = CloudflareConfig(
        project="p", account_id="a", kv_namespace_id="k", base_url="https://p.pages.dev"
    )
    config = Config(publish=PublishConfig(backend="cloudflare", cloudflare=cloudflare))
    publisher = get_publisher(config)
    assert isinstance(publisher, FakeCloudflarePublisher)
    assert publisher.publish_config is config.publish


# Run in a subprocess: other test modules in this suite may already have
# imported jimemo.publish (or injected fake backend submodules into
# sys.modules) by the time this test body runs, which would make an
# in-process sys.modules check meaningless regardless of run order --
# same rationale as tests/test_cli.py's vendor-free import checks.

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")


def test_importing_publish_does_not_import_backends():
    script = (
        "import sys\n"
        f"sys.path.insert(0, {SRC_DIR!r})\n"
        "import jimemo.publish\n"
        "assert 'jimemo.publish.command_backend' not in sys.modules, sorted(sys.modules)\n"
        "assert 'jimemo.publish.cloudflare_backend' not in sys.modules, sorted(sys.modules)\n"
        "print('OK')\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout
