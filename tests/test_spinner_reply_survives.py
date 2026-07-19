"""The interactive (TTY) turn must not erase the agent's reply (gh #84).

`run_single_turn_agui()` stops the spinner twice per turn: once on the first chunk
(correct — that clears the "Thinking…" animation) and again in its `finally`.
`Spinner.stop()` ends with `print("\\r\\033[2K")` — CR + "erase entire line" — and
because the reply is streamed with `end=""` and never terminated by a newline, the
cursor is still parked on the reply's last line when the `finally` runs. The second
clear therefore wiped the reply: on a real terminal the user saw only their own
prompt and the timing line.

Testing this needs the *TTY* path, not the piped one: the spinner only exists when
`_QUIET` is False (`cli.py`: `spinner = None if _QUIET else Spinner("Thinking")`),
which is exactly why the piped/`--quiet` suites and the README one-liner never
caught it. The issue's repro drives a real PTY and renders it through `pyte`, but
`pty.fork()` is Unix-only. So instead of faking a terminal we assert on the thing a
terminal actually consumes — the emitted byte stream — replaying it through the
minimal VT model below (CR, CSI 2K, newline: the three sequences at issue). A test
that merely counted `stop()` calls would couple to the implementation; these assert
the rendered screen, which is the observable defect.
"""

import asyncio
import contextlib
import io

import pytest

from langstage_cli import cli
from langstage_cli import agui_stream


def render(raw: str) -> list[str]:
    """Replay `raw` the way a VT terminal would and return the visible lines.

    Models only what this bug turns on: `\\r` (cursor to column 0), `CSI 2K`
    (erase entire line, cursor unmoved), `\\n` (next line), and overwrite-in-place
    for printable characters. Every other CSI sequence (color, cursor show/hide) is
    consumed without affecting the display, exactly as a terminal does.
    """
    lines = [""]
    col = 0
    i = 0
    while i < len(raw):
        if raw.startswith("\033[2K", i):
            lines[-1] = ""  # erase entire line; cursor column is unchanged
            i += 4
            continue
        if raw.startswith("\033[", i):  # any other CSI — skip to its final byte
            j = i + 2
            while j < len(raw) and not raw[j].isalpha():
                j += 1
            i = j + 1
            continue
        ch = raw[i]
        i += 1
        if ch == "\r":
            col = 0
        elif ch == "\n":
            lines.append("")
            col = 0
        else:
            line = lines[-1].ljust(col)
            lines[-1] = line[:col] + ch + line[col + 1 :]
            col += 1
    return lines


def _stream_of(*chunks):
    """Build a stand-in for `agui_stream_updates` yielding the given chunks."""

    async def _fake(agent, message, thread_id, resume=None):
        for chunk in chunks:
            yield chunk

    return _fake


def _run_turn(monkeypatch, *chunks) -> list[str]:
    """Drive one interactive turn, capturing everything written to stdout (spinner
    thread included) and returning the rendered screen."""
    monkeypatch.setattr(agui_stream, "agui_stream_updates", _stream_of(*chunks))
    assert cli._QUIET is False, "the spinner — and thus the bug — only exists on the TTY path"

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        duration, _ = asyncio.run(cli.run_single_turn_agui(object(), "hi", "t1"))
        # The real loop prints the timing immediately after the turn; it is what
        # scrolled over the erased reply, so keep it in the captured stream.
        cli.print_timing(duration)
    return render(buffer.getvalue())


def test_single_line_reply_survives_the_turn(monkeypatch):
    """A one-line reply — the common case — vanished entirely before the fix."""
    screen = _run_turn(
        monkeypatch,
        {"status": "streaming", "chunk": "SENTINEL-REPLY-TEXT", "node": "agent"},
        {"status": "complete"},
    )
    assert any("SENTINEL-REPLY-TEXT" in line for line in screen), (
        f"the reply was erased from the rendered terminal; screen={screen!r}"
    )
    # And the spinner's own line is gone — the first stop() still does its job.
    assert not any("Thinking" in line for line in screen), screen


def test_multi_line_reply_keeps_its_last_line(monkeypatch):
    """A multi-line reply lost only its LAST line — the single-line `CSI 2K`."""
    screen = _run_turn(
        monkeypatch,
        {"status": "streaming", "chunk": "LINE-ONE\nLINE-TWO\nLINE-THREE-LAST", "node": "agent"},
        {"status": "complete"},
    )
    for expected in ("LINE-ONE", "LINE-TWO", "LINE-THREE-LAST"):
        assert any(expected in line for line in screen), (
            f"{expected!r} was erased from the rendered terminal; screen={screen!r}"
        )


def test_no_line_clear_is_emitted_after_reply_text(monkeypatch):
    """Byte-level invariant: once reply text has been written, no erase-line escape
    may be emitted before a newline returns the cursor to a fresh line."""
    monkeypatch.setattr(
        agui_stream,
        "agui_stream_updates",
        _stream_of(
            {"status": "streaming", "chunk": "SENTINEL-REPLY-TEXT", "node": "agent"},
            {"status": "complete"},
        ),
    )
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        asyncio.run(cli.run_single_turn_agui(object(), "hi", "t1"))
    raw = buffer.getvalue()

    tail = raw[raw.index("SENTINEL-REPLY-TEXT") + len("SENTINEL-REPLY-TEXT") :]
    dangerous = tail.split("\n")[0]  # only what lands on the reply's own line
    assert "\033[2K" not in dangerous, (
        f"a line-clear escape follows the reply on its own line: tail={tail!r}"
    )


def test_turn_with_no_chunks_still_clears_the_spinner_line(monkeypatch):
    """The `finally` exists for the error/empty turn: when nothing streams, the
    spinner was never stopped on a first chunk, so its line must still be cleared
    rather than leaving a dangling "Thinking…" on screen."""
    screen = _run_turn(monkeypatch)
    assert not any("Thinking" in line for line in screen), (
        f"a dangling spinner line was left on screen; screen={screen!r}"
    )


def test_spinner_stop_is_idempotent_and_clears_once(monkeypatch):
    """Directly: the first stop() clears the line, a repeat stop() writes nothing."""
    spinner = cli.Spinner("Thinking")
    spinner.start()

    first = io.StringIO()
    with contextlib.redirect_stdout(first):
        spinner.stop()
    assert "\033[2K" in first.getvalue()

    second = io.StringIO()
    with contextlib.redirect_stdout(second):
        spinner.stop()
    assert second.getvalue() == "", "a repeat stop() must not emit another line-clear"

    # A restarted spinner clears again — the guard is per-run, not permanent.
    spinner.start()
    third = io.StringIO()
    with contextlib.redirect_stdout(third):
        spinner.stop()
    assert "\033[2K" in third.getvalue()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("abc", ["abc"]),
        ("abc\r\033[2K", [""]),  # the bug's exact sequence erases the line
        ("abc\ndef\r\033[2K", ["abc", ""]),  # ...only the current line
        ("\033[36mabc\033[0m", ["abc"]),  # color is invisible chrome
        ("ab\rX", ["Xb"]),  # CR overwrites in place
    ],
)
def test_render_models_the_terminal(raw, expected):
    """Guard the guard: the VT model must behave like a real terminal, or the
    assertions above would be meaningless."""
    assert render(raw) == expected
