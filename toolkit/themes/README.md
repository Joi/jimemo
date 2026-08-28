# Repo themes

Theme token overrides shipped with the repo: a `<name>.css` file here is
resolvable as `--theme <name>` on any machine with the repo. None ship
yet — jimemo bundles no design systems (see the README's "Design systems
are bring-your-own") — but the directory is on the theme search path,
after `~/.jimemo/themes/` (personal themes win a name collision; see
`src/jimemo/inline.py`).
