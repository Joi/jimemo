"""Import a Claude-design export (tokens + fonts) into a jimemo theme.

Submodules:
  - reader: parse-only ingestion of an export directory into a
    DesignExport (the parse-only reader).
  - mapping (later task): DesignExport -> jimemo `--jm-*` theme CSS.

Every module here treats the export directory as untrusted DATA: no
export code (`.js`/`.jsx`/`.ts`) is ever read or executed.
"""
