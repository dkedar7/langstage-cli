"""The arrow-key approval menu never approves on a key that doesn't mean "approve"
(gh #211).

- Digits were ignored, so typing ``3`` then Enter (meaning **Exit**, the dumb-terminal
  menu's numbering) confirmed the highlighted **Approve**. A digit now moves the
  highlight to that option; Enter still confirms.
- Ctrl-D was ignored (the menu redrew) and Esc blocked waiting for a CSI byte, so it
  swallowed the next Ctrl-C. Ctrl-D, Esc and ``q`` now leave the menu like Ctrl-C:
  exit 2, the turn stays paused.
"""

import io
import os
import sys
import textwrap
from contextlib import redirect_stdout

import pytest
from click.testing import CliRunner

from langstage_cli import cli
from langstage_cli.cli import main

_OPTIONS = ["Approve all actions", "Provide custom decision (JSON)", "Exit"]


def _keys(monkeypatch, *keys):
    it = iter(keys)
    seen = []

    def fake_get_key():
        key = next(it)
        seen.append(key)
        return key

    monkeypatch.setattr(cli, "get_key", fake_get_key)
    return seen


def _select(options=_OPTIONS):
    buf = io.StringIO()
    with redirect_stdout(buf):
        choice = cli.select_option(options, "How would you like to proceed?")
    return choice, buf.getvalue()


# ---- digits ------------------------------------------------------------------------


@pytest.mark.parametrize("digit, index", [("1", 0), ("2", 1), ("3", 2)])
def test_a_digit_then_enter_picks_that_option(digit, index, monkeypatch):
    _keys(monkeypatch, digit, "enter")
    assert _select()[0] == index


def test_three_enter_picks_exit_not_approve(monkeypatch):
    # The issue's repro: "3" means Exit; it used to approve.
    _keys(monkeypatch, "3", "enter")
    choice, out = _select()
    assert _OPTIONS[choice] == "Exit"
    assert "❯ Exit" in out  # the highlight moved, so the user sees what Enter picks


@pytest.mark.parametrize("key", ["0", "4", "9", "x", "", " "])
def test_out_of_range_digits_and_other_keys_are_ignored(key, monkeypatch):
    # Ignored keys don't change the highlight, and don't redraw.
    _keys(monkeypatch, "down", key, "enter")
    choice, out = _select()
    assert choice == 1
    assert out.count("\033[3A") == 1  # one redraw, for the "down"


def test_digit_then_arrows_still_navigate(monkeypatch):
    _keys(monkeypatch, "3", "up", "enter")
    assert _select()[0] == 1


def test_the_menu_shows_its_keys(monkeypatch):
    _keys(monkeypatch, "enter")
    _, out = _select()
    assert "1-3 to choose" in out
    assert "Esc to leave" in out


# ---- leaving -----------------------------------------------------------------------


@pytest.mark.parametrize("key", ["ctrl-d", "esc", "q", "Q"])
def test_leave_keys_exit_paused_and_restore_the_cursor(key, monkeypatch):
    _keys(monkeypatch, "down", key)
    buf = io.StringIO()
    with redirect_stdout(buf), pytest.raises(SystemExit) as exc:
        cli.select_option(_OPTIONS, "How would you like to proceed?")
    assert exc.value.code == 2
    assert buf.getvalue().endswith("\033[?25h")


_ACTION_AGENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import interrupt
    from langchain_core.messages import AIMessage

    def ask(state):
        decision = interrupt({"action": "delete_file", "path": "/etc/hosts"})
        return {"messages": [AIMessage(content=f"Decision was: {decision!r}")]}

    g = StateGraph(MessagesState)
    g.add_node("ask", ask)
    g.add_edge(START, "ask")
    g.add_edge("ask", END)
    graph = g.compile(checkpointer=MemorySaver())
    """
)


@pytest.mark.parametrize(
    "keys", [("3", "enter"), ("esc",), ("ctrl-d",)], ids=["3-enter", "esc", "ctrl-d"]
)
def test_single_shot_never_resumes_on_a_leave(keys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_is_a_tty", lambda *a, **k: True)
    (tmp_path / "hitl_agent.py").write_text(_ACTION_AGENT, encoding="utf-8")
    _keys(monkeypatch, *keys)
    r = CliRunner().invoke(main, ["-a", "hitl_agent.py:graph", "go"])
    assert r.exit_code == 2, r.output
    assert "Decision was" not in r.output  # nothing was resumed


# ---- escape sequences ----------------------------------------------------------------


def _feed(data: str):
    """A ``read_byte``/``pending`` pair over a fixed byte string (the rest of the input)."""
    buf = list(data)

    def read_byte():
        return buf.pop(0)

    def pending():
        return bool(buf)

    return read_byte, pending, buf


@pytest.mark.parametrize(
    "rest, key",
    [
        ("", "esc"),  # a lone Esc returns at once
        ("[A", "up"),
        ("[B", "down"),
        ("OA", "up"),  # application cursor mode
        ("OB", "down"),
        ("[1;5A", ""),  # Ctrl+Up: read whole, ignored
        ("[H", ""),  # Home
        ("[15~", ""),  # F5
        ("x", ""),  # Alt+x
    ],
)
def test_escape_sequences(rest, key):
    read_byte, pending, buf = _feed(rest)
    assert cli._read_escape_sequence(read_byte, pending) == key
    assert buf == []  # the whole sequence was consumed, nothing leaks into the next key


def test_escape_sequence_stops_at_its_final_byte():
    read_byte, pending, buf = _feed("[A3")
    assert cli._read_escape_sequence(read_byte, pending) == "up"
    assert buf == ["3"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pty")
def test_get_key_lone_esc_does_not_swallow_the_next_key(monkeypatch):
    # The real get_key over a pty: Esc alone returns "esc", and the Ctrl-C typed after
    # it is still there for the next read (it used to be consumed as the CSI byte).
    import pty

    master, slave = pty.openpty()
    try:
        stdin = os.fdopen(slave, "r", closefd=False)
        monkeypatch.setattr(sys, "stdin", stdin)
        os.write(master, b"\x1b")
        assert cli.get_key() == "esc"
        os.write(master, b"\x03")
        assert cli.get_key() == "ctrl-c"
        os.write(master, b"\x1b[B")
        assert cli.get_key() == "down"
        os.write(master, b"3")
        assert cli.get_key() == "3"
        os.write(master, b"\x04")
        assert cli.get_key() == "ctrl-d"
    finally:
        os.close(master)
        os.close(slave)
