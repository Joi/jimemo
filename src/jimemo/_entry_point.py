"""Read the installed entry point and ask its bound interpreter for its version.

``install.sh`` writes ``~/.local/bin/jimemo`` as a short ``sh`` wrapper that
``exec``s one absolute interpreter path against one checkout's launcher
(jimemo#p0nk). The wrapper's first lines carry a machine-readable record of
what was bound::

    #!/bin/sh
    # jimemo entry point, written by install.sh. Re-run install.sh to change it.
    # jimemo-entry-point: 1
    # python: /abs/path/to/python3.13
    # launcher: /abs/path/to/checkout/jimemo

``jimemo doctor`` reads that record here and runs the bound interpreter for
its ``sys.version_info``, so the report says whether the command a shell
would actually run still works -- not just whether the interpreter running
doctor does.

Stdlib only, and it must IMPORT on Python 3.9: doctor runs before the vendor
checksum gate and is expected to report on a sub-floor interpreter in one
line rather than traceback (jimemo#gaga), so no ``X | None`` at runtime, no
``match``, and ``from __future__ import annotations`` for the hints.

Both readers fail closed and never raise: an entry point that cannot be
read, or an interpreter that cannot be run, is a reason string the caller
prints, never an exception that turns a doctor line into a traceback.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path
from typing import NamedTuple, Tuple, Union

# The marker install.sh writes into the header and looks for when deciding
# whether a file at the CLI target is its own work. Kept as one literal here
# and one in install.sh; tests/test_entry_point.py runs the real install.sh
# and asserts this module parses the wrapper it wrote.
MARKER = "# jimemo-entry-point:"
_PYTHON_PREFIX = "# python: "
_LAUNCHER_PREFIX = "# launcher: "

# The header lives in the first lines; install.sh's ownership check reads
# exactly this many, so the two readers agree on where the header ends.
HEADER_LINES = 10
# How much of the file the header may occupy. install.sh reads the same
# number of CHARACTERS with `read -n` and refuses to write a header longer
# than half of it in BYTES, so a header the installer wrote always fits both
# readers whole -- a multibyte path cannot make the two bounds disagree,
# and a `# launcher:` value is never truncated into "gone".
HEADER_BYTES = 65536

# One line, exactly five fields: four 1-4 digit ASCII integers around a
# releaselevel word. The same strictness install.sh applies to its version
# query -- `3 13 6 final garbage` is refused, not read as 3.13.6. `[0-9]`
# rather than `\d` (which admits non-ASCII digits), and a width cap on the
# serial too: int() on a 5000-digit string raises past the 3.11+ limit. The
# line must END in exactly one newline, as `print` leaves it -- install.sh
# refuses an answer with no newline too, and the two readers agree.
_VERSION_LINE = re.compile(
    r"\A([0-9]{1,4}) ([0-9]{1,4}) ([0-9]{1,4}) ([A-Za-z]+) ([0-9]{1,4})\n\Z"
)
_VERSION_QUERY = "import sys; print(*sys.version_info)"


def default_entry_point() -> Path:
    """Where install.sh writes the entry point. A function, not a constant,
    so a test can substitute a path under tmp_path and no in-process doctor
    run reads -- or executes -- the developer's real ``~/.local/bin/jimemo``."""
    return Path.home() / ".local" / "bin" / "jimemo"


class EntryPoint(NamedTuple):
    path: Path
    python: str
    launcher: str


