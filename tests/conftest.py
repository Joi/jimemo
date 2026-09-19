import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo import PYTHON_FLOOR  # noqa: E402


def _version_of(executable):
    """``(major, minor, micro)`` of `executable`, or None if it will not
    run. Measured by asking the interpreter, never parsed out of its
    name: ``python3.13`` may be a 3.13.3, which is below the floor."""
    try:
        result = subprocess.run(
            [executable, "-c", "import sys; print('%d %d %d' % sys.version_info[:3])"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return tuple(int(part) for part in result.stdout.split())
    except ValueError:
        return None


def _sub_floor_pythons():
    """Every interpreter on this machine BELOW ``jimemo.PYTHON_FLOOR``, as
    sorted ``(version, path)`` pairs, one per distinct version. The
    launcher and install.sh refusal tests drive these; both skip when the
    machine has none, so a box with only current Pythons still passes.
    ``python3.13`` is a candidate on purpose -- a same-minor-but-older
    build is exactly what a major/minor-only check accepts wrongly."""
    candidates = (
        "/usr/bin/python3",
        "/usr/local/bin/python3",
        "python3.9",
        "python3.10",
        "python3.11",
        "python3.12",
        "python3.13",
    )
    found = {}
    for candidate in candidates:
        resolved = candidate
        if not candidate.startswith("/"):
            resolved = shutil.which(candidate) or ""
            if not resolved:
                continue
        version = _version_of(resolved)
        if version is not None and version < PYTHON_FLOOR:
            found.setdefault(version, resolved)
    return sorted(found.items())


SUB_FLOOR_PYTHONS = _sub_floor_pythons()


@pytest.fixture(autouse=True)
def hermetic_entry_point(tmp_path, monkeypatch):
    """`jimemo doctor` reads -- and runs -- the interpreter bound in
    ~/.local/bin/jimemo (jimemo#p0nk). Point it at a path under tmp_path for
    EVERY in-process test (test_cli.py and test_suggest.py both call
    main(["doctor"])), so no doctor run sees or executes the developer's real
    entry point: a symlink-style install or a stale wrapper on one machine
    must not fail the suite. Narrower than redirecting HOME, which
    discovery.py and config.py also read. Doctor tests that need an entry
    point write their fixture wrapper to this same path. JIMEMO_ENTRY_POINT
    is dropped too, so a suite run THROUGH the entry point does not add a
    doctor line the assertions did not ask for. Subprocess-based doctor tests
    are out of reach of a monkeypatch and set HOME themselves."""
    from jimemo import _entry_point

    entry_point = tmp_path / "jimemo"
    monkeypatch.setattr(_entry_point, "default_entry_point", lambda: entry_point)
    monkeypatch.delenv("JIMEMO_ENTRY_POINT", raising=False)
    return entry_point
