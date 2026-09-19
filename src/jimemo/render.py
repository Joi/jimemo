"""Render a manifest-defined template + parsed content into a single,
self-contained HTML page: Jinja2 render -> image inlining -> lint
(fail closed on errors, warn to stderr otherwise).

Charts: when the manifest declares charts, the renderer injects two
extra context names — ``chart_lib`` (the vendored Chart.js source,
emitted once by the page skeleton as the single library <script>) and
``charts`` (one entry per declaration: id, type, title, and the full
init-script body built by charts.chart_init_js as ``init_js``, the only
value the chart macro may be called with). The renderer then hands lint
the exact bodies it emitted (library + every init), and lint requires
the page's inline scripts to equal that set — nothing forged, extra,
duplicated, or missing. A chartless manifest injects neither name,
leaving chartless no-script output byte-identical.
"""
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ._paths import CHARTJS_BUNDLE, REPO_ROOT
from ._vendor import add_vendor_to_path
from .charts import (
    build_chart_config,
    chart_init_js,
    chart_lib_inline_text,
    serialize_chart_config,
)
from .errors import ContentError
from .inline import assemble_css, inline_images
from .lint import lint_html
from .manifest import load_manifest
from .sanitize import SvgDrop, _svg_drop_label, sanitize_svg_with_report

add_vendor_to_path()
from jinja2 import (  # noqa: E402
    Environment,
    FileSystemLoader,
    StrictUndefined,
    TemplateError,
    UndefinedError,
)
from markupsafe import Markup  # noqa: E402

TOOLKIT_DIR = REPO_ROOT / "toolkit"
TEMPLATE_FILENAME = "template.html.j2"


def _chart_lib() -> Markup:
    """The vendored Chart.js source, ready to emit verbatim inside the
    page skeleton's library <script>. This is our pinned, checksummed
    file (doctor verifies it), not content — hence Markup.
    chart_lib_inline_text (charts.py) is the single function that reads
    and prepares this text — it also strips the bundle's trailing
    sourceMappingURL comment and re-checks the inline-safety invariant
    (script element text must not be able to close the element or open
    an HTML comment) — and lint.py's script-body allowlist calls the
    same function, so the two can never drift on what "the inlined
    library" is."""
    try:
        lib = chart_lib_inline_text(CHARTJS_BUNDLE)
    except OSError as e:
        raise ContentError(
            f"cannot read vendored Chart.js at {CHARTJS_BUNDLE}: {e} "
            "(run 'jimemo doctor')"
        ) from e
    return Markup(lib)


