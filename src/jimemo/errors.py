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
