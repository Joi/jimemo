import argparse
import sys

from . import __version__
from ._vendor import VENDOR_DIR, add_vendor_to_path
from .checksums import verify_checksums
from .discovery import default_search_dirs, find_templates

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

    args = parser.parse_args(argv)

    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "list":
        return cmd_list(args)

    parser.print_usage(sys.stderr)
    return 2
