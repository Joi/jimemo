"""Domain errors for the manifest/content/render pipeline.

Both carry a plain, user-facing message naming the offending field/slot/
path. The CLI prints ``str(error)`` to stderr and exits 1 — no traceback.
"""


class ManifestError(Exception):
    """A template's manifest.json is missing, malformed, or invalid."""


class ContentError(Exception):
    """A content file failed to parse/validate, or rendering could not
    produce safe self-contained output (missing local image, lint
    errors)."""


class ScaffoldError(Exception):
    """`new-template` was asked for an invalid name, or the destination
    template directory already exists."""


class ConfigError(Exception):
    """~/.jimemo/config.toml is missing, is not valid TOML, or is missing
    a field required by the selected publish backend."""


class PublishError(Exception):
    """A publish backend name is unknown, not configured, or not
    available; or a publish/purge/list/gc operation failed."""


class DesignImportError(Exception):
    """A Claude-design export's manifest/token CSS is missing, malformed,
    or contains a token value that isn't safe to drop into generated
    theme CSS (see jimemo.design.reader)."""
