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
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ._vendor import add_vendor_to_path
from .errors import ConfigError

_CLOUDFLARE_FIELDS = ("project", "account_id", "kv_namespace_id", "base_url")

#: Cloudflare Pages project names: lowercase letters, digits, and hyphens;
#: no leading or trailing hyphen; 1-63 characters. Shared by load_config()'s
#: [publish.cloudflare] validation below and publish/setup.py's wizard-input
#: validation (imported from here) so the two can never drift on what
#: counts as a valid project name -- a hand-edited config.toml must be held
#: to the exact same rule the setup wizard enforces on its own prompt,
#: since the project name flows unescaped into a filesystem path join
#: (cloudflare_backend.py's _default_state_dir -> ~/.jimemo/cloudflare/
#: <project>/, so e.g. "../evil" would escape that directory).
PROJECT_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def valid_project_name(name: Any) -> bool:
    """True iff `name` is a valid Cloudflare Pages project name: a string
    of lowercase letters, digits, and hyphens, not starting or ending with
    a hyphen. See PROJECT_NAME_RE for why this is shared with setup.py
    rather than defined twice."""
    return isinstance(name, str) and bool(PROJECT_NAME_RE.match(name))


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

    # A hand-edited config.toml bypasses `jimemo publish setup`'s own input
    # validation entirely -- load_config() is the only gate a value like
    # project = "../.." (which CloudflarePublisher would then join into a
    # filesystem path -- see cloudflare_backend.py's _default_state_dir) or
    # a non-string field ever passes through. Validate every field is a
    # string before anything downstream (a path join, a URL, another TOML
    # write) gets to assume that.
    non_string = [
        field for field in _CLOUDFLARE_FIELDS if not isinstance(cf.get(field), str)
    ]
    if non_string:
        raise ConfigError(
            f'{cfg_path}: [publish.cloudflare] field(s) must be strings: '
            f'{", ".join(non_string)}'
        )

    if not valid_project_name(cf["project"]):
        raise ConfigError(
            f'{cfg_path}: [publish.cloudflare].project {cf["project"]!r} is '
            'not a valid Cloudflare Pages project name (lowercase letters, '
            'digits, and hyphens only; no leading or trailing hyphen)'
        )

    base_url = cf["base_url"]
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        raise ConfigError(
            f'{cfg_path}: [publish.cloudflare].base_url {base_url!r} must '
            'be an http(s) URL'
        )

    return PublishConfig(
        backend=backend,
        cloudflare=CloudflareConfig(**{field: cf[field] for field in _CLOUDFLARE_FIELDS}),
    )
