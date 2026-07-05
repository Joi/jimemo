import argparse
import hashlib
import json
import re
import sys
import webbrowser
from pathlib import Path

from . import __version__
from ._paths import CHARTS_VENDOR_DIR
from ._vendor import VENDOR_DIR, add_vendor_to_path
from .checksums import verify_checksums
from .discovery import default_search_dirs, find_templates
from .errors import ContentError, ManifestError, ScaffoldError
from .manifest import load_manifest
from .scaffold import create_template

# render/content/suggest are NOT imported here: each transitively imports
# vendored jinja2/yaml/markdown at its own module top, and doctor (plus
# --version and list) must be able to run with zero vendored imports until
# after verify_checksums has passed. Each command handler that actually
# needs one of them imports it locally instead.

PYTHON_FLOOR = (3, 9)

_CHARTJS_VERSION_RE = re.compile(r"Chart\.js v([0-9]+\.[0-9]+\.[0-9]+)")


def _chartjs_version(charts_vendor_dir: Path) -> str:
    bundle = charts_vendor_dir / "chartjs" / "chart.umd.min.js"
    try:
        head = bundle.read_bytes()[:200].decode("utf-8", errors="replace")
    except OSError:
        return "unknown"
    m = _CHARTJS_VERSION_RE.search(head)
    return m.group(1) if m else "unknown"


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

    charts_problems = verify_checksums(CHARTS_VENDOR_DIR)
    if charts_problems:
        for p in charts_problems:
            print(f"FAIL charts: {p}")
        ok = False
    else:
        version = _chartjs_version(CHARTS_VENDOR_DIR)
        print(f"ok   charts vendored (chart.js {version})")

    if problems:
        print("skip vendored imports (checksum verification failed)")
    else:
        add_vendor_to_path()
        try:
            import jinja2  # noqa: F401
            import markdown  # noqa: F401
            import tomli  # noqa: F401
            import yaml  # noqa: F401
            print("ok   vendored imports (jinja2, markdown, yaml, tomli)")
        except ImportError as e:
            print(f"FAIL vendored imports: {e}")
            ok = False

    # Lazy: suggest.py itself defers its yaml import (see suggest.py), so
    # this import is vendor-free — importing it here, unconditionally,
    # does not compromise the checksum gate above.
    from .suggest import is_stale_labels

    stale_names = []
    for name, template_dir in find_templates(default_search_dirs()):
        try:
            manifest = load_manifest(template_dir)
        except ManifestError:
            continue
        if is_stale_labels(manifest, template_dir):
            stale_names.append(name)
    if stale_names:
        for name in stale_names:
            print(f"WARNING stale suitability labels: {name} "
                  "(template.html.j2 changed since labeling; re-run suggest tuning)")
    else:
        print("ok   suitability labels fresh (or none recorded)")

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


def _do_render(template_dir: Path, content_path: Path, args) -> int:
    from .content import load_content
    from .render import render_page, write_output

    out_path = Path(args.out) if args.out else Path("dist") / f"{content_path.stem}.html"
    try:
        manifest = load_manifest(template_dir)
        content = load_content(content_path, manifest)
        html = render_page(
            template_dir,
            content,
            args.theme,
            base_dir=content_path.resolve().parent,
        )
        write_output(html, out_path)
    except (ManifestError, ContentError) as e:
        print(str(e), file=sys.stderr)
        return 1

    print(f"wrote {out_path}")

    if args.open:
        webbrowser.open(out_path.resolve().as_uri())

    return 0


def cmd_render(args) -> int:
    content_path = Path(args.content)
    templates = find_templates(default_search_dirs())
    templates_by_name = dict(templates)

    if args.template == "auto":
        from .charts import build_chart_config
        from .content import load_content
        from .suggest import score_templates

        if not templates:
            print("no templates to choose from", file=sys.stderr)
            return 1
        if not content_path.is_file():
            print(f"content file not found: {content_path}", file=sys.stderr)
            return 1
        try:
            ranked, warnings = score_templates(content_path, templates)
        except ContentError as e:
            print(str(e), file=sys.stderr)
            return 1
        for warning in warnings:
            print(warning, file=sys.stderr)
        if not ranked:
            print("no usable templates", file=sys.stderr)
            return 1
        # Best score wins, but only among templates that can actually
        # take this content: the top scorer may require slots the
        # content doesn't have (or vice versa), so walk the ranking and
        # pick the first template whose manifest loads the content AND,
        # for every chart it declares, whose data-slot value actually
        # builds a chart config. Chart data slots are schema-free
        # (content.py passes them through unvalidated), so load_content
        # alone cannot tell a well-formed {labels, series} mapping from
        # a malformed one — without this second check a template could
        # be selected here and only fail later, mid-render, in
        # render_page instead of falling through to the next candidate.
        chosen = None
        chosen_idx = 0
        tried = []
        for idx, entry in enumerate(ranked):
            candidate_dir = templates_by_name[entry["name"]]
            try:
                candidate_manifest = load_manifest(candidate_dir)
                content = load_content(content_path, candidate_manifest)
                for chart_decl in candidate_manifest["charts"]:
                    build_chart_config(chart_decl, content[chart_decl["data_slot"]])
            except (ManifestError, ContentError) as e:
                tried.append(entry["name"])
                print(
                    f"auto: skipping {entry['name']} (content does not fit): {e}",
                    file=sys.stderr,
                )
                continue
            chosen = entry
            chosen_idx = idx
            break
        if chosen is None:
            print(
                "no template accepts this content; tried (best score first): "
                + ", ".join(tried),
                file=sys.stderr,
            )
            return 1
        tied = [r["name"] for r in ranked[chosen_idx + 1:] if r["score"] == chosen["score"]]
        reason = chosen["reasons"][0] if chosen["reasons"] else "no distinguishing signal; alphabetical default"
        if tied:
            print(
                f"auto-selected {chosen['name']} (tie broken alphabetically; "
                f"also tied: {', '.join(tied)}): {reason}",
                file=sys.stderr,
            )
        else:
            print(f"auto-selected {chosen['name']}: {reason}", file=sys.stderr)
        template_dir = templates_by_name[chosen["name"]]
    else:
        template_dir = templates_by_name.get(args.template)
        if template_dir is None:
            print(f"unknown template: {args.template!r}", file=sys.stderr)
            return 1
        if not content_path.is_file():
            print(f"content file not found: {content_path}", file=sys.stderr)
            return 1

    return _do_render(template_dir, content_path, args)


