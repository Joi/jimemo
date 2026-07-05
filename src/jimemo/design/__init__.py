"""Import a Claude-design export (tokens + fonts) into a jimemo theme.

Submodules:
  - reader: parse-only ingestion of an export directory into a
    DesignExport (this phase's Task 1).
  - mapping (later task): DesignExport -> jimemo `--jm-*` theme CSS.

Every module here treats the export directory as untrusted DATA: no
export code (`.js`/`.jsx`/`.ts`) is ever read or executed.
"""
