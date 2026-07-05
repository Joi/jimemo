"""Load ``~/.jimemo/config.toml`` -- the only config file jimemo's publish
subsystem reads.

Schema::

    [publish]
    backend = "command" | "cloudflare"
    command = "notes-publish"        # required when backend = "command"

    [publish.cloudflare]              # required when backend = "cloudflare"
    project = "..."
    account_id = "..."
    kv_namespace_id = "..."
    base_url = "https://<project>.pages.dev"

Parsed with the vendored ``tomli`` reader (see ``_vendor.py``): jimemo's
Python floor is 3.9, and the stdlib ``tomllib`` module only ships from
3.11 onward.

SECURITY: this file NEVER holds secrets. It stores only non-secret
identifiers -- a command name, or a Cloudflare project/account/KV-namespace
name and public base URL. No API tokens, no credentials. The `cloudflare`
backend's Wrangler seam reads its API token from the environment or
Wrangler's own credential store; jimemo must never write one into
config.toml.
"""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ._vendor import add_vendor_to_path
from .errors import ConfigError

_CLOUDFLARE_FIELDS = ("project", "account_id", "kv_namespace_id", "base_url")


@dataclass
class CloudflareConfig:
    project: str
    account_id: str
    kv_namespace_id: str
    base_url: str


@dataclass
class PublishConfig:
    backend: str
    command: Optional[str] = None
    cloudflare: Optional[CloudflareConfig] = None


@dataclass
class Config:
    publish: Optional[PublishConfig] = None


def config_path() -> Path:
    """Where load_config() reads from by default.

    Set JIMEMO_CONFIG to point at a different file (used by tests, and
    useful for trying an alternate config without touching the real one).
    """
    override = os.environ.get("JIMEMO_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".jimemo" / "config.toml"


def load_config(path: Optional[Path] = None) -> Config:
    """Load and validate ~/.jimemo/config.toml (or `path`, if given).

    Raises ConfigError if the file is missing, is not valid TOML, or is
    missing a field required by the selected publish backend.
    """
    cfg_path = path if path is not None else config_path()
    if not cfg_path.is_file():
        raise ConfigError(
            f"no config file at {cfg_path}; run `jimemo publish setup` to create one"
        )

    add_vendor_to_path()
    import tomli

    try:
        data = tomli.loads(cfg_path.read_text())
    except tomli.TOMLDecodeError as e:
        raise ConfigError(f"{cfg_path}: invalid TOML: {e}")

    publish_data = data.get("publish")
    publish = _parse_publish(publish_data, cfg_path) if publish_data is not None else None
    return Config(publish=publish)


def _parse_publish(data: Dict[str, Any], cfg_path: Path) -> PublishConfig:
    backend = data.get("backend")
    if not backend:
        raise ConfigError(
            f'{cfg_path}: [publish].backend is required ("command" or "cloudflare")'
        )
    if backend not in ("command", "cloudflare"):
        raise ConfigError(
            f'{cfg_path}: [publish].backend must be "command" or "cloudflare" '
            f"(got {backend!r})"
        )

    if backend == "command":
        command = data.get("command")
        if not command:
            raise ConfigError(
                f'{cfg_path}: [publish].command is required when backend="command" '
                f'(e.g. "notes-publish")'
            )
        return PublishConfig(backend=backend, command=command)

    cf = data.get("cloudflare")
    if not isinstance(cf, dict):
        raise ConfigError(
            f'{cfg_path}: [publish.cloudflare] section is required when '
            f'backend="cloudflare"'
        )
    missing = [field for field in _CLOUDFLARE_FIELDS if not cf.get(field)]
    if missing:
        raise ConfigError(
            f'{cfg_path}: [publish.cloudflare] missing required field(s) for '
            f'backend="cloudflare": {", ".join(missing)}'
        )
    return PublishConfig(
        backend=backend,
        cloudflare=CloudflareConfig(**{field: cf[field] for field in _CLOUDFLARE_FIELDS}),
    )
