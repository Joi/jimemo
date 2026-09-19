"""Refuse to provide jimemo's HTML checks on an unsupported interpreter.

jimemo#y9p8 carried a fail-closed guard inside the linter because
``html.parser`` before CPython 3.13.4 decodes a semicolonless character
reference inside an attribute where a browser keeps it literal, which moves
CSS string boundaries and can hide a live ``url()`` behind an apparent
comment. Joi ruled (jimemo#gaga) that the floor rises instead, so the guard
is gone, and this module is the boundary that replaces it.

It checks two things: the interpreter is at or above ``jimemo.PYTHON_FLOOR``,
and it is a FINAL release. The second is not pedantry -- CPython 3.14.0b1
compares ``(3, 14, 0) >= (3, 13, 6)`` and so passed every boundary in an
earlier draft, while its ``html.parser`` fails all three disagreements below
and jimemo#y9p8's payload lints clean on it (measured on the real build;
gh-69426 landed in 3.14.0b2). A version number cannot say which pre-release
carries which backport.

**Why only a version check.** An earlier draft of this module also MEASURED
the running parser -- feeding it probe inputs and refusing if any answer
disagreed with a browser -- so that a distribution which backported one of
these fixes into an older release, or shipped a current release with one
reverted, would be seen for what it is. Two independent reviews then found
the probe set incomplete, each time for a different clause of CPython's
implementation (the semicolonless-name rule alone has three), and the second
demonstrated a live lint bypass on a parser that passed every probe.

That is the trap Joi's ruling already rejected as option 2: a complete probe
set is a model of ``html.parser``, and a model of a tokenizer is the thing
that keeps disagreeing with the tokenizer. So the probes are gone. The
version number is the contract, and the SUITE is the detector -- the 318-case
canary and the nine jimemo#y9p8 payload vectors in ``tests/test_lint.py``
fail loudly on any interpreter whose parser does not behave, which is what
the ruling asked for: delete the guard only where a test proves the floor
matches the browser rule for that input.

The residual risk is stated plainly rather than half-guarded. A release at or
above the floor with one of these fixes reverted is accepted here, and only
running the suite would reveal it. A backport into an older release is
refused even though its parser may be fine. Both follow from supporting a
version range instead of modelling a parser.

**Why this module exists at all**, rather than just the launcher's check:
``install.sh``, the ``./jimemo`` launcher and ``jimemo doctor`` all check the
version, but a direct caller -- ``from jimemo.lint import lint_html``, which
is how y9p8's own reproduction is written -- passes none of the three. This
is the boundary that caller crosses.

**Scope of the contract, stated exactly.** ``jimemo/__init__.py`` holds
``PYTHON_FLOOR`` and deliberately does NOT check it: ``jimemo doctor`` must
stay importable on a sub-floor interpreter so it can *report* the problem
(one line, non-zero exit) instead of dying in a traceback, which is what
jimemo#gaga asks of it.

  ``./jimemo``      refuses every command, one line on stderr, exit 1
  ``install.sh``    refuses to install; ``--uninstall`` still works
  ``jimemo doctor`` reports the running version and fails below the floor
  this module       raises at import of ``jimemo.lint``

``jimemo.sanitize`` parses HTML too and has the same dependence, but is not
wired up here: jimemo#86jn rewrote that file while this change was in flight
and the dispatch brief forbade touching it. Filed as jimemo#dexg.

**What the floor does NOT fix.** It is the three specific disagreements that
jimemo#y9p8, jimemo#86jn and jimemo#1gs5 ran into -- semicolonless attribute
references (gh-69426, 3.13.4), unclosed ``<style>`` text (gh-86155, 3.13.4),
and ``<div title==""id id=grad>`` attribute splitting (3.13.6, the component
that sets the floor) -- not a claim of general equivalence. At least one
divergence is known to REMAIN on every supported interpreter:

  foreign-content RCDATA -- CPython treats ``title`` and ``textarea`` as
  RCDATA regardless of namespace, so inside ``<svg>`` or ``<math>`` it hands
  their content over as TEXT. A browser only does that in the HTML namespace;
  in foreign content the same bytes are real markup. So
  ``<svg><title><style>a{background:url(https://host/x)}</style></title></svg>``
  is markup a browser parses and fetches from, while this parser reports a
  text node and jimemo's self-containment scan never sees it. Confirmed
  against Chromium. NOT a consequence of retiring the y9p8 guard (it is live
  on any 3.13.4+, including every current fleet Mac) and NOT fixed here:
  filed as jimemo#cg2h with the payloads and the evidence, because it needs a
  lint rule with its own false-positive analysis.
"""
import sys

from . import PYTHON_FLOOR


_RELEASELEVEL_SUFFIX = {"alpha": "a", "beta": "b", "candidate": "rc"}


def running_version(version_info=None):
    """The running interpreter's version as CPython spells it, built from
    ``sys.version_info`` alone so a test can substitute one: ``3.14.7``,
    ``3.14.0b1``. Not ``platform.python_version()``, which reports a
    pre-release as though it were final (``3.14.0`` for 3.14.0b1) -- the
    exact ambiguity that made a pre-release look supported."""
    v = version_info if version_info is not None else sys.version_info
    text = "{0}.{1}.{2}".format(v[0], v[1], v[2])
    if v[3] != "final":
        text += "{0}{1}".format(_RELEASELEVEL_SUFFIX.get(v[3], v[3]), v[4])
    return text


def unsupported_interpreter_problem(version_info=None):
    """A message naming how this interpreter falls short of
    ``PYTHON_FLOOR``, or None when it is supported.

    `version_info` is an optional five-tuple standing in for
    ``sys.version_info``. ``jimemo doctor`` passes the tuple it read from
    the interpreter the installed entry point is bound to (jimemo#p0nk), so
    the verdict on THAT interpreter comes from the same comparison as the
    import boundary's -- both the releaselevel check and the floor
    comparison read `v`, never the running interpreter's fields."""
    v = version_info if version_info is not None else sys.version_info
    floor = ".".join(str(part) for part in PYTHON_FLOOR)
    # A PRE-RELEASE is refused whatever its numbers say. CPython 3.14.0b1
    # compares (3, 14, 0) >= (3, 13, 6) and so passed every boundary in an
    # earlier draft -- while failing all three of the disagreements this
    # floor exists to rule out, and letting jimemo#y9p8's payload through
    # `lint_html` clean (measured). gh-69426 landed in 3.14.0b2. A version
    # range cannot say which pre-release carries which backport, so jimemo
    # supports final releases only and says so.
    if v[3] != "final":
        return (
            "Python {running} is a pre-release (releaselevel {level!r}); "
            "jimemo supports final releases from {floor} onward, because a "
            "pre-release's version number does not say which parser fixes "
            "it carries".format(
                running=running_version(v), level=v[3], floor=floor
            )
        )
    if tuple(v[:3]) >= PYTHON_FLOOR:
        return None
    return "Python {running} is below jimemo's floor of {floor}".format(
        running=running_version(v), floor=floor
    )


def assert_interpreter_is_supported():
    """Refuse to provide a check that would answer a different question than
    the browser asks. Fail-closed on purpose: the alternative is a
    self-containment check that silently passes a page a browser would fetch
    from (jimemo#y9p8, jimemo#gaga)."""
    problem = unsupported_interpreter_problem()
    if problem is not None:
        raise RuntimeError(
            "jimemo cannot run here: "
            + problem
            + ". Install Python "
            + ".".join(str(part) for part in PYTHON_FLOOR)
            + " or newer and run jimemo with it."
        )
