"""`/help` must not leak ANSI fragments (gh #-dogfood).

The Commands block built alias strings with `…join([""] + aliases)[4:]`, whose
slice cut into the leading `\x1b[36m` escape — leaking a literal "36m" and bleeding
color. Each alias is now its own cyan token.
"""

import io
import re
from contextlib import redirect_stdout

from langstage_cli.cli import print_help

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _rendered_help() -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_help()
    return buf.getvalue()


def test_help_has_no_leaked_ansi_fragment():
    plain = _ANSI.sub("", _rendered_help())
    # No bare color-code residue once real escapes are stripped.
    assert "36m" not in plain
    assert "[0m" not in plain and "[36" not in plain


def test_help_renders_aliases_cleanly():
    plain = _ANSI.sub("", _rendered_help())
    # A multi-alias command renders as comma-separated /tokens.
    assert "/quit" in plain
    assert re.search(r"/quit(, /\w+)+", plain), plain
