"""Model-written text made safe for the terminal (sagex/sage_text.py)."""

import io

import pytest
from rich.console import Console

from sagex.sage_text import SageMarkdown, for_display, strip_controls


def _render(markdown: str, width: int = 60) -> str:
    buf = io.StringIO()
    Console(file=buf, width=width, color_system=None).print(SageMarkdown(for_display(markdown)))
    return buf.getvalue()


# --- terminal control sequences ---------------------------------------------------

@pytest.mark.parametrize("raw, clean", [
    ("red \x1b[31mRED\x1b[0m", "red RED"),
    ("\x1b]8;;https://evil.example\x1b\\click\x1b]8;;\x1b\\", "click"),       # OSC 8 hyperlink
    ("\x1b]0;new title\x07x", "x"),                                         # window title
    ("bell\x07 back\x08 nul\x00", "bell back nul"),
    ("a\r\nb\rc", "a\nb\nc"),
    ("admin\u202e\u2066txt.exe", "admintxt.exe"),                           # bidi override
    ("tab\tkept\nnewline kept", "tab\tkept\nnewline kept"),
])
def test_control_sequences_are_removed(raw, clean):
    assert strip_controls(raw) == clean


def test_escapes_are_removed_inside_code_too():
    assert "\x1b" not in for_display("```bash\necho \x1b[2Jhi\n```")


# --- links --------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "C:\\Windows\\System32\\calc.exe", "ms-settings:privacy", "file:///C:/x.exe",
    "javascript:alert(1)", "\\\\server\\share\\x.exe", "search-ms:query=x", "scripts/a.sh",
])
def test_only_web_links_stay_links(url):
    out = for_display(f"[open]({url})")
    assert out == f"open (`{url}`)"
    assert "](" not in out


def test_web_links_stay_clickable():
    for md in ("[docs](https://docs.example/a_(b))", "[mail](mailto:a@b.example)", "<https://x.example>"):
        assert for_display(md) == md


def test_unsafe_autolinks_and_reference_links_become_text():
    assert for_display("<ms-settings:privacy>") == "`ms-settings:privacy`"
    out = for_display("See [it][t].\n\n[t]: C:\\x.exe")
    assert "\\[t]: `C:\\x.exe`" in out


def test_a_link_claiming_another_site_shows_where_it_goes():
    assert for_display("[https://autosage.com](https://evil.example/login)") == (
        "[https://evil.example/login](https://evil.example/login)")
    assert for_display("[www.github.com](https://github.com/x)") == "[www.github.com](https://github.com/x)"
    assert for_display("[the docs](https://evil.example)") == "[the docs](https://evil.example)"


def test_unsafe_images_become_text():
    assert for_display("![logo](file:///etc/passwd)") == "logo (`file:///etc/passwd`)"
    assert for_display("![logo](https://img.example/a.png)") == "![logo](https://img.example/a.png)"


# --- HTML ---------------------------------------------------------------------------

def test_inline_html_becomes_markdown():
    assert for_display('Use <b>bold</b>, <code>x</code> and <a href="https://x.example">a link</a>') == (
        "Use **bold**, `x` and [a link](https://x.example)")


def test_html_blocks_keep_their_text():
    out = _render('<div align="center">\n<h1>Title</h1>\n<p>para</p>\n</div>')
    assert "Title" in out and "para" in out
    out = _render("<details>\n<summary>More</summary>\n\nHidden text\n</details>")
    assert "More" in out and "Hidden text" in out


def test_html_anchor_to_an_unsafe_target_is_text():
    assert for_display('<a href="C:\\x.exe">run me</a>') == "run me (`C:\\x.exe`)"


def test_unknown_tags_and_placeholders_show_as_typed():
    out = _render("sagex run script <name> --server <server-name>; List<String>")
    assert "<name>" in out and "<server-name>" in out and "List<String>" in out


def test_br_breaks_the_line_without_leaving_a_backslash():
    out = _render("one<br>two")
    assert out.splitlines()[:2] == ["one" + " " * 57, "two" + " " * 57]
    out = _render('right now:<br><div>\n<p>para</p>\n</div>')
    assert "\\" not in out and "right now:" in out and "para" in out


def test_br_in_a_table_row_keeps_the_table():
    out = _render("| a | b |\n|---|---|\n| one<br>two | 3 |")
    assert "one two" in out and "3" in out


def test_comments_are_dropped():
    assert for_display("a<!-- hidden -->b") == "ab"


# --- code is left alone ---------------------------------------------------------------

def test_code_is_untouched():
    for md in ("```html\n<div><a href=\"C:\\x.exe\">x</a></div>\n```",
               "Use `<b>` and `[x](C:\\y)` literally.",
               "~~~\n<br> [^1]\n~~~"):
        assert for_display(md) == md


def test_an_unclosed_fence_while_streaming_is_code():
    assert for_display("Here:\n```bash\necho <b>") == "Here:\n```bash\necho <b>"


# --- what the renderer would otherwise lose ----------------------------------------------

def test_footnotes_are_visible():
    out = _render("Text[^1]\n\n[^1]: the note")
    assert "[^1]" in out and "the note" in out


def test_wide_table_cells_wrap_instead_of_being_cut():
    run_id = "run-" + "a1b2c3d4" * 6
    out = _render(f"| id | status |\n|---|---|\n| {run_id} | failed |", width=40)
    assert "…" not in out
    cells = [w for w in out.split() if w not in ("id", "status", "failed") and "─" not in w]
    assert "".join(cells) == run_id