def _charts_context(
    manifest: Dict[str, Any], content: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """One entry per manifest chart declaration, each carrying the full
    breakout-safe init-script body the chart macro embeds verbatim
    (Markup-wrapped: serialize_chart_config already u003c-escaped every
    "<" in the config, and autoescaping the body would corrupt it).
    chart_init_js builds the exact bytes; lint.py recognizes exactly
    that shape and rejects every other inline script on a chart page."""
    charts: List[Dict[str, Any]] = []
    for decl in manifest["charts"]:
        data_slot = decl["data_slot"]
        if data_slot not in content:
            raise ContentError(
                f"chart {decl['id']!r} reads data slot {data_slot!r}, "
                "but the content file provides no value for it"
            )
        try:
            config = build_chart_config(decl, content[data_slot])
        except ContentError as e:
            raise ContentError(
                f"chart {decl['id']!r} (data slot {data_slot!r}): {e}"
            ) from e
        charts.append({
            "id": decl["id"],
            "type": decl["type"],
            # Jinja's |default filter only fires on Undefined, not None,
            # so a missing/empty title must fall back to the chart id
            # here rather than relying on the template's default(c.id).
            "title": decl.get("title") or decl["id"],
            "init_js": Markup(
                chart_init_js(decl["id"], serialize_chart_config(config))
            ),
        })
    return charts


FIGURE_PLACEHOLDER = "<p>[[DIAGRAM:{name}]]</p>"

# The wrapper every spliced figure gets. `contain:paint` is the structural
# half of the sanitizer's "nothing leaves the figure" rule: `style` is kept
# on SVG elements (theming needs it), so a figure could declare
# `position:fixed;width:100vw;height:100vh` and paint over the whole page.
# Paint containment makes the <figure> the containing block for fixed and
# absolute descendants and clips their painting to its box — measured in
# Chromium: the same SVG goes from covering the viewport to being confined
# to the figure. Inline rather than in toolkit CSS on purpose: a stylesheet
# change would alter every page, and pages without figures must stay
# byte-identical.
FIGURE_OPEN = '<figure class="jm-figure" style="contain:paint">'

# Detail warning lines per figure before the rest is summarized in one
# line: a hostile figure with thousands of distinct unknown element names
# must not fill the author's terminal.
FIGURE_DROP_WARNINGS_MAX = 20


class _IdCollector(HTMLParser):
    """Every ``id`` attribute value in a page, read from parsed start
    tags (text that merely looks like ``id="x"``, and script bodies, are
    not attributes and are not collected)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "id" and value:
                self.ids.add(value)

    handle_startendtag = handle_starttag


def _page_ids(html: str) -> set:
    collector = _IdCollector()
    try:
        # A browser's HTML tokenizer turns U+0000 into U+FFFD; html.parser
        # passes it through. Normalize before the parse, exactly as
        # sanitize_svg_with_report does for a figure, so an anchor
        # id="g\x00" and a figure id="g\ufffd" compare equal here as they
        # do in the page. Only the collector's view changes: the rendered
        # page keeps whatever sanitize_html produced.
        collector.feed(html.replace("\x00", "\ufffd"))
        collector.close()
    except Exception as e:  # noqa: BLE001 - older html.parser raises assorted types
        # Fail closed: without the page's ids the collision check below
        # would silently not run.
        raise ContentError(
            f"--figure: could not read the rendered page's ids: {e}"
        ) from e
    return collector.ids


def _figure_drop_warnings(name: str, drops: List[SvgDrop]) -> List[str]:
    """The stderr warning lines for one figure's sanitizer drops: one per
    distinct drop, at most FIGURE_DROP_WARNINGS_MAX, then one summary line
    if more remain. The figure NAME goes through the same display filter as
    the dropped names (sanitize._svg_drop_label): it is CLI input an agent
    writes, and it shares the line with them. No dropped VALUE appears —
    the report does not carry one."""
    label = _svg_drop_label(name)
    lines = [
        f"figure {label}: dropped {d.kind} {d.name} ({d.reason})"
        for d in drops[:FIGURE_DROP_WARNINGS_MAX]
    ]
    hidden = len(drops) - FIGURE_DROP_WARNINGS_MAX
    if hidden > 0:
        noun = "drop" if hidden == 1 else "drops"
        lines.append(f"figure {label}: {hidden} more distinct {noun} not shown")
    return lines


def _splice_figures(html: str, figures: Dict[str, str]) -> Tuple[str, List[str]]:
    """`html` with every ``<p>[[DIAGRAM:NAME]]</p>`` placeholder
    paragraph replaced by FIGURE_OPEN + the sanitized SVG for NAME +
    ``</figure>`` (`jimemo render --figure NAME=file.svg`;
    docs/diagrams.md). `figures` maps NAME to RAW, untrusted SVG text.

    Runs on the rendered page, so markdown sanitization has already
    happened — the placeholder is a paragraph sanitize_html let through
    as plain text — and BEFORE lint_html, which still judges the final
    page as the second guard. The SVG is rebuilt by sanitize_svg first;
    nothing raw is ever spliced. Plain ``str.replace`` on an exact
    string: no regex over the page.

    Raises ContentError, always before any replacement is made, when
    a figure is not acceptable SVG (named), when a NAME has no
    placeholder in the page — never a silent no-op — and when two
    DIFFERENT figures define the same ``id``, or a figure defines an id
    the page already uses: inline <svg> roots share the page's single id
    namespace, so figure B's ``url(#grad)`` would resolve to figure A's
    gradient and render wrong without any error — and a figure element
    that takes a chart canvas's id comes first in the document, so the
    chart's ``getElementById`` finds the SVG element and the chart never
    draws. One figure spliced at several placeholders repeats identical
    definitions, which resolve identically; that is allowed.

    Returns ``(html, warnings)``: `warnings` holds one line per distinct
    element or attribute the sanitizer dropped (_figure_drop_warnings), for
    render_page to print — the drops are silent otherwise, and a refused
    style shows up only as a wrongly painted shape.

    Every message here names a figure by its DISPLAY LABEL
    (sanitize._svg_drop_label), as the warnings do: a NAME is written by
    whoever ran the tool and an id comes from the untrusted figure file, and
    both land on a terminal. An id is ALSO printed with ``!r``, which is
    exact and escapes what a terminal would act on; the ``url(#…)`` form
    beside it is the label, because that one is read as the CSS it shows."""
    sanitized: Dict[str, str] = {}
    id_owner: Dict[str, str] = {}
    warnings: List[str] = []
    page_ids = _page_ids(html)
    for name, svg_text in figures.items():
        label = _svg_drop_label(name)
        try:
            svg, ids, drops = sanitize_svg_with_report(svg_text)
        except ValueError as e:
            raise ContentError(f"--figure {label}: {e}") from e
        warnings.extend(_figure_drop_warnings(name, drops))
        for svg_id in ids:
            if svg_id in page_ids:
                raise ContentError(
                    f"--figure {label} defines id={svg_id!r}, which the page "
                    "already uses (a heading anchor or a chart); inline SVG "
                    "shares the page's one id namespace — give the figure's "
                    "ids a distinct prefix"
                )
            owner = id_owner.setdefault(svg_id, name)
            if owner != name:
                raise ContentError(
                    f"--figure {_svg_drop_label(owner)} and --figure {label} "
                    f"both define id={svg_id!r}; "
                    "inline SVG shares the page's one id namespace, so "
                    f"url(#{_svg_drop_label(svg_id)}) would resolve to the "
                    "wrong figure — give each figure's ids a distinct prefix"
                )
        sanitized[name] = svg

    # Every placeholder is looked up in the page as rendered, before any
    # figure lands, so text inside one figure can never stand in for
    # another figure's placeholder.
    for name in sanitized:
        if FIGURE_PLACEHOLDER.format(name=name) in html:
            continue
        # The lookups above use the raw NAME — the page holds the real
        # placeholder, not a label — and only the messages below show it.
        label = _svg_drop_label(name)
        if f"[[DIAGRAM:{name}]]" in html:
            # The text is there, but not as the exact bare paragraph the
            # splice replaces. Say so: the usual cause is invisible in
            # the source (a trailing space renders as "…]] </p>").
            raise ContentError(
                f"--figure {label}: [[DIAGRAM:{label}]] is in the rendered "
                "page, but not as a paragraph of its own — remove any "
                "other text or trailing spaces on its line, and do not "
                "put it in a heading or a list item (see docs/diagrams.md)"
            )
        raise ContentError(
            f"--figure {label}: placeholder [[DIAGRAM:{label}]] not found "
            "in the rendered page — it must be a paragraph of its own "
            "in a markdown slot (see docs/diagrams.md)"
        )
    for name, svg in sanitized.items():
        html = html.replace(
            FIGURE_PLACEHOLDER.format(name=name),
            FIGURE_OPEN + svg + "</figure>",
        )
    return html, warnings


def render_page(
    template_dir: Path,
    content: Dict[str, Any],
    theme: Optional[str] = None,
    *,
    base_dir: Optional[Path] = None,
    figures: Optional[Dict[str, str]] = None,
) -> str:
    """Full HTML string (assembled + inlined) for `content` rendered
    through the template in `template_dir`. `base_dir` is the directory
    local <img> paths in content are resolved against (the content
    file's parent); it defaults to the current working directory when
    omitted, which is only correct if content carries no local images.
    `figures` maps a ``[[DIAGRAM:NAME]]`` placeholder NAME to raw SVG
    text to splice in its place, sanitized (see _splice_figures); None
    or empty runs no figure code at all, so such pages are byte-for-byte
    what they were before the parameter existed. Whatever the sanitizer
    drops from a figure is reported as ``warning: figure NAME: …`` lines
    on stderr, with the other warnings.

    Raises ContentError if lint finds a hard error (any resource
    reference outside lint's self-contained allowlist, script tags where
    the manifest declares no charts, any <script src>, or a chart page
    whose inline scripts are not exactly the ones this renderer emitted
    for it — forged, extra, duplicated, or missing bodies all) — callers
    must not write output in that case — and for chart data that is
    missing or does not fit the {labels, series} contract (see charts.py).
    """
    template_dir = Path(template_dir)
    manifest = load_manifest(template_dir)

    env = Environment(
        loader=FileSystemLoader([str(template_dir), str(TOOLKIT_DIR)]),
        autoescape=True,
        undefined=StrictUndefined,
    )
    try:
        template = env.get_template(TEMPLATE_FILENAME)
    except TemplateError as e:
        # Missing template.html.j2, or one with a syntax error: surface
        # as the domain error the CLI already prints cleanly, naming the
        # template so the author knows what to fix.
        raise ContentError(
            f"template {TEMPLATE_FILENAME!r} in {template_dir} could not "
            f"be loaded: {e}"
        ) from e

    styles = Markup("<style>\n" + assemble_css(manifest, theme) + "\n</style>")

    context: Dict[str, Any] = dict(content)
    context["manifest"] = manifest
    context["styles"] = styles
    context["theme"] = theme

    # Chartless manifests inject NOTHING here — their rendered output
    # is byte-identical to the chartless form (the goldens pin this). The
    # charts/chart_lib slot-name collision is validated authoritatively
    # in load_manifest (manifest.py), which this function has already
    # called above to obtain `manifest` — so it cannot reach this point
    # uncaught, and re-checking it here would be dead code.
    allowed_scripts: Optional[List[str]] = None
    if manifest["charts"]:
        context["chart_lib"] = _chart_lib()
        context["charts"] = _charts_context(manifest, content)
        # The renderer is the source of truth for what inline script a
        # chart page may carry: exactly the library body and each
        # chart's init body — the very values injected into the context
        # above. lint_html gets this list and requires the page's
        # inline scripts to EQUAL it, so a template cannot add, drop,
        # duplicate, or forge a script body — not even one that apes
        # the init shape for a declared id with different config bytes,
        # which lint's structural fallback alone could not tell apart.
        allowed_scripts = [str(context["chart_lib"])] + [
            str(chart["init_js"]) for chart in context["charts"]
        ]

    try:
        html = template.render(**context)
    except UndefinedError as e:
        # StrictUndefined raises on any unknown name; surface it as the
        # domain error the CLI already prints cleanly (no traceback).
        raise ContentError(f"template referenced an undefined value: {e}") from e
    except TemplateError as e:
        raise ContentError(
            f"template {TEMPLATE_FILENAME!r} in {template_dir} failed to "
            f"render: {e}"
        ) from e

    html, img_warnings = inline_images(html, Path(base_dir) if base_dir else Path.cwd())

    # Defined on both paths: a page without --figure runs no figure code
    # and prints exactly the warnings it printed before.
    figure_warnings: List[str] = []
    if figures:
        html, figure_warnings = _splice_figures(html, figures)

    errors, warnings = lint_html(html, manifest, allowed_scripts=allowed_scripts)
    if errors:
        message = "; ".join(errors)
        if figures:
            # Lint judges the whole page and cannot say which part an
            # error came from; point at the one new input.
            message += (
                " (this page includes --figure SVG: see 'What the "
                "sanitizer removes' in docs/diagrams.md)"
            )
        raise ContentError(message)

    for w in [*img_warnings, *figure_warnings, *warnings]:
        print(f"warning: {w}", file=sys.stderr)

    return html


def write_output(html: str, out_path: Path) -> None:
    out_path = Path(out_path)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
    except OSError as e:
        raise ContentError(f"cannot write output file {out_path}: {e}") from e
