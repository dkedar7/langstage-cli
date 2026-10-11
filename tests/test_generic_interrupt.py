"""A generic ``interrupt(value)`` resumes with the RAW user value, not the
tool-review ``{"decisions": [...]}`` envelope (gh #99).

#82 / #95 fixed DISPLAYING a generic ``interrupt(...)`` payload. This is the next
gap: the value the CLI RESUMED the graph with was ALWAYS ``{"decisions": [...]}``,
regardless of what the interrupt asked for. So the canonical LangGraph "collect
input from a human" pattern — ``name = interrupt("What is your name?")`` — got
``{"decisions": [{"type": "approve"}]}`` back and produced the visibly wrong
``Hello, {'decisions': [{'type': 'approve'}]}!``.

The fix branches the RESUME on the same signal #82/#95 use to RENDER: a deepagents
tool-review interrupt (dict keyed ``action``) keeps the ``{"decisions": [...]}``
envelope; a generic/scalar ``interrupt(value)`` resumes with the raw value the user
gives, UNWRAPPED.
"""

import io
import textwrap
from contextlib import redirect_stdout

import pytest

pytest.importorskip("ag_ui_langgraph")
pytest.importorskip("fastapi")

from langstage_cli import cli  # noqa: E402
from langstage_cli.agui_stream import build_session_agent  # noqa: E402
from langstage_cli.cli import _is_generic_interrupt, run_single_turn_agui  # noqa: E402

# The canonical "ask a human for a value" agent from the issue: a node does
# `name = interrupt("What is your name?")` and greets with it. Resuming must return
# EXACTLY the value the user gave — `Hello, Alice!` — not an approval envelope.
_INPUT_AGENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import interrupt
    from langchain_core.messages import AIMessage

    def greet(state):
        name = interrupt("What is your name?")
        return {"messages": [AIMessage(content=f"Hello, {name}!")]}

    g = StateGraph(MessagesState)
    g.add_node("greet", greet)
    g.add_edge(START, "greet")
    g.add_edge("greet", END)
    graph = g.compile(checkpointer=MemorySaver())
    """
)

# A deepagents-style tool-REVIEW interrupt (dict keyed `action`) — the shape whose
# contract IS the `{"decisions": [...]}` protocol, which the fix must not regress.
_REVIEW_AGENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import interrupt
    from langchain_core.messages import AIMessage

    def ask(state):
        decision = interrupt({"action": "delete_file", "path": "/etc/hosts"})
        return {"messages": [AIMessage(content=f"Resumed with: {decision}")]}

    g = StateGraph(MessagesState)
    g.add_node("ask", ask)
    g.add_edge(START, "ask")
    g.add_edge("ask", END)
    graph = g.compile(checkpointer=MemorySaver())
    """
)


def _build(source: str):
    ns: dict = {}
    exec(source, ns)
    return build_session_agent(ns["graph"])


def test_is_generic_interrupt_classifies_shapes():
    # A deepagents ActionRequest (dict keyed `action`, or the legacy `tool`) is a
    # tool review — NOT generic.
    assert _is_generic_interrupt([{"action": "delete_file", "args": {}}]) is False
    assert _is_generic_interrupt([{"tool": "legacy", "args": {}}]) is False
    # A bare string / scalar (the `interrupt("What is your name?")` form) is generic.
    assert _is_generic_interrupt(["What is your name?"]) is True
    # A plain dict without an action/tool key is generic (no tool-review contract).
    assert _is_generic_interrupt([{"question": "How old are you?"}]) is True
    # An empty request has nothing to approve — treat as generic, never approve blind.
    assert _is_generic_interrupt([]) is True
    # A mixed batch is generic if ANY request isn't a real ActionRequest.
    assert _is_generic_interrupt([{"action": "x"}, "free text"]) is True


async def test_generic_interrupt_resumes_with_the_raw_user_value(monkeypatch):
    agent = _build(_INPUT_AGENT)

    # Simulate the interactive HITL menu: a real terminal, the user picks the single
    # "Provide a response" option, and types `Alice`.
    monkeypatch.setattr(cli, "_is_a_tty", lambda *a, **k: True)
    monkeypatch.setattr(cli, "select_option", lambda *a, **k: 0)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "Alice")

    buf = io.StringIO()
    with redirect_stdout(buf):
        _elapsed, had_error = await run_single_turn_agui(agent, "hi", "t-generic", interactive=True)
    out = buf.getvalue()

    assert had_error is False, out
    # The fix: interrupt("What is your name?") gets "Alice" back, UNWRAPPED.
    assert "Hello, Alice!" in out, out
    # The tool-review envelope must never leak into a generic interrupt's answer.
    assert "decisions" not in out, out
    assert "'type': 'approve'" not in out, out


