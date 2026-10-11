"""Leaving the HITL approval menu never reports success, and a JSON typo never sends a
decision (gh #174).

- Ctrl-C at the menu exited 0 ("the turn completed"), so `langstage-cli ... && next`
  ran the next step on an aborted approval. It now exits 2 (paused), like **Exit**,
  and the REPL ends with the same `Goodbye!` as /quit.
- Invalid JSON at "Provide custom decision (JSON)" sent a reject for every pending
  action and resumed the agent. It now shows the parse error and asks again; an empty
  line goes back to the menu with nothing sent.
"""

import io
import textwrap
from contextlib import redirect_stdout

import pytest
from click.testing import CliRunner

from langstage_cli import cli
from langstage_cli.cli import main

# An action-review interrupt (a dict keyed "action"), answered through the menu.
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

_EDIT = '{"type": "edit", "args": {"path": "/tmp/hosts"}}'


def _keys(monkeypatch, *keys):
    """Feed ``get_key`` a fixed sequence of keypresses (or exceptions to raise)."""
    it = iter(keys)

    def fake_get_key():
        key = next(it)
        if isinstance(key, BaseException):
            raise key
        return key

    monkeypatch.setattr(cli, "get_key", fake_get_key)


def _typed(monkeypatch, *lines):
    """Feed ``input()`` a fixed sequence of lines (or exceptions to raise)."""
    it = iter(lines)

    def fake_input(*_a, **_k):
        line = next(it)
        if isinstance(line, BaseException):
            raise line
        return line

    monkeypatch.setattr("builtins.input", fake_input)


def _choices(monkeypatch, *choices):
    """Make the menu return these choices in order; return the list of prompts shown."""
    it = iter(choices)
    shown = []

    def fake_select(options, prompt=""):
        shown.append(prompt)
        return next(it)

    monkeypatch.setattr(cli, "select_option", fake_select)
    return shown


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(cli, "_is_a_tty", lambda *a, **k: True)


# ---- Ctrl-C at the menu exits 2 (paused) ---------------------------------------------


@pytest.mark.parametrize("ctrl_c", ["ctrl-c", KeyboardInterrupt()], ids=["key", "sigint"])
def test_ctrl_c_at_the_menu_exits_paused_and_restores_the_cursor(ctrl_c, monkeypatch):
    _keys(monkeypatch, "down", ctrl_c)
    buf = io.StringIO()
    with redirect_stdout(buf), pytest.raises(SystemExit) as exc:
        cli.select_option(["Approve", "Exit"], "How would you like to proceed?")
    assert exc.value.code == 2
    out = buf.getvalue()
    assert out.startswith("\033[?25l")  # cursor hidden while the menu is up ...
    assert out.endswith("\033[?25h")  # ... and shown again on the way out


@pytest.mark.parametrize("is_generic", [False, True])
def test_ctrl_c_at_the_approval_menu_exits_2(is_generic, tty, monkeypatch):
    _keys(monkeypatch, "ctrl-c")
    with redirect_stdout(io.StringIO()), pytest.raises(SystemExit) as exc:
        cli.handle_interrupt_input(2, is_generic=is_generic)
    assert exc.value.code == 2


@pytest.mark.parametrize("abort", [KeyboardInterrupt(), EOFError()], ids=["ctrl-c", "eof"])
@pytest.mark.parametrize("is_generic", [False, True])
def test_abandoning_the_answer_prompt_exits_2(abort, is_generic, tty, monkeypatch):
    # Ctrl-C / end of input at the line prompt after the menu is the same abort.
    _choices(monkeypatch, 0 if is_generic else 2)
    _typed(monkeypatch, abort)
    with redirect_stdout(io.StringIO()), pytest.raises(SystemExit) as exc:
        cli.handle_interrupt_input(1, is_generic=is_generic)
    assert exc.value.code == 2


