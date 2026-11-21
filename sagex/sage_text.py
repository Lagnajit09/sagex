"""Make model-written text safe and readable in the terminal.

Sage's replies are Markdown written by a model, and a model's text can be
steered by whatever it read (a file, a command's output). Before a reply is
rendered:

* terminal control sequences are removed everywhere (colour codes, cursor
  moves, OSC 8 hyperlinks, title changes…), along with other control and
  bidi-override characters that could disguise text;
* links only stay clickable for http(s) and mailto. Anything else (a Windows
  path, `ms-settings:`, `file:`…) is shown as plain text, because the terminal
  hands a clicked link to the OS, which may run it. A link whose text looks
  like a different web address than where it goes shows its real address;
* raw HTML, which the Markdown renderer would silently drop (a `<div>` block
  loses all its text), becomes the Markdown it means; tags it doesn't know,
  such as a `<name>` placeholder, are shown as typed;
* footnotes, which the renderer would hide, are shown as typed.

Code (fenced blocks and `inline spans`) is left exactly as written, apart from
the control characters.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from rich import box
from rich.markdown import Markdown, TableElement
from rich.table import Table

_ESCAPES = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"      # OSC (hyperlinks, titles), with or without its end
    r"|\x1b[P^_][^\x1b]*(?:\x1b\\)?"           # DCS / PM / APC strings
    r"|\x1b\[[0-?]*[ -/]*[@-~]"                # CSI (colours, cursor moves)
    r"|\x1b[@-Z\\-_]"                          # other two-byte escapes
    r"|\x9b[0-?]*[ -/]*[@-~]"                  # 8-bit CSI
)
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")

_SAFE_SCHEMES = ("http", "https", "mailto")


def strip_controls(text: str) -> str:
    """The text without anything a terminal would act on rather than show."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROLS.sub("", _ESCAPES.sub("", text))


# --- code is left alone ---------------------------------------------------------

_FENCE_OPEN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_INLINE = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.S)


def _segments(text: str) -> list[tuple[bool, str]]:
    """(is_code, chunk) pieces: fenced blocks and inline code are code."""
    pieces: list[tuple[bool, str]] = []
    prose: list[str] = []
    fence: str | None = None
    block: list[str] = []
    for line in text.splitlines(keepends=True):
        if fence is None:
            m = _FENCE_OPEN.match(line)
            if m:
                fence, block = m.group(1), [line]
                continue
            prose.append(line)
        else:
            block.append(line)
            if line.strip().startswith(fence[0] * len(fence)) and not line.strip().strip(fence[0]):
                pieces += _inline_segments("".join(prose))
                pieces.append((True, "".join(block)))
                prose, block, fence = [], [], None
    pieces += _inline_segments("".join(prose))
    if block:                                   # an unclosed fence (still streaming)
        pieces.append((True, "".join(block)))
    return pieces


def _inline_segments(text: str) -> list[tuple[bool, str]]:
    out: list[tuple[bool, str]] = []
    i = 0
    for m in _INLINE.finditer(text):
        if "\n\n" in m.group(0):                # a code span can't cross a blank line
            continue
        out.append((False, text[i:m.start()]))
        out.append((True, m.group(0)))
        i = m.end()
    out.append((False, text[i:]))
    return [p for p in out if p[1]]


# --- HTML -> Markdown -----------------------------------------------------------

_INLINE_TAGS = {"b": "**", "strong": "**", "i": "*", "em": "*", "s": "~~", "del": "~~",
                "strike": "~~", "code": "`", "kbd": "`", "samp": "`", "tt": "`"}
_DROP_TAGS = {"span", "u", "ins", "mark", "small", "sup", "sub", "font", "abbr", "cite", "q",
              "var", "time", "wbr", "picture", "source", "thead", "tbody", "tfoot", "colgroup",
              "col", "caption"}
_BLOCK_TAGS = {"div", "section", "article", "header", "footer", "nav", "main", "aside",
               "center", "figure", "figcaption", "details", "blockquote", "table", "dl",
               "address", "html", "body"}
_TAG = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9-]*)((?:\s+[^<>]*?)?)\s*(/?)>")
_ANCHOR = re.compile(r"<a\b([^<>]*)>(.*?)</a\s*>", re.S | re.I)
_ATTR = r"""\b{}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))"""


def _attr(attrs: str, name: str) -> str:
    m = re.search(_ATTR.format(name), attrs, re.I)
    return next((g for g in m.groups() if g is not None), "") if m else ""


