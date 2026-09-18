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
from pathlib import Path
from typing import Any, Dict, List, Optional

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
from .sanitize import sanitize_svg_with_ids

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


def _splice_figures(html: str, figures: Dict[str, str]) -> str:
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
    DIFFERENT figures define the same ``id``: inline <svg> roots share
    the page's single id namespace, so figure B's ``url(#grad)`` would
    resolve to figure A's gradient and render wrong without any error.
    One figure spliced at several placeholders repeats identical
    definitions, which resolve identically; that is allowed."""
    sanitized: Dict[str, str] = {}
    id_owner: Dict[str, str] = {}
    for name, svg_text in figures.items():
        try:
            svg, ids = sanitize_svg_with_ids(svg_text)
        except ValueError as e:
            raise ContentError(f"--figure {name}: {e}") from e
        for svg_id in ids:
            owner = id_owner.setdefault(svg_id, name)
            if owner != name:
                raise ContentError(
                    f"--figure {owner} and --figure {name} both define "
                    f"id={svg_id!r}; "
                    "inline SVG shares the page's one id namespace, so "
                    f"url(#{svg_id}) would resolve to the wrong figure — "
                    "give each figure's ids a distinct prefix"
                )
        sanitized[name] = svg

    # Every placeholder is looked up in the page as rendered, before any
    # figure lands, so text inside one figure can never stand in for
    # another figure's placeholder.
    for name in sanitized:
        if FIGURE_PLACEHOLDER.format(name=name) in html:
            continue
        if f"[[DIAGRAM:{name}]]" in html:
            # The text is there, but not as the exact bare paragraph the
            # splice replaces. Say so: the usual cause is invisible in
            # the source (a trailing space renders as "…]] </p>").
            raise ContentError(
                f"--figure {name}: [[DIAGRAM:{name}]] is in the rendered "
                "page, but not as a paragraph of its own — remove any "
                "other text or trailing spaces on its line, and do not "
                "put it in a heading or a list item (see docs/diagrams.md)"
            )
        raise ContentError(
            f"--figure {name}: placeholder [[DIAGRAM:{name}]] not found "
            "in the rendered page — it must be a paragraph of its own "
            "in a markdown slot (see docs/diagrams.md)"
        )
    for name, svg in sanitized.items():
        html = html.replace(
            FIGURE_PLACEHOLDER.format(name=name),
            FIGURE_OPEN + svg + "</figure>",
        )
    return html


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
    what they were before the parameter existed.

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

    if figures:
        html = _splice_figures(html, figures)

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

    for w in [*img_warnings, *warnings]:
        print(f"warning: {w}", file=sys.stderr)

    return html


def write_output(html: str, out_path: Path) -> None:
    out_path = Path(out_path)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
    except OSError as e:
        raise ContentError(f"cannot write output file {out_path}: {e}") from e
