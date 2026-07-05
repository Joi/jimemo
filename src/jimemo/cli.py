import argparse
import sys
import webbrowser
from pathlib import Path

from . import __version__
from ._vendor import VENDOR_DIR, add_vendor_to_path
from .checksums import verify_checksums
from .content import load_content
from .discovery import default_search_dirs, find_templates
from .errors import ContentError, ManifestError
from .manifest import load_manifest
from .render import render_page, write_output

PYTHON_FLOOR = (3, 9)


def cmd_doctor(args) -> int:
    ok = True

    v = sys.version_info
    if v >= PYTHON_FLOOR:
        print(f"ok   python {v.major}.{v.minor}.{v.micro}")
    else:
        print(f"FAIL python {v.major}.{v.minor} < required "
              f"{PYTHON_FLOOR[0]}.{PYTHON_FLOOR[1]}")
        ok = False

    problems = verify_checksums(VENDOR_DIR)
    if problems:
        for p in problems:
            print(f"FAIL vendor: {p}")
        ok = False
    else:
        print(f"ok   vendor checksums ({VENDOR_DIR})")

    if problems:
        print("skip vendored imports (checksum verification failed)")
    else:
        add_vendor_to_path()
        try:
            import jinja2  # noqa: F401
            import markdown  # noqa: F401
            print("ok   vendored imports (jinja2, markdown)")
        except ImportError as e:
            print(f"FAIL vendored imports: {e}")
            ok = False

    return 0 if ok else 1


def cmd_list(args) -> int:
    found = find_templates(default_search_dirs())
    if not found:
        print("no templates installed yet "
              "(repo templates/ and ~/.jimemo/templates/ are empty)")
        return 0
    for name, path in found:
        print(f"{name}\t{path}")
    return 0


def cmd_render(args) -> int:
    if args.template == "auto":
        print("render auto requires suggest (coming in this phase)", file=sys.stderr)
        return 2

    templates = dict(find_templates(default_search_dirs()))
    template_dir = templates.get(args.template)
    if template_dir is None:
        print(f"unknown template: {args.template!r}", file=sys.stderr)
        return 1

    content_path = Path(args.content)
    if not content_path.is_file():
        print(f"content file not found: {content_path}", file=sys.stderr)
        return 1

    try:
        manifest = load_manifest(template_dir)
        content = load_content(content_path, manifest)
        html = render_page(
            template_dir,
            content,
            args.theme,
            base_dir=content_path.resolve().parent,
        )
    except (ManifestError, ContentError) as e:
        print(str(e), file=sys.stderr)
        return 1

    out_path = Path(args.out) if args.out else Path("dist") / f"{content_path.stem}.html"
    write_output(html, out_path)
    print(f"wrote {out_path}")

    if args.open:
        webbrowser.open(out_path.resolve().as_uri())

    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="jimemo",
        description="Self-contained single-file HTML pages from templates.",
    )
    parser.add_argument("--version", action="version",
                        version=f"jimemo {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("doctor", help="check environment and vendor integrity")
    sub.add_parser("list", help="list available templates")

    render_p = sub.add_parser("render", help="render a template + content file to HTML")
    render_p.add_argument("template", help='template name, or "auto" to pick automatically')
    render_p.add_argument("content", help="content file (.md, .json, or .yaml)")
    render_p.add_argument("-o", "--out", help="output path (default: dist/<content-stem>.html)")
    render_p.add_argument("--theme", help='pin "light" or "dark" (default: follow the OS)')
    render_p.add_argument("--open", action="store_true", help="open the result in a browser")

    args = parser.parse_args(argv)

    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "list":
        return cmd_list(args)
    if args.command == "render":
        return cmd_render(args)

    parser.print_usage(sys.stderr)
    return 2
