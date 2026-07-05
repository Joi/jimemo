# Architecture

Orientation for contributors. The authoritative design is the spec:
`docs/superpowers/specs/2026-07-05-jimemo-design.md`.

- `jimemo` (repo root) — CLI entry point; puts `src/` and `vendor/` on
  `sys.path`. Users never pip-install anything.
- `src/jimemo/` — CLI implementation.
- `vendor/` — pinned pure-Python dependencies (Jinja2, MarkupSafe,
  Markdown) with `SHA256SUMS`; verified by `jimemo doctor`.
- `templates/<name>/` — a template is a folder: `template.html.j2`,
  `manifest.json`, `preview.jpg`, `sample/`. Personal templates live in
  `~/.jimemo/templates/`.
- `toolkit/`, `themes/`, `charts/` — shared CSS tokens/components, theme
  token files, vendored browser-side chart JS (later phases).
- `publish/` — generalized private-link publishing (later phase).
