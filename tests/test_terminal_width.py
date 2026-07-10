"""`get_terminal_width()` floors at a sane minimum so the header box never collapses (gh #71).

A pty forked without an initialized window size (pexpect/expect automation, some CI
pseudo-ttys, editor-integrated terminals, process supervisors) makes
`os.get_terminal_size()` **succeed** and report `columns == 0` — it does NOT raise
`OSError`, so the old `min(cols, 100)` returned 0 and the banner's borders
(`"─" * (width - 2)` → `"─" * -2` → `""`) collapsed the box to a bare `╭╮`/`╰╯`.
"""

from __future__ import annotations

import io
import os
from contextlib import redirect_stdout
from unittest import mock

from langstage_cli.cli import get_terminal_width, print_header_box, separator


def _fake_size(cols):
    return mock.patch("os.get_terminal_size", return_value=os.terminal_size((cols, 24)))


def test_zero_columns_floors_instead_of_returning_zero():
    # The bug: a real winsize-0 pty reports cols=0 without OSError; width must not be 0.
    with _fake_size(0):
        assert get_terminal_width() >= 40


def test_tiny_columns_are_floored():
    with _fake_size(5):
        assert get_terminal_width() == 40


def test_normal_width_passes_through():
    with _fake_size(80):
        assert get_terminal_width() == 80


def test_wide_terminal_is_capped_at_100():
    with _fake_size(1000):
        assert get_terminal_width() == 100


def test_oserror_still_falls_back_to_80():
    with mock.patch("os.get_terminal_size", side_effect=OSError):
        assert get_terminal_width() == 80


def test_header_box_does_not_collapse_under_zero_width():
    # The user-visible symptom: with cols=0 the top border was empty and the box became
    # `╭╮`. Floored width means the border spans the full box, not just the corners.
    with _fake_size(0):
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_header_box("Demo Agent", "/tmp/x")
        out = buf.getvalue()
        # the top border line carries real horizontal rule between the corners
        border = next(line for line in out.splitlines() if "╭" in line)
        assert border.count("─") >= 30, f"header box collapsed: {border!r}"


def test_separator_is_not_empty_under_zero_width():
    with _fake_size(0):
        assert "─" in separator("light")
