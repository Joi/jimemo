"""PDF seam: converts a rendered, self-contained HTML file to PDF by
running a locally installed Chromium-family browser headless.

Why a browser and not a Python PDF library: jimemo charts are Chart.js
-- JavaScript the viewer's browser executes. A converter that cannot run
JS produces pages with empty chart blocks, so the only faithful engine
is the same family of engine the page targets. This makes pdf jimemo's
second external-tool dependency, after wrangler, and it follows the same
containment rules as the Wrangler seam (publish/wrangler.py): injectable
runner for tests, list-form argv, NEVER shell=True, and only the
commands that need a PDF ever import this module.

Discovery: an explicit ``[pdf] browser`` path in ~/.jimemo/config.toml
wins (and errors if it does not exist -- a configured path silently
falling back to auto-detection would mask typos); then well-known PATH
names; then macOS app-bundle binaries. ``find_browser`` returns None
when nothing is found -- callers print NO_BROWSER_MESSAGE.

Invocation: ``--print-to-pdf`` against a ``file://`` URL, with
``--virtual-time-budget`` so Chart.js animations complete in virtual
time before capture, ``--no-pdf-header-footer`` (no URL/date chrome),
and a throwaway ``--user-data-dir`` so the run never touches the real
browser profile and never collides with a running browser's process
singleton. Page geometry is owned by CSS, not flags: the print-to-pdf
CLI has no paper-size option, and toolkit/print-force.css already forces
the light-token print look.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from subprocess import CompletedProcess
from typing import Callable, List, Optional

from .errors import PdfError

Runner = Callable[[List[str]], CompletedProcess]

NO_BROWSER_MESSAGE = (
    "pdf output needs a Chromium-family browser (Chrome, Chromium, Edge, "
    "or Brave); install one, or point [pdf] browser = \"/path/to/browser\" "
    "in ~/.jimemo/config.toml at one"
)

#: How long one conversion may run before it is killed. A hung browser
#: must fail jimemo, not hang it.
TIMEOUT_SECONDS = 120

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


def _run(argv: List[str]) -> CompletedProcess:
    """Default runner: a real subprocess, list-form argv only, never
    shell=True, killed after TIMEOUT_SECONDS."""
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=TIMEOUT_SECONDS
    )


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
