"""Allowlist HTML sanitizer for markdown-rendered slot content.

python-markdown passes raw HTML in the source through to its output
verbatim, so a content file (which may come from an untrusted source —
e.g. a briefing assembled from web research) can smuggle
``<img onerror=...>``, ``<svg onload=...>`` or ``javascript:`` links
into the rendered page. ``sanitize_html`` rebuilds the markdown output
from parsed tokens, keeping only the tags/attributes python-markdown
itself emits (extensions: tables, fenced_code) and dropping everything
else. Pure stdlib (html.parser); no vendored dependency.

Rules:
  * Tags outside ALLOWED_TAGS are unwrapped (tag dropped, children
    kept), except DISCARD_TAGS whose entire subtree is removed.
  * Only ``a``, ``img``, ``th``/``td``, ``code`` keep any attributes at
    all, per ALLOWED_ATTRS; ``style`` on table cells must be exactly the
    text-align rule python-markdown emits for column alignment; ``class``
    on ``code`` must be exactly the ``language-*`` form the fenced_code
    extension emits.
  * ``on*`` attributes are always stripped, on every tag.
  * ``href``/``src`` values are scheme-checked after entity decoding
    and whitespace/control stripping, so ``java&#09;script:`` and
    friends are rejected; only relative URLs, http(s), ``#fragment``,
    ``mailto:`` (href) and ``data:image/`` other than ``svg+xml`` (img
    src) survive — SVG is the one image subtype that can itself carry
    markup/script, so it's excluded even though ``<img>`` never executes
    it, as defense in depth.
"""
import html
import re
from html.parser import HTMLParser
from typing import List, Optional, Tuple

# Everything python-markdown (with the tables and fenced_code
# extensions) emits for legitimate markdown constructs.
ALLOWED_TAGS = frozenset({
    "p", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "em", "strong", "del", "code", "pre", "blockquote",
    "a", "ul", "ol", "li", "img",
    "table", "thead", "tbody", "tr", "th", "td",
    "sup", "sub",
})

# Dropped together with their entire content, not unwrapped: their text
# children are executable/renderable payload, not prose.
DISCARD_TAGS = frozenset({
    "script", "style", "svg", "iframe", "object", "embed", "template",
})

VOID_TAGS = frozenset({"br", "hr", "img"})

ALLOWED_ATTRS = {
    "a": frozenset({"href", "title"}),
    "img": frozenset({"src", "alt", "title"}),
    "th": frozenset({"align", "style"}),
    "td": frozenset({"align", "style"}),
    "code": frozenset({"class"}),
}

# The one style python-markdown's tables extension emits for column
# alignment. Anything else on a cell is dropped.
_TEXT_ALIGN_RE = re.compile(r"^\s*text-align:\s*(left|right|center);?\s*$")

# The `class` python-markdown's fenced_code extension emits to carry the
# language hint (e.g. `class="language-python"`). Anything else is dropped.
_FENCED_CODE_LANG_RE = re.compile(r"^language-[\w-]+$")


def _url_allowed(value: str, *, allow_mailto: bool, allow_data_image: bool) -> bool:
    """True if the URL's scheme is acceptable. The test runs on a
    normalized copy — entity-decoded (again, defensively; the parser
    already decoded once), control/space characters removed, lowercased
    — so obfuscations like ``java&#09;script:`` or ``JaVaScRiPt:`` are
    judged in their most-decoded form. Over-decoding can only reject a
    value that would have been safe, never accept an unsafe one."""
    compact = "".join(
        ch for ch in html.unescape(value) if ord(ch) > 0x20
    ).lower()
    if not compact or compact.startswith("#"):
        return True
    head = re.split(r"[/?#]", compact, maxsplit=1)[0]
    if ":" not in head:
        return True  # relative URL, no scheme
    scheme = head.split(":", 1)[0]
    if scheme in ("http", "https"):
        return True
    if scheme == "mailto" and allow_mailto:
        return True
    if (
        scheme == "data"
        and allow_data_image
        and compact.startswith("data:image/")
        and not compact.startswith("data:image/svg+xml")
    ):
        return True
    return False


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        # While set, everything (tags and text) is discarded until the
        # matching close tag at the recorded nesting depth.
        self._discard_tag: Optional[str] = None
        self._discard_depth = 0

    # -- tag emission --------------------------------------------------

    def _format_tag(
        self, tag: str, attrs: List[Tuple[str, Optional[str]]], self_closing: bool
    ) -> str:
        parts = ["<", tag]
        allowed = ALLOWED_ATTRS.get(tag, frozenset())
        for name, value in attrs:
            name = name.lower()
            if name.startswith("on"):
                continue  # never, on any tag, regardless of allowlists
            if name not in allowed:
                continue
            if value is None:
                value = ""
            if tag == "a" and name == "href":
                if not _url_allowed(value, allow_mailto=True, allow_data_image=False):
                    continue
            elif tag == "img" and name == "src":
                if not _url_allowed(value, allow_mailto=False, allow_data_image=True):
                    continue
            elif tag == "code" and name == "class":
                if not _FENCED_CODE_LANG_RE.match(value):
                    continue
            elif name == "style":  # only reachable on th/td
                if not _TEXT_ALIGN_RE.match(value):
                    continue
            parts.append(' {0}="{1}"'.format(name, html.escape(value, quote=True)))
        parts.append(" />" if self_closing else ">")
        return "".join(parts)

    # -- parser callbacks ----------------------------------------------

    def handle_starttag(self, tag, attrs):
        if self._discard_tag is not None:
            if tag == self._discard_tag:
                self._discard_depth += 1
            return
        if tag in DISCARD_TAGS:
            self._discard_tag = tag
            self._discard_depth = 1
            return
        if tag not in ALLOWED_TAGS:
            return  # unwrap: drop the tag, keep its children
        self.out.append(self._format_tag(tag, attrs, self_closing=tag in VOID_TAGS))

    def handle_startendtag(self, tag, attrs):
        if self._discard_tag is not None or tag in DISCARD_TAGS:
            return
        if tag not in ALLOWED_TAGS:
            return
        if tag in VOID_TAGS:
            self.out.append(self._format_tag(tag, attrs, self_closing=True))
        else:
            self.out.append(self._format_tag(tag, attrs, self_closing=False))
            self.out.append("</{0}>".format(tag))

    def handle_endtag(self, tag):
        if self._discard_tag is not None:
            if tag == self._discard_tag:
                self._discard_depth -= 1
                if self._discard_depth == 0:
                    self._discard_tag = None
            return
        if tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.out.append("</{0}>".format(tag))

    def handle_data(self, data):
        if self._discard_tag is not None:
            return
        # convert_charrefs=True delivered this decoded, so one escape
        # round-trips existing entities without double-escaping.
        self.out.append(html.escape(data, quote=False))

    def handle_comment(self, data):
        pass

    def handle_decl(self, decl):
        pass

    def handle_pi(self, data):
        pass

    def unknown_decl(self, data):
        pass


def sanitize_html(html_text: str) -> str:
    """Rebuild `html_text` keeping only allowlisted tags/attributes/URL
    schemes (see module docstring). An unterminated DISCARD_TAGS element
    discards the rest of the document — failing closed."""
    parser = _Sanitizer()
    parser.feed(html_text)
    parser.close()
    return "".join(parser.out)