def read_entry_point(path: Path) -> Union[EntryPoint, str]:
    """The entry point at `path`, or a reason string when it is not one that
    install.sh wrote: ``"missing"``, ``"symlink"`` (the old ``ln -s`` install,
    checked before anything else because it is the case that runs the
    caller's ``python3``), ``"directory"``, ``"not a regular file"`` (a FIFO
    or device -- never opened, an open() on a FIFO can block forever),
    ``"unreadable: <strerror>"``, ``"no marker"``, ``"marker without
    python/launcher lines"``.

    Two more reasons guard what the values may contain, so a caller can
    hand them to ``Path`` and ``subprocess`` without a traceback: ``"header
    value contains a control character"`` (install.sh refuses to write one,
    and a NUL survives ``errors="replace"``), and ``"header python path is
    not absolute"`` (install.sh only writes absolute paths; a bare name
    would be PATH-searched, the search jimemo#gaga ruled out).

    Reads the header as BYTES and decodes with ``errors="replace"``: the
    file may be anything a user put at that path, and invalid UTF-8 must
    become a reason, not a ``UnicodeDecodeError``. Lines are split on
    ``\n`` ONLY, like install.sh's ``IFS= read -r`` -- ``str.splitlines``
    would also break on U+2028, NEL, \x0b and \x0c, and the two readers
    must see the same value."""
    path = Path(path)
    # One lstat, classified by hand, instead of Path.is_symlink()/exists()/
    # is_dir(): lstat on a child of an unsearchable directory is EACCES, and
    # pathlib on 3.9 raises that while 3.12+ swallows it into False. Here
    # ENOENT is "missing" and any other failure is "unreadable" on every
    # interpreter doctor runs on.
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "missing"
    except OSError as e:
        return "unreadable: {0}".format(e.strerror or str(e))
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if stat.S_ISDIR(st.st_mode):
        return "directory"
    if not stat.S_ISREG(st.st_mode):
        # A FIFO, socket or device: open() on a FIFO with no writer blocks
        # forever, and "bounded, never raises" includes "never hangs".
        return "not a regular file"
    try:
        with open(path, "rb") as handle:
            raw = handle.read(HEADER_BYTES)
    except OSError as e:
        return "unreadable: {0}".format(e.strerror or str(e))
    lines = raw.decode("utf-8", errors="replace").split("\n")[:HEADER_LINES]
    if not any(line.startswith(MARKER) for line in lines):
        return "no marker"
    python = None
    launcher = None
    for line in lines:
        if python is None and line.startswith(_PYTHON_PREFIX):
            python = line[len(_PYTHON_PREFIX):]
        elif launcher is None and line.startswith(_LAUNCHER_PREFIX):
            launcher = line[len(_LAUNCHER_PREFIX):]
    if not python or not launcher:
        return "marker without python/launcher lines"
    if _has_control_character(python) or _has_control_character(launcher):
        return "header value contains a control character"
    if not python.startswith("/"):
        return "header python path is not absolute"
    return EntryPoint(path=path, python=python, launcher=launcher)


def _has_control_character(value: str) -> bool:
    # bash's [[:cntrl:]], the set install.sh refuses at write time: 0x00-0x1F
    # and 0x7F. A trailing "\r" counts -- install.sh keeps it, so it is in
    # the value, and a CR in a path is refused by the installer too.
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def interpreter_version(
    python: str, timeout: float = 30
) -> Union[Tuple[int, int, int, str, int], str]:
    """``sys.version_info`` of the interpreter at `python` as a five-tuple
    ``(major, minor, micro, releaselevel, serial)``, or a reason string.

    Fails closed. The reasons: ``"is missing"`` / ``"is not executable"``
    (checked with the filesystem, no subprocess), ``"could not be run:
    <error>"`` (any OSError, or a ValueError from a NUL in the path),
    ``"timed out after N s"``, ``"exited N"``, and ``"printed something other
    than a version"`` for any output that is not exactly one line of exactly
    five fields. A banner-printing or multiline interpreter is unreadable,
    never "probably fine" -- the same rule install.sh applies."""
    if not os.path.isfile(python):
        return "is missing"
    if not os.access(python, os.X_OK):
        return "is not executable"
    try:
        # Bytes mode on purpose: `text=True` would let the child's output
        # raise UnicodeDecodeError out of here.
        result = subprocess.run(
            [python, "-c", _VERSION_QUERY],
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "timed out after {0:g} s".format(timeout)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return "could not be run: {0}".format(e)
    if result.returncode != 0:
        return "exited {0}".format(result.returncode)
    text = result.stdout.decode("utf-8", errors="replace")
    match = _VERSION_LINE.match(text)
    if match is None:
        return "printed something other than a version"
    major, minor, micro, level, serial = match.groups()
    return (int(major), int(minor), int(micro), level, int(serial))
