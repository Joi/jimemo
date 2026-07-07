import sys
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.errors import PdfError
from jimemo.pdf import NO_BROWSER_MESSAGE, find_browser


def no_which(name):
    return None


def no_exists(path):
    return False


def test_find_browser_prefers_configured_path():
    result = find_browser(
        "/opt/thorium/thorium",
        which=no_which,
        exists=lambda p: p == "/opt/thorium/thorium",
    )
    assert result == "/opt/thorium/thorium"


def test_find_browser_missing_configured_path_errors():
    with pytest.raises(PdfError) as exc_info:
        find_browser("/nope/chrome", which=no_which, exists=no_exists)
    assert "/nope/chrome" in str(exc_info.value)


def test_find_browser_falls_back_to_path_names():
    hits = {"google-chrome": "/usr/bin/google-chrome"}
    assert (
        find_browser(None, which=hits.get, exists=no_exists)
        == "/usr/bin/google-chrome"
    )


def test_find_browser_path_beats_macos_bundle():
    hits = {"chromium": "/usr/bin/chromium"}
    assert find_browser(None, which=hits.get, exists=lambda p: True) == "/usr/bin/chromium"


def test_find_browser_falls_back_to_macos_bundles():
    mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    assert find_browser(None, which=no_which, exists=lambda p: p == mac) == mac


def test_find_browser_returns_none_when_nothing_found():
    assert find_browser(None, which=no_which, exists=no_exists) is None


def test_no_browser_message_names_the_config_key():
    assert "[pdf]" in NO_BROWSER_MESSAGE
    assert "config.toml" in NO_BROWSER_MESSAGE