async def test_generic_interrupt_accepts_a_structured_json_value(monkeypatch):
    # A user may type structured JSON; it survives as the parsed value, not a string
    # and not an envelope. Here `interrupt("What is your name?")` returns the list
    # `["Alice", "B"]`, so the greeting renders it verbatim.
    agent = _build(_INPUT_AGENT)
    monkeypatch.setattr(cli, "_is_a_tty", lambda *a, **k: True)
    monkeypatch.setattr(cli, "select_option", lambda *a, **k: 0)
    monkeypatch.setattr("builtins.input", lambda *a, **k: '["Alice", "B"]')

    buf = io.StringIO()
    with redirect_stdout(buf):
        _elapsed, had_error = await run_single_turn_agui(
            agent, "hi", "t-generic-json", interactive=True
        )
    out = buf.getvalue()
    assert had_error is False, out
    assert "Hello, ['Alice', 'B']!" in out, out
    assert "decisions" not in out, out


async def test_action_review_interrupt_still_resumes_with_decisions(monkeypatch):
    # No regression on #82/#95: a deepagents tool-review interrupt keeps the
    # {"decisions": [...]} envelope. The user picks "Approve all actions".
    agent = _build(_REVIEW_AGENT)
    monkeypatch.setattr(cli, "_is_a_tty", lambda *a, **k: True)
    monkeypatch.setattr(cli, "select_option", lambda *a, **k: 0)

    buf = io.StringIO()
    with redirect_stdout(buf):
        _elapsed, had_error = await run_single_turn_agui(
            agent, "please act", "t-review", interactive=True
        )
    out = buf.getvalue()

    assert had_error is False, out
    # The approval envelope is exactly what a tool-review interrupt should get back.
    assert "Resumed with:" in out, out
    assert "decisions" in out, out
    assert "approve" in out, out


async def test_generic_interrupt_no_interactive_avoids_the_decisions_envelope():
    # Under --no-interactive nobody can supply the requested value, so a generic
    # interrupt resumes with an empty value — NOT a {"decisions": [...]} approval it
    # never asked for. The graph still runs to completion (gh #99 / #32).
    agent = _build(_INPUT_AGENT)

    buf = io.StringIO()
    with redirect_stdout(buf):
        _elapsed, had_error = await run_single_turn_agui(
            agent, "hi", "t-generic-auto", interactive=False
        )
    out = buf.getvalue()

    assert had_error is False, out
    assert "decisions" not in out, out
    assert "Hello, {'decisions'" not in out, out
    # Resumed with an empty value -> `Hello, !` (a value the agent could have asked for).
    assert "Hello, !" in out, out


def test_langchain_hitl_middleware_request_is_a_tool_review():
    # LangChain 1.x's HumanInTheLoopMiddleware (and deepagents' interrupt_on, built on
    # it) keys the tool `name`, next to `args`. Reading only action/tool sent these
    # to the generic "Provide a response" menu instead of Approve/Reject.
    assert _is_generic_interrupt([{"name": "delete_file", "args": {"path": "/tmp/x"}}]) is False
    # `name` alone is not an ActionRequest: interrupt({"name": "Ada"}) stays generic.
    assert _is_generic_interrupt([{"name": "Ada"}]) is True


async def test_langchain_hitl_middleware_agent_gets_the_approval_menu(monkeypatch):
    from langchain.agents import create_agent
    from langchain.agents.middleware import HumanInTheLoopMiddleware
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool
    from langgraph.checkpoint.memory import MemorySaver

    @tool
    def delete_file(path: str) -> str:
        """Delete a file."""
        return f"deleted {path}"

    class FakeModel(FakeMessagesListChatModel):  # finished messages, no token stream
        def bind_tools(self, tools, **kwargs):
            return self

    model = FakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "delete_file", "args": {"path": "/tmp/x"}, "id": "c1"}],
            ),
            AIMessage(content="All done."),
        ]
    )
    graph = create_agent(
        model,
        tools=[delete_file],
        checkpointer=MemorySaver(),
        middleware=[HumanInTheLoopMiddleware(interrupt_on={"delete_file": True})],
    )
    menus = []

    def pick_first(options, prompt="Select an option:"):
        menus.append(options)
        return 0

    monkeypatch.setattr(cli, "_is_a_tty", lambda *a, **k: True)
    monkeypatch.setattr(cli, "select_option", pick_first)
    buf = io.StringIO()
    with redirect_stdout(buf):
        _elapsed, had_error = await run_single_turn_agui(
            build_session_agent(graph), "clean up", "t-hitl", interactive=True
        )
    out = buf.getvalue()

    assert had_error is False, out
    assert menus and menus[0][0] == "Approve all actions", menus
    assert "deleted /tmp/x" in out and "All done." in out, out
