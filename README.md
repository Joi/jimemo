# jimemo

Toolkit for making self-contained single-file HTML pages — briefings, memos,
catalogs, timelines, dashboards — from a library of templates, with an
optional private-link publishing setup. Stdlib + vendored dependencies only;
nothing to `pip install`, no network access at render time.

Module map and layout: `docs/architecture.md`. Setting this up for
someone else? See [`docs/friends.md`](docs/friends.md) for the exact
steps.

## Install

```
git clone <this repo's URL> jimemo
cd jimemo
./install.sh
jimemo doctor
```

Requires Python >= 3.9 and nothing else -- stdlib plus vendored
dependencies only, nothing to `pip install`. It's one clone: `install.sh`
symlinks everything back to it, so `git pull` updates every harness at
once. The script is idempotent, refuses to clobber a real file/dir that
isn't its own symlink, and `./install.sh --uninstall` reverses exactly
what it created, leaving the clone untouched. `--dry-run` prints the plan
without touching anything.

`install.sh` creates:

- `~/.local/bin/jimemo` -> the CLI (add `~/.local/bin` to your `PATH` if
  it isn't already).
- the agent skill (`skill/`) symlinked into each harness skills directory
  that applies (see the table).

### Per-harness coverage

| Harness | How it gets jimemo | Set up by `install.sh`? |
|---|---|---|
| **Claude Code** | skill at `~/.claude/skills/jimemo` | yes |
| **pi** | reads `~/.claude/skills` (its `settings.json` `skills` paths), so it picks up the same skill | yes -- no separate step |
| **Codex** | skill at `~/.codex/skills/jimemo` | yes |
| **Amplifier** | skill at `~/.amplifier/skills/jimemo` | yes |
| **Claude Desktop** (Cowork / local-agent mode) | **skill is not auto-loaded** -- its skills are app-managed, not read from `~/.claude/skills`. But local-agent mode runs shell commands, so the **`jimemo` CLI works** there directly. | CLI only |

**Claude Desktop note:** because Cowork can't load a filesystem skill,
just tell it to use the `jimemo` command (it's on `PATH`), or point it at
[`AGENTS.md`](AGENTS.md), which is the same CLI contract the skill wraps.
jimemo is CLI-first by design -- the skill is a convenience layer, so any
agent that can run a shell can drive the tool without it.

### Manual install

Without `install.sh`, the CLI is just a symlink, and the skill is a
symlink per harness:

```
ln -s /path/to/jimemo/jimemo   ~/.local/bin/jimemo
ln -s /path/to/jimemo/skill    ~/.claude/skills/jimemo    # Claude Code (and pi)
ln -s /path/to/jimemo/skill    ~/.codex/skills/jimemo     # Codex
ln -s /path/to/jimemo/skill    ~/.amplifier/skills/jimemo # Amplifier
```

## Usage

Seven seed templates ship in `templates/`: `briefing`, `chart-dashboard`,
`data-dashboard`, `genealogy`, `ops-board`, `photo-catalog`, `timeline`.

List what's available:

```
$ jimemo list
briefing	/path/to/jimemo/templates/briefing
chart-dashboard	/path/to/jimemo/templates/chart-dashboard
data-dashboard	/path/to/jimemo/templates/data-dashboard
genealogy	/path/to/jimemo/templates/genealogy
ops-board	/path/to/jimemo/templates/ops-board
photo-catalog	/path/to/jimemo/templates/photo-catalog
timeline	/path/to/jimemo/templates/timeline
```

Start a content file without reverse-engineering the sample — `scaffold`
emits a fill-in skeleton matching a template's slots (markdown frontmatter,
or YAML for templates without a body slot):

```
$ jimemo scaffold briefing -o mynote.md
$ jimemo scaffold ops-board -o board.yaml
```

Inspect a template's slots and suitability:

```
$ jimemo info briefing
briefing — Briefing / memo
A status memo: masthead, optional stat row, prose summary, optional sections.

Slots:
  title            text      required
  date             text
  kicker           text
  subtitle         text
  body             markdown  required
  stats            data
  sections         data
...
```

`--json` gives the same data machine-readable (manifest verbatim plus
`template_dir` and `sample_files`):

```
$ jimemo info briefing --json | python3 -m json.tool
```

Not sure which template fits your content? `suggest` scores every
installed template against a content file and explains why:

```
$ jimemo suggest templates/briefing/sample/content.md
1. briefing  (score 4.0)
     - prose-dominant (212 words, 5 records) -> narrative
     - keyword 'briefing' matched
     - keyword 'status' matched
2. data-dashboard  (score 3.0)
     - top-level list of records -> tabular-data
3. timeline  (score 2.0)
     - keyword 'history' matched
     - keyword 'log' matched
```

Render a specific template, or let `auto` pick one via the same scorer:

```
$ jimemo render briefing templates/briefing/sample/content.md -o out.html
wrote out.html

$ jimemo render auto templates/briefing/sample/content.md -o out.html
auto-selected briefing: prose-dominant (212 words, 5 records) -> narrative
wrote out.html
```

A content file is either `.md` (YAML frontmatter for every slot except
`body`, which is the markdown after the closing `---`) or a `.json`/`.yaml`
object keyed by slot name — see any `templates/<name>/sample/` for a real
example. `out.html` is a single file: CSS and images inlined, nothing
fetched at view time; open it directly in a browser.

Add `--pdf [PATH]` to also write a PDF (default: the HTML path with
`.pdf` swapped in), or give `-o` a `.pdf` extension instead for PDF
only — no HTML file gets written. Both need a locally installed
Chromium-family browser; see "The draft loop" below.

### The draft loop

`out.html` is an ordinary file: open it, tweak it directly, or edit the
content file and re-render (re-rendering overwrites hand tweaks).
Before finishing, re-verify a hand-tweaked file, convert it, and
publish it:

```
$ jimemo check out.html
ok out.html

$ jimemo pdf out.html
wrote out.pdf

$ jimemo publish out.html
https://notes.example.com/3f9a1c.../
```

`check` re-runs the self-containment lint with no template involved.
`pdf` and `publish` run the same check on HTML input first and refuse
on violations (`--no-verify` skips it); `pdf` then converts through a
locally installed Chromium-family browser (Chrome, Chromium, Edge, or
Brave) since charts are Chart.js — JavaScript a PDF library can't run.
`jimemo doctor` reports whether one was found. See "Publish" below for
backend setup.

### Diagrams

Templates carry charts, not free-form diagrams — and markdown content
is sanitized, so inline SVG can't ride in through a content file.
Diagrams enter through the draft loop instead: leave a
`[[DIAGRAM:NAME]]` placeholder paragraph in the content, render,
replace the placeholder `<p>` in `out.html` with a `<figure>` of
hand-written inline SVG colored with the page's `--jm-*` tokens (so
light/dark mode keeps working), and re-run `jimemo check`. Patterns,
pitfalls, and copy-paste snippets: [`docs/diagrams.md`](docs/diagrams.md).

