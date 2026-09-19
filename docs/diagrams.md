# Diagrams in jimemo pages (inline SVG)

jimemo templates have no diagram slot, and markdown-typed slots pass
through the allowlist sanitizer — inline SVG written into a content file
will not survive rendering. That is deliberate: content is untrusted.
The supported route for diagrams is a placeholder paragraph in the content
plus `jimemo render --figure`, which splices an SVG file in its place.
The patterns below were worked out on a real page (a tax-mechanics
explainer with four diagrams) and are the difference between an SVG that
fights the page and one that looks native.

## Workflow: placeholder → `--figure` → check

1. In the content file, put a placeholder paragraph where each diagram
   belongs. It must be a paragraph of its own, in a markdown slot; NAME is
   letters, digits, `_` or `-`:

   ```markdown
   Some prose introducing the figure.

   [[DIAGRAM:BASKETS]]

   Prose that refers back to it.
   ```

2. Write each diagram as a standalone SVG file whose root is one `<svg>`
   element (rules and snippets below).

3. Render with one `--figure NAME=FILE` per diagram:

   ```
   jimemo render briefing note.md -o out.html \
     --figure BASKETS=baskets.svg --figure TIMELINE=timeline.svg
   ```

   Each `<p>[[DIAGRAM:NAME]]</p>` in the rendered page is replaced with
   `<figure class="jm-figure" style="contain:paint">` + the sanitized SVG
   + `</figure>` (paint containment keeps a figure from drawing outside
   its own box, whatever its `style` says). The splice happens after markdown sanitization and before the
   self-containment lint, so the page that is written has already passed
   `jimemo check`. Re-rendering repeats the splice; nothing is lost.

   Errors, all before anything is written: a NAME with no placeholder in
   the content (never a silent no-op), a FILE that is not one `<svg>`
   element, an `id` that another figure or the page itself already uses,
   an output path that is one of the `--figure` files, and a malformed
   `--figure` value (the last two exit 2).

4. Look at the result (see "Verifying the result"). Nothing detects text
   that overflows the viewBox: text metrics need a renderer, so the
   screenshot loop stays the check.

### What the sanitizer removes

The SVG file is treated as untrusted — agents pass generated SVG without
reading it — and is rebuilt through an allowlist
(`jimemo.sanitize.sanitize_svg`). What survives: `svg`, `g`, `defs`,
`title`, `desc`, `path`, `rect`, `circle`, `ellipse`, `line`, `polyline`,
`polygon`, `text`, `tspan`, `marker`, `pattern`, `linearGradient`,
`radialGradient`, `stop`, `clipPath`, `use`, with geometry and
presentation attributes, `id`, `class`, `role`, `aria-label`,
`aria-labelledby`, `aria-hidden`, and `style`. What does not:

- `<script>`, `<style>`, `<foreignObject>`, `<image>`, `<a>`, the
  animation elements, and every element not listed above — dropped with
  everything inside them. `<filter>` and `<mask>` are not on the list.
- Every `on*` event-handler attribute, and every attribute not on the
  allowlist.
- `href` / `xlink:href`, except a same-document `#id` (no spaces) on
  `<use>`. Note
  that the self-containment lint still refuses `<use href="#id">` today,
  so a figure that uses `<use>` fails to render with a lint error; repeat
  the shape instead.
- Any `style` or presentation-attribute value (`fill`, `stroke`,
  `clip-path`, `marker-*`, `font-family`, …) that contains a backslash,
  a CSS comment delimiter (`/*` or `*/`), or a function other than
  `var(--token)`, `url(#id)`, `rgb()`, `rgba()`, `hsl()`, `hsla()`,
  `color-mix()`, `calc()`, `min()`, `max()`, `clamp()`, `translate()`,
  `translateX()`, `translateY()`, `scale()`, `scaleX()`, `scaleY()`,
  `rotate()`, `skewX()`, `skewY()` and `matrix()`. So `oklch()`,
  `skew()` and the 3D transforms are refused. The whole attribute is
  dropped, not rewritten: one refused function in a `style` loses every
  declaration in it. Write references bare — `url(#grad)`, not
  `url("#grad")` — and keep comments out of `style`.
- Every other ARIA attribute (`aria-describedby`, …).
- Elements inside `<title>` and `<desc>`; their text is kept.
- Comments, DOCTYPE, processing instructions, CDATA.

Every element and attribute removed is reported on stderr, one line per
distinct drop, so a refused `style` does not pass unnoticed as a shape
painted default black. An element removed with its subtree is one line:
what was inside it is not listed separately.

