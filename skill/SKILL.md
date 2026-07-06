---
name: jimemo
description: Make self-contained single-file HTML pages (briefings, dashboards, catalogs, timelines) from jimemo's templates and charts, with an optional private-link publish step. Use when the user wants a memo, report, dashboard, or similar page as one shareable HTML file, or wants it published to a private link.
---

# jimemo

jimemo is a CLI, not a library or an API — every step below is a shell
command. That makes this skill portable across any harness that can run a
subprocess (Claude Code, Codex, Cowork, Amplifier); nothing here depends on
being inside Claude Code specifically.

Requires `jimemo` on `PATH` (see the repo's `install.sh` / README). If it's
missing, run `jimemo doctor` after installing to confirm the environment is
sane before doing anything else.

## Workflow

### 1. Pick a template

If you already know roughly what the content is, ask `suggest` to rank
templates against it:

```
jimemo suggest <content-file> --json
```

Otherwise browse what's installed:

```
jimemo list
```

Templates live in the repo's `templates/` and in `~/.jimemo/templates/`
(personal templates, discovered alongside the built-in ones). `suggest`
needs a content file already shaped for *some* template's slots; if you
haven't drafted content yet, skip ahead and let `render auto` (below) pick
after you've drafted it against a reasonable guess.

### 2. Get the content schema

Before writing content, get the template's slot contract:

```
jimemo info <template> --json
```

The `slots` object is the contract: each key is a content field, with a
`type` (`text`, `markdown`, or `data`) and whether it's `required`. `data`
slots that back a chart (see `charts` in the same JSON) are schema-free —
shape them `{labels: [...], series: [{name, values}]}`. Read
`sample_files` (paths relative to the template dir) for a real example of
the shape.

### 3. Generate content matching the schema

Write a content file as either:
- **Markdown** (`.md`): YAML frontmatter with one key per slot, except
  `body` — that slot is everything after the closing `---`.
- **JSON or YAML** (`.json` / `.yaml`): a flat object keyed by slot name.

Only fill slots the manifest declares; required slots must be present.

### 4. Render

```
jimemo render <template> <content-file> -o out.html
```

If you're not fully sure which template fits, use `auto` instead of a
template name — it runs the same scorer as `suggest`, then walks the
ranking and picks the first template whose manifest actually accepts the
content (falling through candidates that need slots the content doesn't
have):

```
jimemo render auto <content-file> -o out.html
```

`render auto` prints which template it picked and why (or which ones it
skipped and why) to stderr — read that if the result looks wrong. The
output is always one self-contained HTML file: CSS and images inlined,
nothing fetched at view time. Nothing further needs bundling to share it.

### 5. Optionally publish

```
jimemo publish <out.html>
```

Prints an unlisted URL. Requires a backend configured in
`~/.jimemo/config.toml` first — run `jimemo publish setup` (or
`--dry-run` to preview without writing anything) if none is configured
yet. `jimemo publish purge <hash-or-url>` revokes a link;
`jimemo publish list` / `jimemo publish gc` manage what's published.

## Brand themes (optional)

`jimemo render` uses the toolkit's default look unless you pass
`--theme <name>`. To generate a theme from a brand's design tokens:

```
jimemo import-design <export-dir> --name <theme-name>
jimemo render <template> <content-file> --theme <theme-name>
```

`<export-dir>` is a Claude-design export (design tokens + font references,
produced by the design-system Skill) — a folder, not a URL. **Design
systems are copyrighted and are never bundled with jimemo.** The user
brings their own export, or keeps a personal collection in a private repo
they control (a reasonable convention: clone it to
`~/.jimemo/design-systems/` and pass a path under there to
`import-design`). Never fetch, clone, or reference someone else's design
export on their behalf without them providing it. The import is
parse-only — jimemo reads tokens as data and never executes any code in
the export directory.

## Stale suitability labels

`suggest --json` and `doctor` flag a template whose `suitability` block
(the `keywords` / `content_kinds` / `good_for` that drive template
ranking) predates the last edit to its `template.html.j2`, via a
`labeled_hash` mismatch in the manifest. If either command reports a
template as having stale labels: reread that template's
`template.html.j2` and `sample/` content, rewrite `manifest.json`'s
`suitability.keywords` / `content_kinds` / `good_for` to match what the
template now actually renders, then recompute `labeled_hash` as the
SHA-256 of the (possibly unchanged) `template.html.j2` file and write
that hex digest back into `suitability.labeled_hash`. There's no CLI
command for this — it's a direct manifest edit.

## Security posture

jimemo output is self-contained (no view-time network fetches), and
markdown/HTML content is sanitized before it lands in the page. Treat
`import-design` export directories as untrusted data: jimemo never opens,
imports, or executes the export's own code, only its token/font files.
`jimemo publish` is the only jimemo subcommand that touches the network.

## Reference

This skill intentionally doesn't restate every flag. For the full,
authoritative command surface:

```
jimemo --help
jimemo <command> --help
```

or the repo's `AGENTS.md` / `README.md`.
