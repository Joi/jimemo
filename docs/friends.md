# Sharing jimemo with a friend

Exact steps for someone who isn't you: clone it, make a page, and
(optionally) turn on publishing and brand themes. Everything except the
optional publish step is pure Python -- nothing to `pip install`, no
Node, no account anywhere.

## 1. Clone and install

```
git clone https://github.com/Joi/jimemo.git jimemo
cd jimemo
./install.sh
```

`install.sh` needs `python3` >= 3.13.6 -- a final release, not a beta or
release candidate -- and nothing else. That is newer
than the `python3` macOS ships (`/usr/bin/python3` is 3.9.6) and newer than
most distro defaults, so you may need to install one first -- macOS:
`brew install python@3.13`; Debian/Ubuntu: `apt install python3.13` -- and
either put it first on your `PATH` or tell the installer where it is:
`./install.sh --python /path/to/python3.13`. The floor is that high
because `jimemo check` has to read a page's HTML as closely as it can to
the way a browser reads it, and Python's `html.parser` only stopped
disagreeing with browsers about
attribute character references, attribute splitting and unclosed `<style>`
elements in 3.13.4/3.13.6. `install.sh` and `./jimemo` both refuse an
older interpreter with one line rather than running with a weaker check.
`./jimemo` checks the `python3` you ran it with and never hunts for
another on your `PATH`. `install.sh` tries `python3`, `python3.13` and
`python3.14` in that order, runs each to check its version, and binds the
first that qualifies (or the one you named with `--python`), printing
which one it took and why it rejected the ones tried before it. It writes
`~/.local/bin/jimemo` as a small shell script that runs that interpreter
by absolute path, so the installed `jimemo` does not depend on which
`python3` your shell finds later; if that interpreter is removed or
replaced by an older one, `jimemo` says so in one line and asks you to
re-run `./install.sh`, which binds another. It also registers the agent
skill with whatever harness it finds on the machine (Claude Code/Cowork,
Codex, Amplifier) -- see
`AGENTS.md` if you're wiring it into something else. If `~/.local/bin`
isn't already on `PATH`, the installer says so and prints the line to
add to your shell rc.

Confirm the install:

```
jimemo doctor
```

`--uninstall` removes exactly what `install.sh` created -- the `jimemo`
entry point and the skill symlinks -- and leaves the clone itself
untouched:

```
./install.sh --uninstall
```

## 2. Make a first page

Every template ships a real sample under `templates/<name>/sample/`, so
there's something to render with zero setup:

```
jimemo list
jimemo render briefing templates/briefing/sample/content.md -o out.html
open out.html
```

`out.html` is one file -- CSS and images inlined, nothing fetched when
it's opened. Hand it to someone, attach it to an email, or drop it
anywhere; no server is involved in viewing it.

Not sure which template fits your own content yet? `jimemo suggest
<content-file>` ranks the installed templates against it and explains
why; `jimemo render auto <content-file> -o out.html` does the same
ranking and renders the top pick, falling through to the next-best
template if the content doesn't actually fit the top one's schema. See
the README's command tour for the full walkthrough, including how a
content file is shaped.

## 3. Optionally: set up your own publish site

This is the one step that needs Node, and it's entirely optional --
`render` never touches the network regardless.

If you don't already run something like notes.ito.com, `jimemo publish
setup` provisions a free Cloudflare Pages project for you: it shells out
to `npx wrangler`, so you need Node installed, plus a Cloudflare account
and an API token scoped to `Pages: Edit` + `Workers KV Storage: Edit`.
Preview the whole plan first, without creating or touching anything:

```
jimemo publish setup --dry-run
```

Full walkthrough, including a couple of one-time steps the wizard can't
automate (creating a KV namespace, binding it to the Pages project) and
how to publish from several machines safely (make the state directory a
git repo; jimemo then syncs it around every deploy): `docs/publish-setup.md`.

Once configured:

```
jimemo publish out.html
https://your-site.example.com/3f9a1c.../

jimemo publish purge https://your-site.example.com/3f9a1c.../
```

If you *do* already run a publish site with its own CLI, skip the
wizard and point jimemo at it instead (`backend = "command"` in
`~/.jimemo/config.toml`) -- see the README's Publish section.

## 4. Optionally: bring your own brand theme

jimemo ships zero design systems -- they're copyrighted brand material
(colors, typefaces, logos), not tool code, so none are bundled with this
repo. If you have a Claude-design export (a folder of design tokens and
font references), turn it into a theme:

```
jimemo import-design /path/to/your/export --name mybrand
jimemo render briefing content.md -o out.html --theme mybrand
```

If you keep a personal collection of exports in your own repo, clone it
to `~/.jimemo/design-systems/` and use `--from <name>` instead of typing
the full path each time:

```
git clone <your-private-design-systems-repo> ~/.jimemo/design-systems
jimemo import-design --from mybrand-name
```

`import-design` only ever reads the export's tokens and font references
as data -- it never opens, imports, or executes any code in the export
directory, so it's safe to point at an export you didn't write yourself.

## What needs what

| Step | Needs |
| --- | --- |
| Install, render, suggest, info, list, doctor, new-template, import-design | Python >= 3.13.6 only |
| Publish (either backend) | The `command` backend needs whatever CLI you already point it at; the `cloudflare` backend needs Node (`npx wrangler`), curl >= 8.3, a Cloudflare account, and `CLOUDFLARE_API_TOKEN` exported in your shell |

Everything in the first row works with nothing installed beyond Python
itself -- no account, no network access, no dependency to fetch.
