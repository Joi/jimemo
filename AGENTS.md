# AGENTS.md

Instructions for any coding agent or harness (Claude Code, Codex, Cowork,
Amplifier, or a plain shell) working with jimemo. This is the CLI contract;
`README.md` is the human-facing tour and `docs/architecture.md` is the
module map for anyone changing jimemo's code.

## What this is

jimemo turns a template + a content file into one self-contained HTML
page — a briefing, dashboard, catalog, or timeline. Everything is stdlib
Python plus vendored dependencies: nothing to `pip install`, no network
access at render time. It also has an optional publish step for pushing a
rendered page to an unlisted private link.

## Install

```
git clone <this repo>
cd jimemo
./install.sh
```

`install.sh` symlinks the `jimemo` executable onto `PATH` and registers
this skill (`skill/`) with any harness it finds installed (Claude Code,
Codex, ...). Idempotent, and `./install.sh --uninstall` reverses it. One
clone; `git pull` updates every harness that points at it. Requires
Python >= 3.9.

Without `install.sh`, the manual equivalent is a symlink:
`ln -s $(pwd)/jimemo ~/.local/bin/jimemo`.

## Commands

Every subcommand has its own `--help`; this is the map, not the manual.

| Command | Does |
| --- | --- |
| `jimemo doctor` | checks Python version, vendor checksums, vendored imports, and stale suitability labels |
| `jimemo list` | lists installed templates (repo `templates/` + `~/.jimemo/templates/`) |
| `jimemo suggest <content> [--json]` | ranks templates by fit for a content file, with reasons |
| `jimemo info <template> [--json]` | shows a template's slot schema, components, charts, and suitability metadata |
| `jimemo render <template\|auto> <content> [-o OUT] [--theme NAME] [--open]` | renders a template + content file to one HTML file; `auto` uses the same scorer as `suggest` and falls through to the next-best template if the top pick's manifest rejects the content |
| `jimemo new-template <name>` | scaffolds a personal template under `~/.jimemo/templates/<name>/` |
| `jimemo import-design <export-dir> [--name NAME] [--embed-fonts]` | parses a Claude-design export into a jimemo theme at `~/.jimemo/themes/<name>.css` |
| `jimemo publish <file>` / `purge <hash-or-url>` / `list` / `gc` / `setup [--dry-run]` | publishes a rendered file to an unlisted link and manages it; `setup` provisions a backend in `~/.jimemo/config.toml` |

## The content contract

`jimemo info <template> --json` is the schema for that template: its
`slots` object gives each content field's `type` (`text`, `markdown`,
`data`) and whether it's `required`, plus `charts` (chart declarations
backed by schema-free `data` slots shaped `{labels: [...], series:
[{name, values}]}`) and `sample_files` (real example content, paths
relative to the template dir). Generate content that matches this schema
— either Markdown with YAML frontmatter (one key per slot except `body`,
which is the markdown after the closing `---`) or a flat JSON/YAML object
keyed by slot name — then hand it to `jimemo render`.

## Guarantees

- **Self-contained output.** A rendered `out.html` inlines its CSS and
  images and fetches nothing at view time; open it directly in a
  browser or hand it to someone with no server involved.
- **No network at render time.** Only `jimemo publish` (and
  `import-design`'s use of local files) touches anything outside the
  process; `jimemo render` never shells out or hits the network.
- **Sanitized input.** Markdown-typed slot content is rendered through a
  stdlib allowlist sanitizer before it reaches the page.

## Design systems are bring-your-own

`import-design` reads tokens and font references from a folder (a
Claude-design export) as data — it never opens, imports, or executes any
code in that folder. jimemo ships **zero** design systems: they're
copyrighted brand material, not tool code. Point `import-design` at
whatever export directory the user provides. A reasonable personal
convention is a private repo of exports cloned to
`~/.jimemo/design-systems/`, but that repo is never assumed to exist and
is never fetched automatically.

## Agent skill

`skill/SKILL.md` is the task-oriented workflow (pick a template, get its
schema, generate content, render, optionally publish/theme) for an agent
driving jimemo end to end. This file is the reference; that one is the
walkthrough.
