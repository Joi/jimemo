"""``jimemo._entry_point`` reads install.sh's wrapper header and runs the
bound interpreter for its version (jimemo#p0nk). Both readers fail closed:
every case here returns a reason string, and none raises. The wrapper format
the header parser reads is install.sh's; ``tests/test_install.py`` asserts
the installer writes it.
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo import _entry_point
from jimemo._entry_point import EntryPoint, interpreter_version, read_entry_point

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"

HEADER = (
    "#!/bin/sh\n"
    "# jimemo entry point, written by install.sh. Re-run install.sh to change it.\n"
    "# jimemo-entry-point: 1\n"
    "# python: {python}\n"
    "# launcher: {launcher}\n"
    "JIMEMO_PYTHON='{python}'\n"
    "JIMEMO_LAUNCHER='{launcher}'\n"
    "JIMEMO_ENTRY_POINT=\"$0\"\n"
    "export JIMEMO_ENTRY_POINT\n"
    'exec "$JIMEMO_PYTHON" "$JIMEMO_LAUNCHER" "$@"\n'
)


def write_wrapper(path, python, launcher):
    path.write_text(HEADER.format(python=python, launcher=launcher))
    path.chmod(0o755)
    return path


def _shim(path, body, interpreter="/bin/sh"):
    """A small executable at `path` whose shebang is `interpreter`."""
    path.write_text("#!{0}\n{1}".format(interpreter, body))
    path.chmod(0o755)
    return str(path)


# --- read_entry_point ------------------------------------------------------


def test_reads_a_real_wrapper_header(tmp_path):
    wrapper = write_wrapper(
        tmp_path / "jimemo", "/opt/py/bin/python3.13", "/repo/jimemo"
    )
    found = read_entry_point(wrapper)
    assert isinstance(found, EntryPoint), found
    assert found.path == wrapper
    assert found.python == "/opt/py/bin/python3.13"
    assert found.launcher == "/repo/jimemo"


def test_header_values_keep_the_admitted_character_set(tmp_path):
    # Spaces, quotes, `$`, backticks and backslashes are admitted paths;
    # the value is the rest of the line, verbatim.
    odd = "/tmp/sp ace'q\"d$ollar`tick\\bs/bin/python3"
    wrapper = write_wrapper(tmp_path / "jimemo", odd, odd + "/jimemo")
    found = read_entry_point(wrapper)
    assert isinstance(found, EntryPoint), found
    assert found.python == odd
    assert found.launcher == odd + "/jimemo"


def test_missing_path(tmp_path):
    assert read_entry_point(tmp_path / "jimemo") == "missing"


def test_symlink_is_reported_before_anything_else(tmp_path):
    # The old `ln -s` install: even a symlink to a valid wrapper is the
    # case that runs the caller's python3, so it is "symlink", never read.
    real = write_wrapper(tmp_path / "real", sys.executable, "/repo/jimemo")
    link = tmp_path / "jimemo"
    link.symlink_to(real)
    assert read_entry_point(link) == "symlink"
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "nowhere")
    assert read_entry_point(dangling) == "symlink"


def test_directory(tmp_path):
    (tmp_path / "jimemo").mkdir()
    assert read_entry_point(tmp_path / "jimemo") == "directory"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads anything")
def test_unreadable_file(tmp_path):
    wrapper = write_wrapper(tmp_path / "jimemo", sys.executable, "/repo/jimemo")
    wrapper.chmod(0)
    try:
        found = read_entry_point(wrapper)
    finally:
        wrapper.chmod(0o755)
    assert isinstance(found, str) and found.startswith("unreadable: "), found
    assert "ermission" in found, found


def test_no_marker(tmp_path):
    path = tmp_path / "jimemo"
    path.write_text("#!/bin/sh\nexec python3 /somewhere/jimemo \"$@\"\n")
    assert read_entry_point(path) == "no marker"


def test_marker_past_the_header_does_not_count(tmp_path):
    path = tmp_path / "jimemo"
    path.write_text("#!/bin/sh\n" + "# filler\n" * 9 + "# jimemo-entry-point: 1\n")
    assert read_entry_point(path) == "no marker"


def test_invalid_utf8_content_is_a_reason_not_an_exception(tmp_path):
    path = tmp_path / "jimemo"
    path.write_bytes(b"#!/bin/sh\n\xff\xfe\x00garbage\n" * 3)
    assert read_entry_point(path) == "no marker"
    marked = tmp_path / "marked"
    marked.write_bytes(
        b"#!/bin/sh\n# jimemo-entry-point: 1\n# python: /p\xff\n# launcher: /l\n"
    )
    found = read_entry_point(marked)
    assert isinstance(found, EntryPoint), found
    assert found.python.startswith("/p"), found


def test_marker_without_a_python_line(tmp_path):
    path = tmp_path / "jimemo"
    path.write_text("#!/bin/sh\n# jimemo-entry-point: 1\n# launcher: /repo/jimemo\n")
    assert read_entry_point(path) == "marker without python/launcher lines"


def test_marker_without_a_launcher_line(tmp_path):
    path = tmp_path / "jimemo"
    path.write_text("#!/bin/sh\n# jimemo-entry-point: 1\n# python: /usr/bin/python3\n")
    assert read_entry_point(path) == "marker without python/launcher lines"


def test_header_lines_split_on_newline_only_like_install_sh(tmp_path):
    # install.sh reads the header with `IFS= read -r`, which splits on \n
    # alone; str.splitlines() would also break on U+2028, NEL, \x0b, \x0c
    # and then the two readers would disagree about the value. A path
    # holding U+2028 is admitted by the installer (not a control character).
    odd = "/opt/py\u2028thon/bin/python3"
    wrapper = write_wrapper(tmp_path / "jimemo", odd, "/repo/jimemo")
    found = read_entry_point(wrapper)
    assert isinstance(found, EntryPoint), found
    assert found.python == odd
    nel = "/opt/py\u0085thon/bin/python3"
    wrapper = write_wrapper(tmp_path / "nel", nel, "/repo/jimemo")
    found = read_entry_point(wrapper)
    assert isinstance(found, EntryPoint), found
    assert found.python == nel


def test_a_trailing_carriage_return_is_kept_and_refused(tmp_path):
    # install.sh's `read -r` keeps a CR; the installer refuses to write a
    # path with one. So it stays in the value and is refused as a control
    # character, instead of being stripped into a path that was never bound.
    path = tmp_path / "jimemo"
    path.write_bytes(
        b"#!/bin/sh\r\n# jimemo-entry-point: 1\r\n# python: /usr/bin/python3\r\n"
        b"# launcher: /repo/jimemo\r\n"
    )
    assert read_entry_point(path) == "header value contains a control character"


@pytest.mark.parametrize(
    "python, launcher",
    [
        ("/usr/bin/py\x00thon3", "/repo/jimemo"),
        ("/usr/bin/python3", "/repo/ji\x00memo"),
        ("/usr/bin/py\tthon3", "/repo/jimemo"),
        ("/usr/bin/python3", "/repo/\x7fjimemo"),
        ("/usr/bin/python3\x1b[0m", "/repo/jimemo"),
    ],
    ids=["nul-python", "nul-launcher", "tab", "del", "escape"],
)
def test_control_characters_in_header_values_are_refused(tmp_path, python, launcher):
    # A NUL survives errors="replace" and would make Path(...).resolve()
    # raise ValueError in the caller; the reader refuses it first.
    path = tmp_path / "jimemo"
    path.write_bytes(
        b"#!/bin/sh\n# jimemo-entry-point: 1\n"
        + "# python: {0}\n# launcher: {1}\n".format(python, launcher).encode("utf-8")
    )
    assert read_entry_point(path) == "header value contains a control character"


@pytest.mark.parametrize("python", ["python3", "./python3", "bin/python3", "None"])
def test_a_relative_python_path_is_refused(tmp_path, python):
    # install.sh only ever writes absolute paths (a relative sys.executable
    # is refused at install time); subprocess.run on a bare name would
    # PATH-search, which is the search jimemo#gaga ruled out.
    wrapper = write_wrapper(tmp_path / "jimemo", python, "/repo/jimemo")
    assert read_entry_point(wrapper) == "header python path is not absolute"


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches anything")
def test_unsearchable_parent_directory_is_unreadable_not_an_exception(tmp_path):
    # lstat on a child of a mode-000 directory is EACCES: on 3.9
    # Path.is_symlink() raised PermissionError, on 3.12+ it answered False
    # and the file looked "missing". One os.lstat classifies it the same
    # way everywhere.
    parent = tmp_path / "bin"
    parent.mkdir()
    wrapper = write_wrapper(parent / "jimemo", sys.executable, "/repo/jimemo")
    parent.chmod(0)
    try:
        found = read_entry_point(wrapper)
    finally:
        parent.chmod(0o755)
    assert isinstance(found, str) and found.startswith("unreadable: "), found
    assert "ermission" in found, found


def test_default_entry_point_is_under_home(monkeypatch):
    # conftest's autouse fixture patches this very function for every test;
    # undo that here to see the real one.
    monkeypatch.undo()
    expected = Path.home() / ".local" / "bin" / "jimemo"
    assert _entry_point.default_entry_point() == expected


# --- interpreter_version ----------------------------------------------------


def test_real_interpreter_reports_its_version_info():
    assert interpreter_version(sys.executable) == tuple(sys.version_info)


def test_missing_interpreter(tmp_path):
    assert interpreter_version(str(tmp_path / "python3")) == "is missing"


def test_directory_is_missing_not_run(tmp_path):
    assert interpreter_version(str(tmp_path)) == "is missing"


def test_non_executable_file(tmp_path):
    path = tmp_path / "python3"
    path.write_text("#!/bin/sh\necho 3 13 6 final 0\n")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    if os.access(str(path), os.X_OK):  # root
        pytest.skip("root can execute anything")
    assert interpreter_version(str(path)) == "is not executable"


def test_a_path_with_a_nul_byte():
    # os.path.isfile swallows the ValueError from an embedded NUL (3.8+) and
    # answers False, so the path is "missing" before any subprocess.
    assert interpreter_version("/usr/bin/python3\x00") == "is missing"


def test_executable_garbage_bytes_could_not_be_run(tmp_path):
    shim = tmp_path / "python3"
    shim.write_bytes(b"\x7fELF\x00garbage")
    shim.chmod(0o755)
    found = interpreter_version(str(shim))
    assert isinstance(found, str) and found.startswith("could not be run: "), found
    assert "Traceback" not in found


def test_shim_that_prints_a_huge_serial(tmp_path):
    # int() on a 5000-digit string raises ValueError past the 3.11+ digit
    # limit; the regex caps every numeric field at four digits first.
    shim = _shim(tmp_path / "python3", "echo 3 13 6 final " + "9" * 5000 + "\n")
    assert interpreter_version(shim) == "printed something other than a version"


def test_shim_that_prints_non_ascii_digits(tmp_path):
    # `\d` matches U+0663 ARABIC-INDIC DIGIT THREE; `[0-9]` does not.
    shim = _shim(tmp_path / "python3", "printf '\\331\\243 13 6 final 0\\n'\n")
    assert interpreter_version(shim) == "printed something other than a version"


def test_shim_that_exits_nonzero(tmp_path):
    shim = _shim(tmp_path / "python3", "exit 3\n")
    assert interpreter_version(shim) == "exited 3"


def test_shim_that_hangs_times_out(tmp_path):
    shim = _shim(tmp_path / "python3", "sleep 30\n")
    found = interpreter_version(shim, timeout=0.5)
    assert found == "timed out after 0.5 s", found


def test_shim_that_prints_a_banner(tmp_path):
    shim = _shim(
        tmp_path / "python3", "echo 'Welcome to python'\necho 3 13 6 final 0\n"
    )
    assert interpreter_version(shim) == "printed something other than a version"


def test_shim_that_prints_two_lines(tmp_path):
    shim = _shim(tmp_path / "python3", "echo 3 13 6 final 0\necho 3 13 6 final 0\n")
    assert interpreter_version(shim) == "printed something other than a version"


def test_shim_with_a_sixth_field(tmp_path):
    shim = _shim(tmp_path / "python3", "echo 3 13 6 final garbage\n")
    assert interpreter_version(shim) == "printed something other than a version"


@pytest.mark.parametrize(
    "line",
    [
        "",
        "3 13 6",
        "3 13 6 final",
        "3.13.6 final 0",
        "3 13 6 final 0 extra",
        "x 13 6 final 0",
    ],
)
def test_shim_that_prints_a_malformed_version(tmp_path, line):
    shim = _shim(tmp_path / "python3", "printf '%s\\n' '{0}'\n".format(line))
    assert interpreter_version(shim) == "printed something other than a version"


def test_shim_that_prints_invalid_utf8(tmp_path):
    shim = _shim(tmp_path / "python3", "printf '\\377\\376 13 6 final 0\\n'\n")
    assert interpreter_version(shim) == "printed something other than a version"


def test_shim_faking_a_prerelease_reports_its_releaselevel(tmp_path):
    shim = _shim(
        tmp_path / "python3",
        "import collections, sys\n"
        "VI = collections.namedtuple('vi', 'major minor micro releaselevel serial')\n"
        "sys.version_info = VI(3, 14, 0, 'beta', 1)\n"
        "exec(sys.argv[2])\n",
        interpreter=sys.executable,
    )
    assert interpreter_version(shim) == (3, 14, 0, "beta", 1)


# --- the format is install.sh's, pinned by running install.sh -------------


def test_reads_the_wrapper_the_real_install_sh_writes(tmp_path):
    # The header format has two writers' worth of readers (install.sh's
    # `read` loop for --uninstall, this module for doctor) and one writer.
    # Run the writer for real, then read what it wrote.
    env = {**os.environ, "HOME": str(tmp_path)}
    for name in ("JIMEMO_PYTHON", "JIMEMO_ENTRY_POINT", "AMPLIFIER_SKILLS_DIR"):
        env.pop(name, None)
    result = subprocess.run(
        ["/bin/bash", str(INSTALL_SH), "--python", sys.executable],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    found = read_entry_point(tmp_path / ".local" / "bin" / "jimemo")
    assert isinstance(found, EntryPoint), (found, result.stdout)
    assert found.launcher == str(REPO_ROOT / "jimemo"), found
    assert found.python.startswith("/"), found
    assert os.access(found.python, os.X_OK), found
    version = interpreter_version(found.python)
    assert isinstance(version, tuple), version
    assert version[3] == "final", version
    assert version[:3] == tuple(sys.version_info[:3]), version
