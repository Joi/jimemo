# jimemo

Toolkit for making self-contained single-file HTML pages — briefings, memos,
catalogs, timelines, dashboards — from a library of templates, with an
optional private-link publishing setup. Stdlib + vendored dependencies only;
nothing to `pip install`, no network access at render time.

Design spec: `docs/superpowers/specs/2026-07-05-jimemo-design.md`. Module
map and layout: `docs/architecture.md`.

## Install

Clone the repo and put `jimemo` (the executable at the repo root) on your
`PATH`, e.g.:

```
ln -s /path/to/jimemo/jimemo ~/.local/bin/jimemo
```

Requires Python >= 3.9.

## Usage

Six seed templates ship in `templates/`: `briefing`, `chart-dashboard`,
`data-dashboard`, `genealogy`, `photo-catalog`, `timeline`.

List what's available:

```
$ jimemo list
briefing	/path/to/jimemo/templates/briefing
chart-dashboard	/path/to/jimemo/templates/chart-dashboard
data-dashboard	/path/to/jimemo/templates/data-dashboard
genealogy	/path/to/jimemo/templates/genealogy
photo-catalog	/path/to/jimemo/templates/photo-catalog
timeline	/path/to/jimemo/templates/timeline
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
labels):

```
$ jimemo doctor
ok   python 3.14.6
ok   vendor checksums (/path/to/jimemo/vendor)
ok   vendored imports (jinja2, markdown)
ok   suitability labels fresh (or none recorded)
```

## Development

```
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
```

Regenerate golden renders after a deliberate template/pipeline change:

```
JIMEMO_UPDATE_GOLDENS=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_golden.py
```

MIT license.