### Charts

`chart-dashboard` renders headline stat tiles plus a line and a bar
chart from tabular content:

```
$ jimemo render chart-dashboard templates/chart-dashboard/sample/content.yaml -o out.html
wrote out.html
```

A template declares charts in its manifest (`charts: [{id, type,
data_slot, title}]`); each chart reads its data from an ordinary
schema-free `data` slot shaped `{labels: [...], series: [{name,
values}]}` — no chart-specific content format to learn. Chart.js is
vendored under `charts/vendor/chartjs/` and inlined into the page
(no CDN, no network access at view time), so a chart-bearing `out.html`
stays exactly what every other jimemo page is: one self-contained
file you can open directly in a browser.

Scaffold a new personal template under `~/.jimemo/templates/` (discovered
alongside the repo's own):

```
$ jimemo new-template zine
created /Users/you/.jimemo/templates/zine
```

Check the environment (vendor checksums, Python version, stale suitability
labels, PDF browser availability):

```
$ jimemo doctor
ok   python 3.14.6
ok   vendor checksums (/path/to/jimemo/vendor)
ok   charts vendored (chart.js 4.5.1)
ok   vendored imports (jinja2, markdown, yaml, tomli)
ok   suitability labels fresh (or none recorded)
ok   pdf browser (/Applications/Google Chrome.app/Contents/MacOS/Google Chrome)
```

## Import a design

`jimemo import-design <export-dir> --name mybrand` reads a Claude-design
export (a folder of design tokens and fonts produced by the
design-system Skill) and produces a jimemo **theme**: a `--jm-*` token
override file that `jimemo render <template> <content> --theme mybrand`
layers on top of the toolkit's defaults.

```
$ jimemo import-design "Northwind Field Kit" --name mybrand
/* jimemo theme 'mybrand' -- auto-generated from a Claude-design export
 * (namespace: NorthwindFieldKit_7b3f21). Deterministic: re-running the import on the
 * same export regenerates this file byte-for-byte.
 *
 * Auto-mapped roles (source token -> role):
 *   --nw-font -> --jm-font-prose
 *   --nw-font -> --jm-font-ui
 *   --nw-blue-core -> --jm-accent
 *   ...
 */

fonts are referenced by family name only (no font files embedded); they
render correctly only where that family is installed on the viewer's
system. Re-run with --embed-fonts to inline the font files instead (see
the licensing note that prints with it).

wrote theme: /Users/you/.jimemo/themes/mybrand.css
use it with: jimemo render <template> <content> --theme mybrand

$ jimemo render briefing templates/briefing/sample/content.md \
    -o out.html --theme mybrand
```

It's **parse-only**: the importer reads the export's `_ds_manifest.json`
(or, absent a manifest, `tokens/*.css`) and never opens, imports, or
executes the export's `.js`/`.jsx`/`.ts` files — the whole export is
treated as untrusted data, never as code. Every token value is
validated before it lands in the generated theme (rejecting anything
that could break out of a CSS declaration or point at a remote
resource), and the theme itself is checked against the same
self-contained-page rule every other jimemo output follows: no remote
`url()`, no `@import`, no script.

Fonts map to family name + a generic fallback stack by default, so the
theme names the brand's typeface but renders correctly only where that
font is already installed on the viewer's machine. Pass `--embed-fonts`
to instead read the export's font files and inline them into the theme
as base64 `data:` URIs — the rendered page stays self-contained but
gets larger, and only makes sense for fonts you're licensed to
redistribute, since embedding publishes the font bytes in every page
rendered with that theme.

Imported themes are written to `~/.jimemo/themes/<name>.css`, never
into the repo, and take precedence over a repo theme of the same
name — a theme you just imported wins even on a name collision.

### Design systems are bring-your-own

jimemo ships **zero** design systems: they're copyrighted brand
material (colors, typefaces, logos), not tool code, so none are
bundled with this repo, ever. Point `import-design` at an export
directory someone gave you, or keep a personal collection in a
private repo you control, cloned to `~/.jimemo/design-systems/`:

> **Respect the copyright.** A design system you import belongs to its
> owner — only use ones you have the rights to, don't redistribute them,
> and don't publish pages that embed their fonts (`--embed-fonts`) unless
> you're licensed to. jimemo is the tool; the brand material is not yours
> to hand on.

```
git clone <your-private-design-systems-repo> ~/.jimemo/design-systems
jimemo import-design --from mybrand    # resolves ~/.jimemo/design-systems/mybrand/
```

`--from <name>` is sugar for the positional export-dir argument — it
resolves `<name>` (a lowercase-letters/digits/hyphens slug; nothing
else, since it becomes a path component) against that convention dir
and errors out, naming the exact path it expected, if nothing's there
yet. It's mutually exclusive with passing an export dir positionally;
the positional form still works unchanged for a one-off export
that isn't part of a collection.

## Publish

`jimemo publish` turns a rendered `out.html` into an unlisted private
link, mirroring notes.ito.com's model: a 24-hex-char hash path is the
access control (`secrets.token_hex(12)`, ~96 bits, unguessable), reading
and purging use the same URL, and purging tombstones a hash rather than
deleting it outright. Configure exactly one backend in
`~/.jimemo/config.toml`. The same file takes an optional `[pdf] browser`
key naming a specific Chromium-family binary, for when `jimemo pdf` /
`--pdf` can't auto-detect one already on the machine.

### `command` backend — you already run a publish site

If you already run something like `notes-publish` (notes.ito.com's own
CLI), point jimemo at it instead of reimplementing hosting:

```toml
[publish]
backend = "command"
command = "notes-publish"
```

```
$ jimemo render briefing content.md -o out.html
$ jimemo publish out.html
https://notes.example.com/3f9a1c.../

$ jimemo publish purge https://notes.example.com/3f9a1c.../
```

jimemo shells out to `command` for publish/purge/list/gc and parses the
published URL from its stdout; the configured command stays the sole
authority on hosting, hashing, and storage.

### `cloudflare` backend — no existing site

For someone with nowhere to publish to yet: `jimemo publish setup` walks
through provisioning a free Cloudflare Pages project and KV namespace.
It needs Node (everything runs through `npx wrangler`) and a Cloudflare
API token scoped to `Pages: Edit` + `Workers KV Storage: Edit`; a couple
of one-time steps (creating the KV namespace, binding it to the Pages
project as `TOMBSTONES`) have no wrangler CLI equivalent, so the wizard
prints the exact manual command or dashboard step instead of faking
automation of it. See [`docs/publish-setup.md`](docs/publish-setup.md)
for the full walkthrough, including the single-machine state-directory
limitation. Preview the whole plan without touching any account:

```
$ jimemo publish setup --dry-run
```

Once configured, publish/purge/list/gc use the same commands shown
above for the `command` backend.

### Security model

The hash is the entire access-control story: unguessable, and symmetric
between read and purge — anyone with the link can view or purge it,
nobody without the link can find it. Purging tombstones the hash
(subsequent requests 404) rather than deleting the underlying file;
`gc` is the separate step that removes tombstoned files. Full details,
including the `cloudflare` backend's single-machine limitation, are in
[`docs/publish-setup.md`](docs/publish-setup.md).

## Security posture

- **Self-contained output.** A rendered `out.html` inlines its CSS and
  images; nothing is fetched when it's opened. Hand it to anyone with
  no server involved.
- **No network at view or render time.** `jimemo render` never shells
  out or touches the network. `jimemo publish` is the only subcommand
  that does (and only when you run it).
- **Sanitized content.** Markdown-typed slot content goes through a
  stdlib allowlist sanitizer before it lands in the page.
- **Design exports are untrusted data.** `import-design` reads an
  export's tokens and font references; it never opens, imports, or
  executes any code the export directory contains.
- **Vendored, checksummed dependencies.** Jinja2, MarkupSafe, Markdown,
  PyYAML, tomli, and Chart.js are vendored into the repo, not fetched
  at install or run time; `jimemo doctor` verifies them against
  checked-in SHA-256 sums and refuses to import a tampered copy.

## Development

```
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
```

Regenerate golden renders after a deliberate template/pipeline change:

```
JIMEMO_UPDATE_GOLDENS=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_golden.py
```

## License

MIT — see [`LICENSE`](LICENSE). Third-party credits (vendored libraries,
design inspiration, ported code) are in [`CREDITS.md`](CREDITS.md).
