# Contributing

Open an ordinary GitHub pull request. That is the whole job from your
side:

1. Fork or branch, make the change.
2. Run the tests: `python3 -m pip install pytest`, then
   `python3 -m pytest tests -q` (and `./jimemo doctor`) — pytest is the
   suite runner and the repo's only development dependency.
   If you changed a template or its sample, regenerate its golden with
   `JIMEMO_UPDATE_GOLDENS=1 python3 -m pytest tests/test_golden.py`.
3. Open the PR.

`main` only takes writes from an automated merge queue on Joi's side
(see `.marshal-managed`), so the web UI's merge button is disabled by a
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
