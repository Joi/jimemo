"""Render a manifest-defined template + parsed content into a single,
self-contained HTML page: Jinja2 render -> image inlining -> lint
(fail closed on errors, warn to stderr otherwise).
"""
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from ._paths import REPO_ROOT
from ._vendor import add_vendor_to_path
from .errors import ContentError
from .inline import assemble_css, inline_images
from .lint import lint_html
from .manifest import load_manifest

add_vendor_to_path()
from jinja2 import Environment, FileSystemLoader, StrictUndefined, UndefinedError  # noqa: E402
from markupsafe import Markup  # noqa: E402

TOOLKIT_DIR = REPO_ROOT / "toolkit"
TEMPLATE_FILENAME = "template.html.j2"


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

    Raises ContentError if lint finds a hard error (script tags where
    the manifest declares no charts, or any external <script src>) —
    callers must not write output in that case.
    """
    template_dir = Path(template_dir)
    manifest = load_manifest(template_dir)

    env = Environment(
        loader=FileSystemLoader([str(template_dir), str(TOOLKIT_DIR)]),
        autoescape=True,
        undefined=StrictUndefined,
    )
    template = env.get_template(TEMPLATE_FILENAME)

    styles = Markup("<style>\n" + assemble_css(manifest, theme) + "\n</style>")

    context: Dict[str, Any] = dict(content)
    context["manifest"] = manifest
    context["styles"] = styles
    context["theme"] = theme

    try:
        html = template.render(**context)
    except UndefinedError as e:
        # StrictUndefined raises on any unknown name; surface it as the
        # domain error the CLI already prints cleanly (no traceback).
        raise ContentError(f"template referenced an undefined value: {e}") from e

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
