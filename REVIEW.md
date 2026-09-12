# Review instructions — jimemo

jimemo commit and range reviews read this file from the default branch; dirty reviews and local runs read the working copy.

Stdlib Python plus vendored, checksummed dependencies that render a
template and a content file into one self-contained HTML page. Plain
`jimemo render` never shells out and never touches the network; the
explicit `--pdf` / `jimemo pdf` modes launch a local Chromium-family
browser; `jimemo publish` is the only subcommand that touches the
network (`import-design` reads a local export folder and fetches
nothing). `jimemo check` is the self-containment guarantee.

Already enforced by the gate (do not repeat): `python3 -m pytest tests
-q`, including the HTML lint goldens and vendor checksums.

Always flag: a network or subprocess call on the plain render path
(`render` without `--pdf`); output that fetches anything at view time; a
change under `vendor/` without the matching checksum update; a new
runtime dependency that is not vendored.