def cmd_suggest(args) -> int:
    from .suggest import score_templates

    content_path = Path(args.content)
    if not content_path.is_file():
        print(f"content file not found: {content_path}", file=sys.stderr)
        return 1

    templates = find_templates(default_search_dirs())
    try:
        ranked, warnings = score_templates(content_path, templates)
    except ContentError as e:
        print(str(e), file=sys.stderr)
        return 1

    for warning in warnings:
        print(warning, file=sys.stderr)

    if args.json:
        print(json.dumps(ranked, indent=2))
        return 0

    if not ranked:
        print("no templates available to suggest")
        return 0

    for rank, entry in enumerate(ranked[:3], start=1):
        print(f"{rank}. {entry['name']}  (score {entry['score']})")
        for reason in entry["reasons"]:
            print(f"     - {reason}")

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
    charts = ", ".join(f"{c['type']}#{c['id']}" for c in manifest["charts"])
    print(f"Charts: {charts or '(none)'}")
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


def cmd_publish(args) -> int:
    from .errors import ConfigError, PublishError

    # "setup" dispatches before load_config(): it's what PRODUCES
    # ~/.jimemo/config.toml, so requiring a valid config first would make
    # it impossible to ever run on a fresh machine.
    if args.target == "setup":
        from .config import config_path
        from .publish.setup import RealIO, run_setup
        from .publish.wrangler import Wrangler

        try:
            run_setup(args.dry_run, Wrangler(), config_path(), RealIO())
        except PublishError as e:
            print(str(e), file=sys.stderr)
            return 1
        return 0

    from .config import load_config
    from .publish import get_publisher

    try:
        publisher = get_publisher(load_config())
    except (ConfigError, PublishError) as e:
        print(str(e), file=sys.stderr)
        return 1

    # "publish" doubles as a small command group: `jimemo publish <file>`
    # publishes, while `purge`/`list`/`gc`/`setup` as the first positional
    # dispatch to the matching Publisher method (mirroring notes-publish's
    # own top-level UX); `setup` itself is handled above, before
    # load_config(). This is a deliberate simplification over nested
    # argparse subparsers, which can't cleanly mix a bare positional (the
    # file to publish) with subcommands in the same slot. The tradeoff: a
    # file literally named "purge", "list", "gc", or "setup" (no extension)
    # cannot be published this way -- an acceptable, documented edge case.
    target = args.target
    try:
        if target == "purge":
            if not args.arg:
                print("jimemo publish purge: missing hash or URL", file=sys.stderr)
                return 2
            publisher.purge(args.arg)
            print(f"purged: {args.arg}")
        elif target == "list":
            for entry in publisher.list():
                print(entry)
        elif target == "gc":
            publisher.gc()
        elif target is None:
            print(
                "jimemo publish: provide a file to publish, or purge/list/gc",
                file=sys.stderr,
            )
            return 2
        else:
            html_path = Path(target)
            if not html_path.is_file():
                print(f"file not found: {html_path}", file=sys.stderr)
                return 1
            print(publisher.publish(html_path, args.title))
    except PublishError as e:
        print(str(e), file=sys.stderr)
        return 1

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

    suggest_p = sub.add_parser("suggest", help="rank templates by fit for a content file")
    suggest_p.add_argument("content", help="content file (.md, .json, or .yaml)")
    suggest_p.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    publish_p = sub.add_parser(
        "publish", help="publish a rendered HTML file to an unlisted link"
    )
    publish_p.add_argument(
        "target", nargs="?",
        help='HTML file to publish, or one of "purge", "list", "gc", "setup"',
    )
    publish_p.add_argument(
        "arg", nargs="?", help='hash or URL (only used with "purge")',
    )
    publish_p.add_argument("--title", help="title for the published page")
    publish_p.add_argument(
        "--dry-run", action="store_true",
        help='with "setup": print the plan without executing or writing anything',
    )

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
    if args.command == "suggest":
        return cmd_suggest(args)
    if args.command == "publish":
        return cmd_publish(args)

    parser.print_usage(sys.stderr)
    return 2