def test_ctrl_c_at_the_menu_single_shot_exits_2(tmp_path, monkeypatch, tty):
    # The issue's repro: `langstage-cli -a agent.py:graph "go" && next-step`.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hitl_agent.py").write_text(_ACTION_AGENT, encoding="utf-8")
    _keys(monkeypatch, "ctrl-c")
    r = CliRunner().invoke(main, ["-a", "hitl_agent.py:graph", "go"])
    assert r.exit_code == 2, r.output
    assert "Decision was" not in r.output  # nothing was resumed


@pytest.mark.parametrize("leave", ["ctrl-c", "exit"])
def test_leaving_the_menu_in_the_repl_says_goodbye_and_exits_2(
    leave, tmp_path, monkeypatch, tty, repl_via_stdin
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hitl_agent.py").write_text(_ACTION_AGENT, encoding="utf-8")
    if leave == "ctrl-c":
        _keys(monkeypatch, "ctrl-c")
    else:
        _keys(monkeypatch, "down", "down", "down", "enter")  # Exit is the 4th option
    r = CliRunner().invoke(main, ["-a", "hitl_agent.py:graph"], input="go\n")
    assert r.exit_code == 2, r.output
    assert "Goodbye!" in r.output
    assert "Decision was" not in r.output


# ---- Invalid custom-decision JSON re-prompts ----------------------------------------


def test_invalid_then_valid_json_sends_the_valid_decision(tty, monkeypatch):
    shown = _choices(monkeypatch, 2)
    _typed(monkeypatch, "oops{", _EDIT)
    buf = io.StringIO()
    with redirect_stdout(buf):
        resume = cli.handle_interrupt_input(2)
    edit = {"type": "edit", "args": {"path": "/tmp/hosts"}}
    assert resume == {"decisions": [edit, edit]}
    assert "Invalid JSON" in buf.getvalue()
    assert "Nothing was sent" in buf.getvalue()
    assert len(shown) == 1  # re-prompted in place, not sent back to the menu


def test_invalid_json_then_empty_line_goes_back_to_the_menu(tty, monkeypatch):
    # Back at the menu the user picks Exit: no decision of any kind was returned.
    shown = _choices(monkeypatch, 2, 3)
    _typed(monkeypatch, "oops{", "")
    with redirect_stdout(io.StringIO()), pytest.raises(SystemExit) as exc:
        cli.handle_interrupt_input(2)
    assert exc.value.code == 2
    assert len(shown) == 2  # the menu came back


def test_empty_line_then_approve_sends_only_the_approval(tty, monkeypatch):
    _choices(monkeypatch, 2, 0)
    _typed(monkeypatch, "  ")
    with redirect_stdout(io.StringIO()):
        resume = cli.handle_interrupt_input(1)
    assert resume == {"decisions": [{"type": "approve"}]}


def test_json_null_is_a_decision_not_back_to_menu(tty, monkeypatch):
    # The "go back" signal is an empty line, so valid JSON like `null` still resumes.
    shown = _choices(monkeypatch, 2)
    _typed(monkeypatch, "null")
    with redirect_stdout(io.StringIO()):
        resume = cli.handle_interrupt_input(1)
    assert resume == {"decisions": [None]}
    assert len(shown) == 1


async def test_invalid_json_typo_resumes_the_agent_with_the_corrected_decision(tty, monkeypatch):
    # End to end: the graph receives the corrected decision, never a reject.
    pytest.importorskip("ag_ui_langgraph")
    from langstage_cli.agui_stream import build_session_agent

    ns: dict = {}
    exec(_ACTION_AGENT, ns)
    agent = build_session_agent(ns["graph"])
    _choices(monkeypatch, 2)
    _typed(monkeypatch, "oops{", _EDIT)
    buf = io.StringIO()
    with redirect_stdout(buf):
        _elapsed, had_error = await cli.run_single_turn_agui(agent, "go", "t-174", interactive=True)
    out = buf.getvalue()
    assert had_error is False, out
    assert "Decision was" in out and "'edit'" in out, out
    assert "reject" not in out, out
