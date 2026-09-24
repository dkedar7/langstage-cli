"""``render_markdown`` input -> output table (gh #155, #156, #157, #161).

The TTY render used four layered regexes, which deleted ``*`` bullets and the ``*`` in
``5 * 3`` (#155), styled markdown inside code spans and broke an escape on ``[a](b)``
(#156), mangled fenced code blocks (#157), and dropped the outer bold after a nested
italic (#161). It is now a small tokenizing pass. Each case below is one of those
reports or a guard around it. ANSI codes are shown as tags for readability.
"""

import pytest

from langstage_cli import cli

_TAGS = {
    "\x1b[0m": "</>",
    "\x1b[1m": "<b>",
    "\x1b[2m": "<dim>",
    "\x1b[3m": "<i>",
    "\x1b[4m": "<u>",
    "\x1b[36m": "<code>",
}


def _tagged(text: str) -> str:
    out = cli.render_markdown(text)
    for code, tag in _TAGS.items():
        out = out.replace(code, tag)
    return out


CASES = [
    # --- gh #155: a single * is emphasis only when it flanks text ---
    ("* alpha\n* beta\n* gamma", "* alpha\n* beta\n* gamma"),
    ("5 * 3 * 2 = 30", "5 * 3 * 2 = 30"),
    ("a ** b ** c", "a ** b ** c"),
    ("* item with *emph*", "* item with <i>emph</>"),
    ("this is *important*", "this is <i>important</>"),
    ("*one* and *two*", "<i>one</> and <i>two</>"),
    ("**bold** text", "<b>bold</> text"),
    ("***both***", "<b><i>both</>"),
    # Emphasis never spans lines, so an unclosed * can't leak into the next paragraph.
    ("*open\n\nclose*", "*open\n\nclose*"),
    # --- gh #156: code spans are literal ---
    ("`**bold**`", "<code>**bold**</>"),
    ("`*x*`", "<code>*x*</>"),
    ("`[a](b)`", "<code>[a](b)</>"),
    ("Use `**kwargs` here", "Use <code>**kwargs</> here"),
    ("glob `src/**/*.py` ok", "glob <code>src/**/*.py</> ok"),
    ("``a ` b``", "<code>a ` b</>"),
    ("unclosed `tick", "unclosed `tick"),
    # --- gh #157: fenced code blocks ---
    (
        "```python\nx = 1\nprint(x)\n```",
        "<dim>```python</>\n<code>x = 1</>\n<code>print(x)</>\n<dim>```</>",
    ),
    (
        "Here:\n```\n*a* `b` **c**\n\n```\nRun *it*.",
        "Here:\n<dim>```</>\n<code>*a* `b` **c**</>\n\n<dim>```</>\nRun <i>it</>.",
    ),
    ("~~~\n*x*\n~~~", "<dim>~~~</>\n<code>*x*</>\n<dim>~~~</>"),
    # An unclosed fence runs to the end of the text.
    ("```sh\nls *.py", "<dim>```sh</>\n<code>ls *.py</>"),
    # A one-line ```code``` is an inline span, not a fence.
    ("```inline```", "<code>inline</>"),
    # --- gh #161: nesting keeps the outer style; escapes; parens in a link URL ---
    (
        "**Important: *do not* delete** the file",
        "<b>Important: <i>do not</><b> delete</> the file",
    ),
    ("*a **b** c*", "<i>a <b>b</><i> c</>"),
    ("**use `x` now**", "<b>use <code>x</><b> now</>"),
    (r"Escaped \*not italic\* stays literal", "Escaped *not italic* stays literal"),
    (r"C:\Users\me", r"C:\Users\me"),
    (
        "[Merge sort](https://en.wikipedia.org/wiki/Merge_sort_(algorithm)) is stable",
        "<u>Merge sort</> is stable",
    ),
    ("[**docs**](https://x.y)", "<u><b>docs</><u></>"),
    # Plain text passes through untouched.
    ("snake_case_name and 2 < 3", "snake_case_name and 2 < 3"),
    ("", ""),
]


@pytest.mark.parametrize("text,expected", CASES, ids=[repr(c[0])[:40] for c in CASES])
def test_render_markdown_table(text, expected):
    assert _tagged(text) == expected


def test_no_bare_escape_bytes_leak():
    # gh #156: the link rule used to match across a code span's own \x1b[36m and leave
    # a bare ESC plus the literal text "36m".
    out = cli.render_markdown("Use `**kwargs` in Python. See `[a](b)` for the link form.")
    assert "36m" not in out.replace("\x1b[36m", "")


def test_markers_are_dropped_cleanly_when_ansi_is_off(monkeypatch):
    # The quiet path blanks the color constants; the render stays lossless text.
    for name in ("RESET", "BOLD", "DIM", "ITALIC", "UNDERLINE", "CYAN"):
        monkeypatch.setattr(cli, name, "")
    assert cli.render_markdown("**a** *b* `*c*` * d") == "a b *c* * d"
