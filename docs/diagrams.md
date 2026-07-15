# Diagrams in jimemo pages (inline SVG)

jimemo templates have no diagram slot, and markdown-typed slots pass
through the allowlist sanitizer — inline SVG written into a content file
will not survive rendering. That is deliberate: content is untrusted.
The supported route for diagrams is the draft loop (render, hand-edit the
output, `jimemo check`), with the patterns below. They were worked out on
a real page (a tax-mechanics explainer with four diagrams) and are the
difference between an SVG that fights the page and one that looks native.

## Workflow: placeholder → splice → check

1. In the content file, put a one-word placeholder paragraph where each
   diagram belongs:

   ```markdown
   Some prose introducing the figure.

   [[DIAGRAM:BASKETS]]

   Prose that refers back to it.
   ```

2. `jimemo render` as usual. Each placeholder comes out as an easy-to-find
   `<p>[[DIAGRAM:BASKETS]]</p>` in the output HTML.

3. Replace each placeholder paragraph with a `<figure>` containing
   hand-written inline SVG (snippets below).

4. Re-verify: `jimemo check out.html`. Hand-tweaked files must re-pass the
   self-containment check; inline SVG passes as long as it references no
   external images or fonts. `publish` and `pdf` re-run the same check.

Re-rendering the content file overwrites the spliced diagrams — keep the
SVG in a scratch file (or regenerate it) if you expect to re-render.

## Why inline SVG, not `<img>`

The page's CSS custom properties cascade into inline SVG, so a diagram
colored with `var(--jm-*)` tokens follows light and dark mode for free.
An `<img>` with a data-URI SVG is an isolated document: page tokens do
not reach it, and it would need hard-coded colors that break in one of
the two themes.

## Rules that make an SVG look native

- **Root element.** Fixed `viewBox`, fluid width, page UI font:

  ```html
  <figure style="margin:2.2rem 0">
  <svg viewBox="0 0 760 340" role="img" aria-label="One-sentence description
    of what the diagram shows, for screen readers and PDF text extraction."
    style="width:100%;height:auto;font-family:var(--jm-font-ui)">
    ...
  </svg>
  </figure>
  ```

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

- **Labels on colored fills need a per-theme contrast check.** Some
  `--jm-chart-*` tokens are dark enough in both themes for bold white
  `#ffffff` labels (`--jm-chart-1` blue, `--jm-chart-2` green,
  `--jm-chart-8` orange in the default theme). Others go light in one
  theme or both (`--jm-chart-3` amber; the dark-theme values of
  `--jm-chart-5` / `--jm-chart-7`), where white text washes out — and a
  custom `--theme` can reshuffle all of them. Check the token's light
  AND dark values in the rendered page's CSS before writing white on
  it. On `--jm-accent` fills use `fill:var(--jm-accent-contrast)`,
  which flips per theme for exactly this purpose. When in doubt, put
  the label outside the shape in `fill:var(--jm-text)`. Everywhere
  else, use `--jm-text` / `--jm-muted`, never a literal gray.

- **SVG text never wraps, and nothing detects overflow.** Break long
  labels into separate `<text>` (or `<tspan>`) lines yourself. Budget
  roughly 6 px per character at `font-size:12.5px` in the UI font — about
  90 characters across a 760-wide viewBox. The classic failure is a
  caption running off the right viewBox edge, and `jimemo check` cannot
  catch it: verify visually (screenshot the rendered file; crop to the
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

Split / stacked horizontal bar (two slices of one quantity; white labels
work here because `--jm-chart-2` / `--jm-chart-8` stay dark in both
themes — see the contrast rule above):

```html
<rect x="40"  y="52" width="408" height="66" rx="8" style="fill:var(--jm-chart-2);fill-opacity:.85"/>
<rect x="452" y="52" width="268" height="66" rx="8" style="fill:var(--jm-chart-8);fill-opacity:.85"/>
<text x="244" y="80" text-anchor="middle" style="font-size:15px;font-weight:700;fill:#ffffff">$6,000</text>
<text x="586" y="80" text-anchor="middle" style="font-size:15px;font-weight:700;fill:#ffffff">$4,000</text>
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