```
warning: figure FLOW: dropped element script (not allowlisted)
warning: figure FLOW: dropped attribute style (css value refused)
warning: figure FLOW: dropped attribute href (href not a same-document #fragment)
warning: figure FLOW: dropped element g (child of <title>/<desc>)
```

The other reasons are `outside the <svg> root` and `id is not a plain
token`. A figure prints at most 20 such lines, then one line counting the
rest (`warning: figure FLOW: 7 more distinct drops not shown`). The line
names what was removed, never the value: a refused `style` is untrusted
text, so it does not go to the terminal. For the same reason element and
attribute names, and the figure name, are shown as printable ASCII with
anything else as `?`, cut to 40 characters. A repeated attribute is not
reported, because a browser ignores it too. A warning is not an error —
the page still renders — so read the warnings for every figure before
publishing.

Inline SVG shares the page's one `id` namespace. Two figures that both
define `id="grad"` are refused, and so is a figure id the page already
uses — a heading anchor, or a chart's canvas, whose script would
otherwise find the SVG element first and never draw. Give each figure's
ids a distinct prefix (`baskets-grad`, `timeline-arrow`). An `id`
containing whitespace or a control character is dropped.

### Fallback: splice by hand

For a one-off tweak the old draft loop still works: render without
`--figure`, replace each `<p>[[DIAGRAM:NAME]]</p>` in the output with a
`<figure>` containing the SVG, and re-verify with `jimemo check out.html`
(`publish` and `pdf` re-run the same check). A hand splice is not
sanitized, and re-rendering the content file overwrites it — keep the SVG
in a file if you expect to re-render, or use `--figure`.

## Why inline SVG, not `<img>`

The page's CSS custom properties cascade into inline SVG, so a diagram
colored with `var(--jm-*)` tokens follows light and dark mode for free.
An `<img>` with a data-URI SVG is an isolated document: page tokens do
not reach it, and it would need hard-coded colors that break in one of
the two themes.

## Rules that make an SVG look native

- **Root element.** Fixed `viewBox`, fluid width, page UI font:

  ```html
  <svg viewBox="0 0 760 340" role="img" aria-label="One-sentence description
    of what the diagram shows, for screen readers and PDF text extraction."
    style="width:100%;height:auto;font-family:var(--jm-font-ui)">
    ...
  </svg>
  ```

  `--figure` adds the `<figure class="jm-figure" …>` wrapper, and the page
  CSS already gives a figure its vertical margin; the SVG file holds the
  `<svg>` element only. (When splicing by hand, wrap it in
  `<figure>…</figure>` yourself.)

  760 is close to the rendered content column, so viewBox pixels map
  roughly 1:1 to screen pixels — font sizes behave like page font sizes.

- **Color only with page tokens.** The stable core set: `--jm-text`,
  `--jm-muted`, `--jm-accent`, `--jm-positive`, `--jm-negative`,
  `--jm-border`, `--jm-surface`, `--jm-chart-1` … `--jm-chart-8`, plus
  `--jm-font-ui` / `--jm-font-mono`. Discover what a given page defines:

  ```
  grep -o '\-\-jm-[a-z0-9-]*' out.html | sort -u
  ```

- **`var()` does not resolve in SVG presentation attributes.**
  `fill="var(--jm-accent)"` silently fails; put every color in a `style`
  attribute instead: `style="fill:var(--jm-accent)"`. This includes
  paths inside `<marker>` definitions (document-level custom properties
  do reach marker content, but only via `style`).

- **Default to labels outside colored fills, in `var(--jm-text)`.**
  Several `--jm-chart-*` slots sit below 3:1 contrast against white
  (`toolkit/README.md` documents aqua, yellow, and magenta as WARN
  slots in light mode), a custom `--theme` can reshuffle the whole
  palette, and nothing lints SVG text contrast. So: put value labels
  above, below, or beside the shape in `fill:var(--jm-text)`
  (secondary lines in `--jm-muted`). Two sanctioned exceptions:
  on an `--jm-accent` fill use `fill:var(--jm-accent-contrast)`, which
  the theme flips for exactly this purpose; and white inside a
  chart-token fill only after checking the token's light AND dark
  values in the rendered page's CSS give bold white roughly 4.5:1 —
  then confirm on a screenshot in both themes. Never use a literal
  gray for text anywhere.

- **SVG text never wraps, and nothing detects overflow.** Break long
  labels into separate `<text>` (or `<tspan>`) lines yourself. Budget
  roughly 6 px per character at `font-size:12.5px` in the UI font — about
  90 characters across a 760-wide viewBox. The classic failure is a
  caption running off the right viewBox edge, and neither `--figure` nor
  `jimemo check` can catch it: verify visually (screenshot the rendered file; crop to the
  figure if the page is long).

