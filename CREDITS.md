# Credits

## Design inspiration

Ideas below informed jimemo's design but no code was copied from any
source. Full context: `docs/research/sections/01-prior-art-and-single-file.md`.

| Author | URL | Idea |
| --- | --- | --- |
| Dave Liepmann / Edward Tufte project | https://github.com/edwardtufte/tufte-css | Sidenotes and margin notes for report-style documents — footnote-style annotations placed in the page margin next to the referenced text instead of at the page bottom, with a CSS-only toggle for small screens. |
| picocss org | https://github.com/picocss/pico | Zero-class, semantic-HTML-first theming — automatic light/dark themes targeting plain tags (`header`, `main`, `article`) with no authored classes required. |
| pytest-dev | https://github.com/pytest-dev/pytest-html/blob/master/docs/user_guide.rst | Explicit warnings over silent failure when an asset can't be inlined, rather than quietly leaving a linked file as an external reference in "self-contained" mode. |
| Author of cr0x.net | https://cr0x.net/en/dark-mode-toggle-pattern/ | Three-state (`system`/`light`/`dark`) theme attribute pattern on the document root, reacting to both the attribute and `prefers-color-scheme`, instead of a two-state light/dark toggle. |

## Vendored libraries

Filled in as libraries are vendored (Task 7 and later phases). Authoritative
version pins live in `docs/research/2026-07-05-phase1-research.md`'s
`## Pinned shortlist`.

| Name | Version | License | Source |
| --- | --- | --- | --- |
| Jinja2 | 3.1.6 | BSD-3-Clause | https://pypi.org/project/Jinja2/ |
| MarkupSafe | 3.0.2 | BSD-3-Clause | https://pypi.org/project/MarkupSafe/ |
| Markdown | 3.10.2 | BSD-3-Clause | https://pypi.org/project/Markdown/ |
