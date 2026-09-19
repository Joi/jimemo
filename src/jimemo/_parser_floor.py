"""Measure the running ``html.parser`` against the browser behaviours
jimemo's checks depend on, and refuse to provide those checks otherwise.

jimemo#y9p8 carried a fail-closed guard inside the linter because
``html.parser`` before CPython 3.13.4 decodes a semicolonless character
reference inside an attribute where a browser keeps it literal, which moves
CSS string boundaries and can hide a live ``url()`` behind an apparent
comment. Joi ruled (jimemo#gaga) that the floor rises instead, so the guard
is gone and this module is what makes its absence safe.

Why a measurement and not only a version comparison: a version number is the
contract a human installs against -- the ``./jimemo`` launcher, ``install.sh``
and ``jimemo doctor`` all check it -- but it cannot see a distribution that
backported one of these fixes into an older release, or shipped a current
release with one reverted. And a direct caller (``from jimemo.lint import
lint_html``, which is how y9p8's own reproduction is written) passes none of
those three. So the boundary checks the number AND measures the parser.

Why a version comparison and not only the measurement: the probes cannot tell
a user what to install, and ``install.sh`` has to refuse before it creates any
symlinks.

**Scope of the contract, stated exactly.** ``jimemo/__init__.py`` holds
``PYTHON_FLOOR`` and deliberately does NOT check it: ``jimemo doctor`` must
stay importable on a sub-floor interpreter so that it can *report* the problem
(one line, non-zero exit) instead of dying in a traceback, which is what
jimemo#gaga asks of it. The enforcing boundaries are therefore:

  ``./jimemo``      refuses every command, one line on stderr, exit 1
  ``install.sh``    refuses to install; ``--uninstall`` still works
  ``jimemo doctor`` reports the running version and fails below the floor
  this module       called at import of the modules whose correctness depends
                    on html.parser matching a browser

``jimemo.lint`` calls it. ``jimemo.sanitize`` parses HTML too and has the same
dependency, but is not wired up here: jimemo#86jn rewrote that file while this
change was in flight and the dispatch brief forbade touching it. Filed as a
follow-up -- it is one ``_assert_parser_is_browser_faithful()`` call.

The probes, and the CPython release that made each one true:

  refs   a semicolonless legacy reference in an attribute stays literal
         (gh-69426, 3.13.4)
  style  the text of an unclosed <style> still reaches handle_data
         (gh-86155, 3.13.4)
  attrs  ``<div title==""id id=grad>`` splits the way a browser splits it
         (3.13.6 -- the component that sets the floor)

Measured 2026-09-19 on real interpreters: 3.9.6, 3.10.21, 3.12.11, 3.13.0 and
3.13.3 fail ``refs`` and ``style``; 3.13.4 and 3.13.5 pass those two and fail
``attrs``; 3.13.6, 3.13.7, 3.13.15, 3.14.0 and 3.14.7 pass all three. Three
probes rather than two precisely because the first two do not separate 3.13.5
from the floor.
"""
import sys
from functools import lru_cache
from html.parser import HTMLParser
from typing import List, Optional, Tuple

from . import PYTHON_FLOOR


class _AttrProbe(HTMLParser):
    """Records the attributes of the one start tag it is fed."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.attrs: List[Tuple[str, Optional[str]]] = []

    def handle_starttag(self, tag, attrs):
        self.attrs = list(attrs)


class _DataProbe(HTMLParser):
    """Records the character data of the one document it is fed."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.data: List[str] = []

    def handle_data(self, data):
        self.data.append(data)


def _parser_keeps_semicolonless_attr_refs() -> bool:
    """True when ``&ampx`` inside an attribute survives as written, as a
    browser keeps it (the HTML "historical reasons" rule). False below
    CPython 3.13.4, where html.parser decodes it to ``&x``."""
    probe = _AttrProbe()
    probe.feed("<p a='&ampx'>")
    probe.close()
    return probe.attrs == [("a", "&ampx")]


def _parser_keeps_unclosed_style_text() -> bool:
    """True when the text of a ``<style>`` with no closing tag still reaches
    ``handle_data``, as a browser applies it to the end of the document.
    False below CPython 3.13.4, which drops it at close() and so hides any
    url() it contains from the scan."""
    probe = _DataProbe()
    probe.feed("<!doctype html><html><body><style>.x{color:red}")
    probe.close()
    return any(".x{color:red}" in chunk for chunk in probe.data)


def _parser_splits_attributes_like_a_browser() -> bool:
    """True when ``<div title==""id id=grad>`` splits into the two attributes
    a browser reads -- an unquoted ``title`` value of ``=""id``, then
    ``id=grad``. False below CPython 3.13.6, which reports an empty ``title``,
    a phantom valueless ``id`` and then ``id=grad``, so a check can judge a
    different attribute value than the browser uses."""
    probe = _AttrProbe()
    probe.feed('<div title==""id id=grad>')
    probe.close()
    return probe.attrs == [("title", '=""id'), ("id", "grad")]


# The roster is asserted by name in tests/test_parser_floor.py, so removing a
# probe cannot quietly remove its coverage with it.
PROBES = (
    ("refs", _parser_keeps_semicolonless_attr_refs),
    ("style", _parser_keeps_unclosed_style_text),
    ("attrs", _parser_splits_attributes_like_a_browser),
)


@lru_cache(maxsize=None)
def browser_faithfulness_problem() -> Optional[str]:
    """The first way this interpreter disagrees with a browser -- by version
    or by measurement -- as a message, or None when it agrees."""
    running = ".".join(str(part) for part in sys.version_info[:3])
    floor = ".".join(str(part) for part in PYTHON_FLOOR)
    if tuple(sys.version_info[:3]) < PYTHON_FLOOR:
        return "Python {running} is below jimemo's floor of {floor}".format(
            running=running, floor=floor
        )
    for name, probe in PROBES:
        if not probe():
            return (
                "this Python's html.parser fails the {name!r} check: it reads "
                "HTML differently than a browser does, so jimemo cannot judge "
                "a page the way the browser renders it (running {running}; "
                "jimemo's floor is {floor})".format(
                    name=name, running=running, floor=floor
                )
            )
    return None


def assert_parser_is_browser_faithful() -> None:
    """Refuse to provide a check that would answer a different question than
    the browser asks. Fail-closed on purpose: the alternative is a
    self-containment check that silently passes a page a browser would fetch
    from (jimemo#y9p8, jimemo#gaga)."""
    problem = browser_faithfulness_problem()
    if problem is not None:
        raise RuntimeError(
            "jimemo cannot run here: "
            + problem
            + ". Install Python "
            + ".".join(str(part) for part in PYTHON_FLOOR)
            + " or newer and run jimemo with it."
        )