- **Keep text ≥ 11.5px** in a 760 viewBox or it turns to dust in the PDF
  export.

## Snippets

Arrowhead marker (one per color; `orient="auto-start-reverse"` lets the
same marker serve both arrow directions):

```html
<defs>
  <marker id="arrOK" viewBox="0 0 10 10" refX="9" refY="5"
          markerWidth="7" markerHeight="7" orient="auto-start-reverse">
    <path d="M0 0 L10 5 L0 10 z" style="fill:var(--jm-positive)"/>
  </marker>
</defs>
<path d="M127 200 L127 248 L330 248"
      style="stroke:var(--jm-positive);stroke-width:2.5;fill:none"
      marker-end="url(#arrOK)"/>
```

Labeled box, plain and highlighted (highlight = accent stroke + a wash of
the same accent at low opacity, which works in both themes):

```html
<rect x="20" y="92" width="210" height="158" rx="10"
      style="fill:var(--jm-surface);stroke:var(--jm-border);stroke-width:1.5"/>
<text x="125" y="124" text-anchor="middle"
      style="font-size:15px;font-weight:700;letter-spacing:.08em;fill:var(--jm-muted)">PLAIN</text>

<rect x="530" y="92" width="210" height="158" rx="10"
      style="fill:var(--jm-accent);fill-opacity:.09;stroke:var(--jm-accent);stroke-width:2.5"/>
<text x="635" y="124" text-anchor="middle"
      style="font-size:15px;font-weight:700;letter-spacing:.08em;fill:var(--jm-accent)">HIGHLIGHTED</text>
```

Split / stacked horizontal bar (two slices of one quantity). Labels sit
outside the bar in `--jm-text` / `--jm-muted` per the contrast rule
above, which also frees you to soften the fills with `fill-opacity`
(blending toward the surface only matters under inside labels):

```html
<text x="244" y="40" text-anchor="middle" style="font-size:15px;font-weight:700;fill:var(--jm-text)">$6,000</text>
<text x="586" y="40" text-anchor="middle" style="font-size:15px;font-weight:700;fill:var(--jm-text)">$4,000</text>
<rect x="40"  y="52" width="408" height="66" rx="8" style="fill:var(--jm-chart-2);fill-opacity:.85"/>
<rect x="452" y="52" width="268" height="66" rx="8" style="fill:var(--jm-chart-8);fill-opacity:.85"/>
<text x="244" y="140" text-anchor="middle" style="font-size:12px;fill:var(--jm-muted)">value Japan already taxed</text>
<text x="586" y="140" text-anchor="middle" style="font-size:12px;fill:var(--jm-muted)">appreciation in Bhutan</text>
```

Timeline (axis, colored nodes, stacked label lines under each node):

```html
<path d="M70 64 L690 64" style="stroke:var(--jm-border);stroke-width:2;fill:none"/>
<circle cx="130" cy="64" r="7" style="fill:var(--jm-chart-2)"/>
<text x="130" y="94"  text-anchor="middle" style="font-size:13.5px;font-weight:700;fill:var(--jm-text)">2024 · receipt</text>
<text x="130" y="113" text-anchor="middle" style="font-size:12px;fill:var(--jm-text)">80 shares, FMV $6,000</text>
```

Hatched segment (a "lost / stranded / unusable" visual for part of a bar):

```html
<defs>
  <pattern id="strand" width="7" height="7" patternTransform="rotate(45)"
           patternUnits="userSpaceOnUse">
    <rect width="7" height="7" style="fill:var(--jm-negative);fill-opacity:.18"/>
    <line x1="0" y1="0" x2="0" y2="7" style="stroke:var(--jm-negative);stroke-width:2"/>
  </pattern>
</defs>
<rect x="230" y="240" width="100" height="45"
      style="fill:url(#strand);stroke:var(--jm-negative);stroke-width:1.5"/>
```

Comparison bars with a dashed shared-level line:

```html
<rect x="230" y="285" width="100" height="100" style="fill:var(--jm-chart-2);fill-opacity:.85"/>
<rect x="430" y="285" width="100" height="100" style="fill:var(--jm-chart-1);fill-opacity:.85"/>
<path d="M215 285 L545 285"
      style="stroke:var(--jm-border);stroke-width:1.2;stroke-dasharray:5 5;fill:none"/>
```

## Verifying the result

Render, open, screenshot, and look — there is no lint for visual overflow:

```
jimemo check out.html
agent-browser open "file://$PWD/out.html"    # or any browser
agent-browser screenshot --full page.png
```

On a long page, crop bands around each figure before judging text fit
(`sips --cropOffset <y> 0 -c <height> <width> page.png --out crop.png` on
macOS). Check the right viewBox edge of every caption — that is where
overflow hides.
