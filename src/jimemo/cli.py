import argparse
import hashlib
import json
import sys
import webbrowser
from pathlib import Path

from . import __version__
from ._vendor import VENDOR_DIR, add_vendor_to_path
from .checksums import verify_checksums
from .content import load_content
from .discovery import default_search_dirs, find_templates
from .errors import ContentError, ManifestError, ScaffoldError
from .manifest import load_manifest
from .render import render_page, write_output
from .scaffold import create_template

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


def _sample_files(template_dir: Path) -> list:
    sample_dir = template_dir / "sample"
    if not sample_dir.is_dir():
        return []
    return sorted(
        str(p.relative_to(template_dir)) for p in sample_dir.rglob("*") if p.is_file()
    )


def _labels_status(manifest, template_dir: Path) -> str:
    labeled_hash = manifest.get("suitability", {}).get("labeled_hash")
    template_path = template_dir / "template.html.j2"
    if not labeled_hash or not template_path.is_file():
        return "(no labeled_hash recorded)"
    actual_hash = hashlib.sha256(template_path.read_bytes()).hexdigest()
    if actual_hash == labeled_hash:
        return "fresh"
    return "stale (template.html.j2 changed since suitability labels were written)"


def _print_info_human(manifest, template_dir: Path, sample_files: list) -> None:
    print(f"{manifest['name']} — {manifest['title']}")
    if manifest.get("description"):
        print(manifest["description"])
    print()

    print("Slots:")
    for slot_name, slot in manifest["slots"].items():
        required = "required" if slot.get("required") else ""
        print(f"  {slot_name:<16} {slot['type']:<9} {required}".rstrip())
    print()

    print(f"Components: {', '.join(manifest['components']) or '(none)'}")
    print(f"Charts: {', '.join(manifest['charts']) or '(none)'}")
    print()

    suitability = manifest.get("suitability", {})
    print("Suitability:")
    print(f"  keywords: {', '.join(suitability.get('keywords', [])) or '(none)'}")
    print(f"  content_kinds: {', '.join(suitability.get('content_kinds', [])) or '(none)'}")
    print(f"  good_for: {suitability.get('good_for') or '(none)'}")
    print(f"  labels: {_labels_status(manifest, template_dir)}")
    print()

    print(f"Template dir: {template_dir}")
    if sample_files:
        print("Sample files:")
        for f in sample_files:
            print(f"  {f}")
    else:
        print("Sample files: (none)")


def cmd_info(args) -> int:
    templates = dict(find_templates(default_search_dirs()))
    template_dir = templates.get(args.template)
    if template_dir is None:
        available = ", ".join(sorted(templates)) if templates else "none"
        print(
            f"unknown template: {args.template!r} (available: {available})",
            file=sys.stderr,
        )
        return 1

    try:
        manifest = load_manifest(template_dir)
    except ManifestError as e:
        print(str(e), file=sys.stderr)
        return 1

    sample_files = _sample_files(template_dir)

    if args.json:
        data = dict(manifest)
        data["template_dir"] = str(template_dir)
        data["sample_files"] = sample_files
        print(json.dumps(data, indent=2))
        return 0

    _print_info_human(manifest, template_dir, sample_files)
    return 0


def cmd_new_template(args) -> int:
    try:
        template_dir = create_template(args.name)
    except ScaffoldError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"created {template_dir}")
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

    info_p = sub.add_parser("info", help="show a template's manifest and suitability")
    info_p.add_argument("template", help="template name")
    info_p.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    new_template_p = sub.add_parser("new-template", help="scaffold a new personal template")
    new_template_p.add_argument("name", help="template name (lowercase letters, digits, hyphens)")

    args = parser.parse_args(argv)

    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "list":
        return cmd_list(args)
    if args.command == "render":
        return cmd_render(args)
    if args.command == "info":
        return cmd_info(args)
    if args.command == "new-template":
        return cmd_new_template(args)

    parser.print_usage(sys.stderr)
    return 2