def _html_to_markdown(text: str) -> str:
    text = re.sub(r"<!--.*?(?:-->|$)", "", text, flags=re.S)

    def anchor(m: re.Match) -> str:
        href, label = _attr(m.group(1), "href"), m.group(2).strip()
        return f"[{label or href}]({href})" if href else label

    text = _ANCHOR.sub(anchor, text)

    def tag(m: re.Match) -> str:
        closing, name, attrs = m.group(1) == "/", m.group(2).lower(), m.group(3)
        line_start = text.rfind("\n", 0, m.start()) + 1
        in_table_row = text[line_start:m.start()].lstrip().startswith("|")
        if name in _INLINE_TAGS:
            return _INLINE_TAGS[name]
        if name == "br":
            # Two trailing spaces, not a backslash: at the end of a paragraph
            # (a <br> before a block) a backslash would be shown as typed.
            return " " if in_table_row else "  \n"
        if name == "hr":
            return "\n\n---\n\n"
        if name == "p":
            return "\n\n"
        if name in ("ul", "ol", "tr"):
            return "\n"
        if name == "li":
            return "" if closing else "\n- "
        if name in ("td", "th"):
            return "" if closing else " "
        if name == "summary":
            return "**\n\n" if closing else "\n\n**"
        if name == "pre":
            return "\n```\n" if closing else "\n```\n"
        if re.fullmatch(r"h[1-6]", name):
            return "\n\n" if closing else f"\n\n{'#' * int(name[1])} "
        if name == "img":
            src = _attr(attrs, "src")
            return f"![{_attr(attrs, 'alt') or 'image'}]({src})" if src else ""
        if name in _BLOCK_TAGS:
            return "\n"
        if name in _DROP_TAGS:
            return ""
        return "\\" + m.group(0)                  # not HTML we know: show it as typed

    text = _TAG.sub(tag, text)
    return re.sub(r"\n{3,}", "\n\n", text)


# --- links ----------------------------------------------------------------------

_URL = r"<?([^\s()<>]+(?:\([^\s()]*\)[^\s()<>]*)*)>?"
_LINK = re.compile(r"(!?)\[([^\]\n]*)\]\(\s*" + _URL + r"""(?:\s+(?:"[^"]*"|'[^']*'))?\s*\)""")
_AUTOLINK = re.compile(r"<([A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*)>")
_REF_DEF = re.compile(r"^([ \t]{0,3})\[([^\]\n]+)\]:[ \t]*<?(\S+?)>?(?=[ \t]|$)", re.M)
_DOMAIN = re.compile(r"(?:[a-z][a-z0-9+.-]*://)?((?:[a-z0-9-]+\.)+[a-z]{2,})(?:[/:?#]\S*)?", re.I)


def _safe(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme.lower() in _SAFE_SCHEMES and (parts.scheme.lower() == "mailto" or
                                                      bool(parts.netloc))


def _host(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _as_code(url: str) -> str:
    return f"`{url.replace('`', '')}`"


def _links(text: str) -> str:
    def link(m: re.Match) -> str:
        image, label, url = m.group(1), m.group(2), m.group(3)
        if not _safe(url):
            shown = label or ("image" if image else "")
            return f"{shown} ({_as_code(url)})" if shown else _as_code(url)
        if image:
            return m.group(0)
        claimed = _DOMAIN.fullmatch(label.strip())
        if claimed:
            host = claimed.group(1).lower()
            host = host[4:] if host.startswith("www.") else host
            if host != _host(url):                 # text says one site, link goes to another
                return f"[{url}]({url})"
        return m.group(0)

    text = _LINK.sub(link, text)
    text = _AUTOLINK.sub(lambda m: m.group(0) if _safe(m.group(1)) else _as_code(m.group(1)), text)
    # A reference definition pointing somewhere unsafe: shown as text, so the
    # references to it aren't links.
    text = _REF_DEF.sub(lambda m: m.group(0) if _safe(m.group(3)) else f"{m.group(1)}\\[{m.group(2)}]: "
                        f"{_as_code(m.group(3))}", text)
    return text


# --- all together ---------------------------------------------------------------

def _prose(text: str) -> str:
    text = _html_to_markdown(text)
    text = re.sub(r"\[\^([^\]\s]+)\]", r"\\[^\1\\]", text)     # footnotes: shown as typed
    return _links(text)


def for_display(markdown: str) -> str:
    """A model's Markdown reply, ready to hand to the renderer."""
    text = strip_controls(markdown)
    return "".join(chunk if is_code else _prose(chunk) for is_code, chunk in _segments(text))


class _FoldingTable(TableElement):
    """A Markdown table whose long cells (ids, paths, URLs) wrap instead of
    being cut off with "…"."""

    def __rich_console__(self, console, options):
        table = Table(box=box.SIMPLE, pad_edge=False, style="markdown.table.border",
                      show_edge=True, collapse_padding=True)
        if self.header is not None and self.header.row is not None:
            for column in self.header.row.cells:
                heading = column.content.copy()
                heading.stylize("markdown.table.header")
                table.add_column(heading, overflow="fold")
        if self.body is not None:
            for row in self.body.rows:
                table.add_row(*(cell.content for cell in row.cells))
        yield table


class SageMarkdown(Markdown):
    """Rich's Markdown with tables that wrap. Feed it `for_display()` text."""

    elements = {**Markdown.elements, "table_open": _FoldingTable}
