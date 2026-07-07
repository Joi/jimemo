"""PDF seam: converts a rendered, self-contained HTML file to PDF by
running a locally installed Chromium-family browser headless.

Why a browser and not a Python PDF library: jimemo charts are Chart.js
-- JavaScript the viewer's browser executes. A converter that cannot run
JS produces pages with empty chart blocks, so the only faithful engine
is the same family of engine the page targets. This makes pdf jimemo's
second external-tool dependency, after wrangler, and it follows the same
containment rules as the Wrangler seam (publish/wrangler.py): injectable
launcher for tests, list-form argv, NEVER shell=True, and only the
commands that need a PDF ever import this module.

Discovery: an explicit ``[pdf] browser`` path in ~/.jimemo/config.toml
wins (and errors if it does not exist -- a configured path silently
falling back to auto-detection would mask typos); then well-known PATH
names; then macOS app-bundle binaries. ``find_browser`` returns None
when nothing is found -- callers print NO_BROWSER_MESSAGE.

Invocation: real Chrome builds do not reliably exit after headless
``--print-to-pdf`` (updater/keepalive child processes hang around), so
the browser process is not trusted to exit on its own. Instead the
output file is the contract: the process is launched detached from our
pipes (its own log file), then polled until ``--print-to-pdf``'s target
exists with a stable non-zero size (or the process exits by itself),
at which point anything still running is killed. ``--virtual-time-budget``,
``--no-pdf-header-footer`` (no URL/date chrome), a throwaway
``--user-data-dir`` (never touches the real browser profile or its
process singleton), and ``--disable-background-networking``
/``--disable-component-update`` (stop the launch from waking
GoogleUpdater helper processes) round out the flags. Page geometry is
owned by CSS, not flags: the print-to-pdf CLI has no paper-size option,
and toolkit/print-force.css already forces the light-token print look.

Printing never runs against the user's own file: Chart.js animations
run on wall-clock time, which virtual time does not advance, so a
straight print captures the animation's first frame. `render_pdf`
prints a temp copy instead, with Chart.js animation disabled via a
snippet injected right after the library's own ``</script>`` (see
`_NO_ANIMATION_SNIPPET`), and removes the copy when done.
"""
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, List, Optional

from .errors import PdfError

Launcher = Callable[[List[str], Any], "subprocess.Popen"]

NO_BROWSER_MESSAGE = (
    "pdf output needs a Chromium-family browser (Chrome, Chromium, Edge, "
    "or Brave); install one, or point [pdf] browser = \"/path/to/browser\" "
    "in ~/.jimemo/config.toml at one"
)

#: How long one conversion may run before it is killed. A hung browser
#: must fail jimemo, not hang it.
TIMEOUT_SECONDS = 120

#: How often to check whether the PDF has appeared/stabilized.
POLL_INTERVAL = 0.25

#: The one mutation jimemo makes to the print copy (never to the user's
#: file): Chart.js animations run on wall-clock time, which headless
#: print capture does not wait for, so an animated chart prints as its
#: first frame. Disabling animation makes the first frame the final
#: frame. Injected immediately after the first </script> -- the library
#: script -- so it runs before every chart init.
_NO_ANIMATION_SNIPPET = (
    "</script><script>window.Chart && (Chart.defaults.animation = false);"
    "</script>"
)

_PATH_NAMES = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
)

_MACOS_BUNDLES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)


def _launch(argv: List[str], log_handle: Any) -> "subprocess.Popen":
    """Default launcher: a real subprocess, list-form argv only, never
    shell=True; stdout/stderr go to a log file, not pipes -- browser
    child processes can hold a pipe open long after the PDF is done."""
    return subprocess.Popen(argv, stdout=log_handle, stderr=log_handle)


