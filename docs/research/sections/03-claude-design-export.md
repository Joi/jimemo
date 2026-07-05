# Claude design export format

Research date: 2026-07-05. Scope: what a Claude Design export contains, how
stable the format is, and what `jimemo import-design` (Phase 6) can safely do
with one.

## Export format findings

**Claude Design is a real, separate product**, not the same thing as chat
Artifacts. It launched as an Anthropic Labs research preview ([Claude Help
Center, "Get started with Claude Design"](https://support.claude.com/en/articles/14604416-get-started-with-claude-design);
[Anthropic's "Introducing Claude Design by Anthropic Labs"
post](https://www.anthropic.com/news/claude-design-anthropic-labs)). A
June 2026 update reportedly added two-way `/design-sync` with Claude Code
and design-system import — **this specific claim is sourced only from a
third-party blog ([explainx.ai](https://explainx.ai/blog/claude-design-june-2026-update-design-sync-2026)),
not corroborated in either official source fetched for this research**, so
treat the exact ship date and feature framing as unconfirmed.

Documented export options, per the official Help Center article:
- "Download as .zip"
- "Export as PDF"
- "Export as PPTX"
- "Send to Canva"
- "Export as standalone HTML"
- "Send to the tools you already use, including Adobe, Base44, Canva, Gamma,
  Lovable, Miro, Replit, Vercel, and Wix, with more destinations coming
  soon."
- "Handoff to Claude Code," with two sub-options: "Send to local coding
  agent" and "Send to Claude Code Web"
- an internal share URL (view/comment/edit) for org-internal sharing

**Neither official source documents the internal structure of the standalone
HTML export.** The Help Center page and the Anthropic Labs announcement both
mention "standalone HTML" only as a list item — no schema for CSS custom
properties, no component/class naming convention, no statement of whether
assets are inlined as data URIs or left as linked files. Per the same
third-party source cited above (not officially confirmed), the
`/design-sync` pull direction is described as transferring "your design
system" (colors, typography, components) into a repo, with imports coming
from "style guides, token JSON, brand PDFs" or a git repo — again without a
published schema.

One concrete, independent data point: this machine has a Vercel MCP tool
(`import-claude-design-from-url`, part of the Vercel plugin already
installed here) built specifically to consume this export format. Its own
description states the input is "a self-contained HTML bundle with all
images, fonts, and styles inlined." That is a second party's engineering
description of the actual artifact (not a blog's guess), and it is the most
concrete fact available: **single HTML file, fonts and images inlined,
styles inlined** — consistent with jimemo's own single-file-output
philosophy.

Beyond that, the strongest remaining evidence is one repackaging project,
plus generic blog restatement with no inspected file behind it:
- [`jimliu/baoyu-design` on GitHub](https://github.com/jimliu/baoyu-design)
  states, in its own Credits & license section, that it "repackages **Claude
  Design**, the design skill by **Anthropic** that powers claude.ai/design,
  so it can run on local agents" — i.e. it claims to carry over Anthropic's
  actual design skill rather than independently reimplementing its
  behavior from observation. That makes it weightier than a typical
  third-party blog, though it is still not Anthropic documentation and its
  claims about internals are unverified here. It describes its own output
  as using "curated token CSS" and extracting real SVG/PNG assets "copied,
  never redrawn" — but its README itself concedes tension between a
  "self-contained" claim and a preview server needed because "multi-file
  prototypes won't load from `file://`," so even this source doesn't settle
  the data-URI-vs-linked-assets question.
- General blog coverage ([explainx.ai](https://explainx.ai/blog/claude-design-june-2026-update-design-sync-2026))
  repeats "colors, type, spacing, component vocabulary" language without
  ever quoting a real token file or showing an inspected export.

**Local inspection turned up nothing to examine directly.** No Claude
design export HTML files exist in `~/Downloads` on this machine
(`ls ~/Downloads/*.html` returned no matches), and no DesignSync-specific
tool or slash command is installed in this Claude Code environment — the
only locally installed "design" surfaces are the generic `frontend-design`
and `canvas-design` skills, which give aesthetic/authoring guidance and say
nothing about an export file's internal format.

**Stability assessment: undocumented and actively moving.** The product is
still a "research preview," gained a significant new sync feature in the
same month this research was done, and no official spec for the exported
file's internals has been published. Any parser jimemo writes against this
format should assume it will need re-validation against fresh sample
exports before each jimemo release, not a one-time integration.

## Import path for jimemo

Given the format is real and single-file but internally undocumented, the
defensive parse strategy the brief anticipates is the right call — extract
only what is structurally guaranteed to be inspectable in any CSS file, and
touch nothing else:

**What `import-design` can safely and mechanically extract into a theme:**
- CSS custom properties declared in `:root` (or `:host`) blocks — color,
  spacing, and any other `--token-name: value;` pairs. This needs only a
  regex/CSS-parse pass over `<style>` contents (`:root\s*{[^}]*}`, then
  `--[\w-]+:\s*[^;]+;` within it), not an HTML parser that understands
  Claude Design's specific markup.
- Font stacks — from `@font-face` declarations and from `font-family` on
  body/heading-level selectors. These map directly onto
  `toolkit/tokens.css`'s existing custom-property model, so an extracted
  theme is just a new file under `themes/` in the same shape jimemo already
  uses.
- Base type-scale values, if expressed as custom properties (e.g.
  `--font-size-h1`), fall out of the same `:root` extraction for free.

**What requires a template mint instead of a theme import, and why it
should stay manual/semi-automatic rather than fully scripted:** component
markup and layout. Because the DOM structure, class names, and component
boundaries in a Claude Design export are not documented and are not
guaranteed stable release to release, a script that scrapes markup and
drops it into `templates/<name>/template.html.j2` would be copying
unknown, unversioned structure into the repo — exactly the failure mode the
security posture rules out ("no code copied from unknown sources"). The
CLI's own signature (`jimemo import-design <export> # → theme (+ template)`)
already treats the template half as optional/secondary to the theme half;
Phase 6 should keep it that way: run the token extraction automatically,
then hand the export to an agent to *look at* (never execute) and manually
author or adapt a template folder against it, rather than auto-translating
its markup.

**Concrete recommendations for Phase 6:**
1. Treat every export as an opaque HTML file. Read it as text, scan only
   `<style>` blocks for `:root` custom properties and `@font-face`/
   `font-family`. Never parse or execute `<script>` content — ignore it
   entirely, per the existing security posture.
2. Write extracted tokens to `themes/<slug>.css` in the same custom-property
   shape as `toolkit/tokens.css`, so any existing template can use the new
   theme immediately with zero template work.
3. Mark the generated theme file with a comment noting it was imported from
   a specific export filename/date, so `jimemo doctor` can flag it if the
   Claude Design format later changes shape underneath it.
4. Do not attempt automatic component/template generation from the export
   in a first pass. If a friend wants a full template from their design,
   that stays an agent-assisted, human-reviewed authoring step against the
   exported HTML as a visual reference — not a scrape.
5. Before shipping Phase 6, get one real sample export (there were none on
   this machine at research time) and validate the `:root`/`@font-face`
   regex against it, since every source consulted here is either silent on
   internals or a third party's own reimplementation, not the real file.

### Sources

- Official — export format list: https://support.claude.com/en/articles/14604416-get-started-with-claude-design
- Official — product launch, design-system onboarding description: https://www.anthropic.com/news/claude-design-anthropic-labs
- Third-party blog, not officially corroborated — `/design-sync` behavior and June 2026 update claim: https://explainx.ai/blog/claude-design-june-2026-update-design-sync-2026
- Third-party repackaging project (carries Anthropic's actual design skill per its own README, not an independent reimplementation) — used only to corroborate/contrast internals claims, explicitly flagged as non-official above: https://github.com/jimliu/baoyu-design
- Local, this Claude Code environment — Vercel MCP plugin tool description (`import-claude-design-from-url`), the single most concrete engineering description of the export's file shape found in this research: no public URL (installed plugin tool metadata, inspected via ToolSearch in this session)
- Local checks (no URL): `ls ~/Downloads/*.html` (no matches); searches of `~/.claude/skills`, `~/.claude/plugins`, `~/.claude/commands`, `~/.claude/settings*.json` for "DesignSync"/"design-sync" (no matches beyond generic design-guidance skills)
