# Contributing

Open an ordinary GitHub pull request. That is the whole job from your
side:

1. Fork or branch, make the change. jimemo needs Python >= 3.13.6 — a
   patch-level floor, because `jimemo check` reads a page's HTML as closely
   as it can to the way a browser does, and `html.parser` only stopped
   disagreeing about attribute character references, attribute splitting and
   unclosed `<style>` text in 3.13.4/3.13.6 (see `src/jimemo/__init__.py`;
   `src/jimemo/_parser_floor.py` records what is and is not guaranteed).
   `./jimemo` checks the interpreter it is run with and refuses an older
   one with one line; it never looks for a newer one on `PATH`. The
   installed entry point is bound to the interpreter `install.sh` chose
   and verified (`python3`, `python3.13`, `python3.14` in that order, or
   `--python PATH`); the install refuses when none qualifies.
2. Run the tests: `python3 -m pip install pytest`, then
   `python3 -m pytest tests -q` (and `./jimemo doctor`) — pytest is the
   suite runner and the repo's only development dependency.
   If you changed a template or its sample, regenerate its golden with
   `JIMEMO_UPDATE_GOLDENS=1 python3 -m pytest tests/test_golden.py`.
3. Open the PR.

`main` only takes writes from an automated merge queue on Joi's side
(see `.repoman-managed`), so the web UI's merge button is disabled by a
repository ruleset — that's expected, not a problem with your PR. After
review, a maintainer lands your branch through the queue and the PR
closes with the landed commit referenced.

What makes a change easy to land:

- Tests with the change (the suite is fast and runs offline).
- No new runtime dependencies — the vendored-deps, nothing-to-pip-install
  property is load-bearing (see README "Security posture").
- Rendered output must stay self-contained: no remote fetches, no
  scripts outside the chart path. `jimemo check` on your output is the
  quick gate.
- New templates are a folder under `templates/<name>/` plus a golden and
  a `tests/test_selfrank.py` roster entry — see any seed template and
  `toolkit/README.md` for the component library.
