"""The tool-call card shows every argument, by name (gh #184).

It used to show only the first argument's value, so ``add(a=17, b=25)`` rendered as
``add └─ 17`` and ``transfer_funds(to, amount, currency)`` as ``└─ alice``, in default
and ``-v`` output alike. Under ``--no-interactive`` the card is the only record of
what ran.
"""

import io
import re
from contextlib import redirect_stdout

import pytest

pytest.importorskip("ag_ui_langgraph")

from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.graph.message import MessagesState  # noqa: E402

from langstage_cli import cli  # noqa: E402
from langstage_cli.agui_stream import build_session_agent  # noqa: E402
from langstage_cli.cli import get_tool_arg_preview, run_single_turn_agui  # noqa: E402

_SGR = re.compile(r"\x1b\[[0-9;]*m")
TRANSFER = {"to": "alice", "amount": 1000000, "currency": "USD"}


def test_every_argument_is_named():
    assert get_tool_arg_preview({"a": 17, "b": 25}) == "a=17, b=25"
    assert get_tool_arg_preview(TRANSFER) == "to=alice, amount=1000000, currency=USD"


def test_a_single_argument_stays_a_bare_value():
    assert get_tool_arg_preview({"path": "docs/a.md"}) == "docs/a.md"
    assert get_tool_arg_preview({}) == ""


def test_values_are_capped_but_never_dropped():
    long = "x" * 200
    preview = get_tool_arg_preview({"path": "a.md", "content": long, "mode": "w"})
    assert preview == f"path=a.md, content={'x' * 50}..., mode=w"
    # Structured values read as JSON, not Python reprs.
    assert get_tool_arg_preview({"ids": [1, 2], "opts": {"k": "v"}}) == 'ids=[1, 2], opts={"k": "v"}'


def test_verbose_shows_values_in_full():
    long = "y" * 200
    assert get_tool_arg_preview({"path": "a.md", "content": long}, verbose=True) == f"path=a.md, content={long}"
    assert get_tool_arg_preview({"q": long}, verbose=True) == long


def test_names_and_values_are_escaped():
    preview = get_tool_arg_preview({"p\x1b[2K": "a\x1b]0;t\x07", "b": "c\nd"})
    assert "\x1b" not in preview and "\x07" not in preview and "\n" not in preview
    assert preview == r"p\x1b[2K=a\x1b]0;t\x07, b=c\nd"


def _transfer_graph():
    def call(state):
        tc = {"id": "t1", "name": "transfer_funds", "args": TRANSFER}
        return {"messages": [AIMessage(content="", tool_calls=[tc])]}

    def tool(state):
        return {"messages": [ToolMessage(content="ok", tool_call_id="t1")]}

    def reply(state):
        return {"messages": [AIMessage(content="Transferred.")]}

    g = StateGraph(MessagesState)
    for n, f in [("call", call), ("tool", tool), ("reply", reply)]:
        g.add_node(n, f)
    g.add_edge(START, "call")
    g.add_edge("call", "tool")
    g.add_edge("tool", "reply")
    g.add_edge("reply", END)
    return g.compile()


@pytest.mark.parametrize("verbose", [False, True])
async def test_card_in_a_real_turn(monkeypatch, verbose):
    """The issue's keyless repro, through the AG-UI stream the CLI renders."""
    monkeypatch.setattr(cli, "Spinner", lambda *a, **k: None)
    buf = io.StringIO()
    with redirect_stdout(buf):
        await run_single_turn_agui(build_session_agent(_transfer_graph()), "pay alice", "t-184",
                                   interactive=False, verbose=verbose)
    plain = _SGR.sub("", buf.getvalue())
    assert "● transfer_funds" in plain, plain
    assert "└─ to=alice, amount=1000000, currency=USD" in plain, plain
