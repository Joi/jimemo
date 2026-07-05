"""Render a manifest-defined template + parsed content into a single,
self-contained HTML page: Jinja2 render -> image inlining -> lint
(fail closed on errors, warn to stderr otherwise).

Charts: when the manifest declares charts, the renderer injects two
extra context names — ``chart_lib`` (the vendored Chart.js source,
emitted once by the page skeleton as the single library <script>) and
``charts`` (one entry per declaration: id, type, title, and the
serialize_chart_config output as ``config_json``, the only value the
chart macro may be called with). A chartless manifest injects neither
name, leaving the Phase 3 no-script output byte-identical.
"""
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._paths import CHARTS_VENDOR_DIR, REPO_ROOT
from ._vendor import add_vendor_to_path
from .charts import build_chart_config, serialize_chart_config
from .errors import ContentError, ManifestError
from .inline import assemble_css, inline_images
from .lint import lint_html
from .manifest import load_manifest

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
CHARTJS_BUNDLE = CHARTS_VENDOR_DIR / "chartjs" / "chart.umd.min.js"

# Context names render_page injects only when the manifest declares
# charts. manifest.py's RESERVED_SLOT_NAMES predates charts and does not
# cover these, so the collision check lives here: a slot with one of
# these names would be silently shadowed on chart pages.
_CHART_CONTEXT_NAMES = ("charts", "chart_lib")


def _chart_lib() -> Markup:
    """The vendored Chart.js source, ready to emit verbatim inside the
    page skeleton's library <script>. This is our pinned, checksummed
    file (doctor verifies it), not content — hence Markup."""
    try:
        lib = CHARTJS_BUNDLE.read_text(encoding="utf-8")
    except OSError as e:
        raise ContentError(
            f"cannot read vendored Chart.js at {CHARTJS_BUNDLE}: {e} "
            "(run 'jimemo doctor')"
        ) from e
    # Inline-safety invariant, checked at every render as defense in
    # depth beyond the checksum: script element text must not be able
    # to close the element or open an HTML comment. The pinned bundle
    # contains neither sequence.
    if "</script" in lib.lower() or "<!--" in lib:
        raise ContentError(
            f"vendored Chart.js at {CHARTJS_BUNDLE} contains '</script' "
            "or '<!--' and cannot be inlined safely"
        )
    return Markup(lib)


def _charts_context(
    manifest: Dict[str, Any], content: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """One entry per manifest chart declaration, each carrying the
    breakout-safe serialized config the chart macro embeds verbatim
    (Markup-wrapped: serialize_chart_config already u003c-escaped every
    "<", and autoescaping it again would corrupt the JSON)."""
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
            "config_json": Markup(serialize_chart_config(config)),
        })
    return charts


def render_page(
    template_dir: Path,
    content: Dict[str, Any],
    theme: Optional[str] = None,
    *,
    base_dir: Optional[Path] = None,
) -> str:
    """Full HTML string (assembled + inlined) for `content` rendered
    through the template in `template_dir`. `base_dir` is the directory
    local <img> paths in content are resolved against (the content
    file's parent); it defaults to the current working directory when
    omitted, which is only correct if content carries no local images.

    Raises ContentError if lint finds a hard error (any resource
    reference outside lint's self-contained allowlist, script tags where
    the manifest declares no charts, or any <script src>) — callers must
    not write output in that case — and for chart data that is missing
    or does not fit the {labels, series} contract (see charts.py).
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
    # is byte-identical to Phase 3 (the goldens pin this).
    if manifest["charts"]:
        for name in _CHART_CONTEXT_NAMES:
            if name in manifest["slots"]:
                raise ManifestError(
                    f"slot name {name!r} collides with the render context "
                    "name injected for chart pages; rename the slot"
                )
        context["chart_lib"] = _chart_lib()
        context["charts"] = _charts_context(manifest, content)

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

    errors, warnings = lint_html(html, manifest)
    if errors:
        raise ContentError("; ".join(errors))

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
