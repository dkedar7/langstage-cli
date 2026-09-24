"""Answering an ``interrupt()`` must not leak ag_ui_langgraph resume warnings (gh #103,
#126, #137).

Two WARNINGs used to land above the (correct) post-resume reply on every HITL resume:

- ``forwardedProps.command.resume is deprecated; please send RunAgentInput.resume[]``
  on every approve/reject/answer (gh #126), and
- ``failed to parse [legacy] resume_input as JSON, treating as string`` on every
  free-text answer and every ``--no-interactive`` empty auto-resume (gh #103). The CLI
  once hid it with a substring log filter, which silently stopped matching when the
  upstream wording gained "legacy" (gh #137).

Both came from core resuming over the deprecated ``forwarded_props.command.resume``
wire. langstage-core 1.0.36 resumes over the standard ``RunAgentInput.resume[]``, so
neither record is emitted at all, and the CLI's filter is gone. These tests capture
every ``ag_ui_langgraph`` log record (root-level, so nothing can be filtered away
before we see it) on the real resume paths.
"""

import io
import logging
import textwrap
from contextlib import redirect_stdout

import pytest
from click.testing import CliRunner

pytest.importorskip("ag_ui_langgraph")
pytest.importorskip("fastapi")

from langstage_cli import cli  # noqa: E402
from langstage_cli.agui_stream import build_session_agent  # noqa: E402
from langstage_cli.cli import main, run_single_turn_agui  # noqa: E402

_LEAKS = ("deprecated", "failed to parse")

# The #103 agent: a plain-string interrupt answered with free text.
_STRING_INTERRUPT_AGENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import interrupt
    from langchain_core.messages import AIMessage

    def ask(state):
        answer = interrupt("What is your favorite color?")
        return {"messages": [AIMessage(content=f"You chose: {answer}")]}

    g = StateGraph(MessagesState)
    g.add_node("ask", ask)
    g.add_edge(START, "ask")
    g.add_edge("ask", END)
    graph = g.compile(checkpointer=MemorySaver())
    """
)

# The #126 agent: an action-review interrupt that is approved.
_ACTION_INTERRUPT_AGENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import interrupt
    from langchain_core.messages import AIMessage

    def ask(state):
        decision = interrupt({"action": "delete_all", "question": "Approve?"})
        return {"messages": [AIMessage(content=f"Decision was: {decision!r}")]}

    g = StateGraph(MessagesState)
    g.add_node("ask", ask)
    g.add_edge(START, "ask")
    g.add_edge("ask", END)
    graph = g.compile(checkpointer=MemorySaver())
    """
)

# The #137 non-interactive repro: no own checkpointer, plain dict question.
_NO_INTERACTIVE_AGENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langgraph.types import interrupt
    from langchain_core.messages import AIMessage

    def ask(state):
        answer = interrupt({"question": "Approve?"})
        return {"messages": [AIMessage(content=f"Decision was: {answer!r}")]}

    g = StateGraph(MessagesState)
    g.add_node("ask", ask)
    g.add_edge(START, "ask")
    g.add_edge("ask", END)
    graph = g.compile()
    """
)


def _agui_leaks(caplog) -> list:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("ag_ui_langgraph") and any(k in r.getMessage() for k in _LEAKS)
    ]


async def _run_interactive(source: str, thread: str, monkeypatch, choice: int, typed: str = ""):
    ns: dict = {}
    exec(source, ns)
    agent = build_session_agent(ns["graph"])
    monkeypatch.setattr(cli, "_is_a_tty", lambda *a, **k: True)
    monkeypatch.setattr(cli, "select_option", lambda *a, **k: choice)
    monkeypatch.setattr("builtins.input", lambda *a, **k: typed)
    buf = io.StringIO()
    with redirect_stdout(buf):
        _elapsed, had_error = await run_single_turn_agui(agent, "start", thread, interactive=True)
    return buf.getvalue(), had_error


async def test_free_text_answer_leaks_no_resume_warning(monkeypatch, caplog):
    # gh #103 / #137: free text (not JSON) is the case that tripped the parse warning.
    caplog.set_level(logging.WARNING)
    out, had_error = await _run_interactive(
        _STRING_INTERRUPT_AGENT, "t-color", monkeypatch, choice=0, typed="blue"
    )
    assert had_error is False, out
    assert "You chose: blue" in out, out  # the resumed value is still exact
    assert _agui_leaks(caplog) == [], _agui_leaks(caplog)


async def test_approve_leaks_no_deprecation_warning(monkeypatch, caplog):
    # gh #126: every approve used to log "forwardedProps.command.resume is deprecated".
    caplog.set_level(logging.WARNING)
    out, had_error = await _run_interactive(
        _ACTION_INTERRUPT_AGENT, "t-approve", monkeypatch, choice=0
    )
    assert had_error is False, out
    assert "Decision was:" in out and "approve" in out, out
    assert _agui_leaks(caplog) == [], _agui_leaks(caplog)


async def test_reject_leaks_no_deprecation_warning(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    out, had_error = await _run_interactive(
        _ACTION_INTERRUPT_AGENT, "t-reject", monkeypatch, choice=1
    )
    assert had_error is False, out
    assert "reject" in out, out
    assert _agui_leaks(caplog) == [], _agui_leaks(caplog)


def test_no_interactive_auto_resume_leaks_nothing(tmp_path, monkeypatch, caplog):
    # gh #137's deterministic repro: `--no-interactive` auto-resumes a generic interrupt
    # with "" (not JSON), end to end through main().
    caplog.set_level(logging.WARNING)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hitl137.py").write_text(_NO_INTERACTIVE_AGENT, encoding="utf-8")
    r = CliRunner().invoke(main, ["-a", "hitl137.py:graph", "--no-interactive", "go"])
    assert r.exit_code == 0, r.output
    assert "Decision was: ''" in r.stdout, r.output
    assert _agui_leaks(caplog) == [], _agui_leaks(caplog)
    assert not any(k in r.output for k in _LEAKS), r.output


def test_the_substring_log_filter_is_gone():
    # The brittle #103 stopgap is deleted, not merely unused: core no longer emits the
    # record, so there is nothing to filter (and nothing to silently stop matching).
    assert not hasattr(cli, "_DropResumeJSONWarning")
    assert not hasattr(cli, "_quiet_agui_resume_json_warning")