def find_browser(
    configured: Optional[str] = None,
    *,
    which: Callable[[str], Optional[str]] = shutil.which,
    exists: Callable[[str], bool] = os.path.exists,
) -> Optional[str]:
    """Resolve the browser binary to run. `configured` is the
    ``[pdf] browser`` value from config.toml (None when unset); `which`
    and `exists` are injectable so tests never depend on what the test
    machine has installed.

    Raises PdfError when `configured` is set but missing. Returns None
    when unconfigured and nothing is auto-detected.
    """
    if configured:
        if not exists(configured):
            raise PdfError(
                f"configured pdf browser does not exist: {configured} "
                "(from [pdf] browser in ~/.jimemo/config.toml)"
            )
        return configured
    for name in _PATH_NAMES:
        hit = which(name)
        if hit:
            return hit
    for path in _MACOS_BUNDLES:
        if exists(path):
            return path
    return None


def render_pdf(
    html_path: Path,
    pdf_path: Path,
    browser: str,
    launcher: Launcher = _launch,
) -> None:
    """Convert `html_path` to `pdf_path` with one headless `browser` run.

    The browser process is not trusted to exit: some builds hang after
    writing the PDF (updater/keepalive children). The output file is the
    contract instead -- poll until the PDF exists with a stable non-zero
    size (or the process exits on its own), then kill whatever is left.
    Raises PdfError on a non-runnable browser, a nonzero exit, a timeout
    with no PDF, or an exit-0 run that left no PDF behind.

    Printing uses a temp copy of the page, written next to the original
    (so any relative references resolve identically), with Chart.js
    animation disabled via a constant injected snippet -- see
    _NO_ANIMATION_SNIPPET. The user's file is never modified.
    """
    html_path = Path(html_path).resolve()
    pdf_path = Path(pdf_path).resolve()
    try:
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise PdfError(
            f"cannot create output directory {pdf_path.parent}: {e}"
        ) from e

    try:
        html = html_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise PdfError(f"cannot read {html_path}: {e}") from e

    print_copy = html_path.parent / f".jimemo-pdf-{os.getpid()}-{html_path.name}"
    try:
        print_copy.write_text(
            html.replace("</script>", _NO_ANIMATION_SNIPPET, 1),
            encoding="utf-8",
        )
    except OSError as e:
        raise PdfError(f"cannot write print copy {print_copy}: {e}") from e

    try:
        with tempfile.TemporaryDirectory(prefix="jimemo-pdf-profile-") as profile_dir:
            argv = [
                browser,
                "--headless",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-component-update",
                f"--user-data-dir={profile_dir}",
                "--virtual-time-budget=10000",
                "--no-pdf-header-footer",
                f"--print-to-pdf={pdf_path}",
                print_copy.as_uri(),
            ]
            log_path = Path(profile_dir) / "browser.log"
            with open(log_path, "w+", encoding="utf-8", errors="replace") as log_handle:
                try:
                    proc = launcher(argv, log_handle)
                except FileNotFoundError as e:
                    raise PdfError(f"browser is not runnable: {browser}: {e}") from e

                deadline = time.monotonic() + TIMEOUT_SECONDS
                last_size = -1
                returncode = None
                while True:
                    returncode = proc.poll()
                    if returncode is not None:
                        break
                    if pdf_path.is_file():
                        size = pdf_path.stat().st_size
                        if size > 0 and size == last_size:
                            # PDF complete and stable while the browser
                            # still runs: this build never exits after
                            # printing. The artifact is the contract.
                            proc.kill()
                            proc.wait()
                            returncode = 0
                            break
                        last_size = size
                    else:
                        last_size = -1
                    if time.monotonic() >= deadline:
                        proc.kill()
                        proc.wait()
                        raise PdfError(
                            f"browser produced no PDF within {TIMEOUT_SECONDS}s "
                            f"converting {html_path.name}"
                        )
                    time.sleep(POLL_INTERVAL)

                if returncode != 0:
                    log_handle.seek(0)
                    tail = log_handle.read().strip().splitlines()[-3:]
                    detail = ": " + " / ".join(tail) if tail else ""
                    raise PdfError(
                        f"browser exited {returncode} converting "
                        f"{html_path.name}{detail}"
                    )
    finally:
        try:
            print_copy.unlink()
        except OSError:
            pass

    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise PdfError(
            f"browser exited 0 but wrote no PDF at {pdf_path} -- the "
            "binary may not support --print-to-pdf; set [pdf] browser in "
            "~/.jimemo/config.toml to a Chromium-family browser"
        )
