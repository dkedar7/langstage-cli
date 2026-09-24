"""Message order and message boundaries in the rendered transcript (gh #119).

On a non-token-streaming agent, a message's tool call used to be emitted BEFORE that
same message's text, so the preamble ("Let me check the weather.") rendered after the
tool had run and the ``↳`` result was glued onto the preamble's line. langstage-core
1.0.36 emits each finished message when its node finishes, text first, and tags every
content frame with ``message_id``; the CLI starts a new block when that id changes and
never appends a tool result to an open text line.
"""

import io
import re
import textwrap
from contextlib import redirect_stdout

import pytest
from click.testing import CliRunner

pytest.importorskip("ag_ui_langgraph")

from langstage_cli import cli  # noqa: E402
from langstage_cli.agui_stream import build_session_agent  # noqa: E402
from langstage_cli.cli import main, print_chunk, run_single_turn_agui  # noqa: E402

# The #91/#119 agent, verbatim.
_TOOL_AGENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langchain_core.messages import AIMessage, ToolMessage

    def call_tool(state):
        return {"messages": [AIMessage(content="Let me check the weather.", tool_calls=[
            {"name": "get_weather", "args": {"city": "Paris"}, "id": "call_1"}])]}
    def run_tool(state):
        return {"messages": [ToolMessage(content="Sunny, 24C", tool_call_id="call_1")]}
    def final(state):
        return {"messages": [AIMessage(content="The weather in Paris is sunny, 24C.")]}

    g = StateGraph(MessagesState)
    for n, f in [("call_tool", call_tool), ("run_tool", run_tool), ("final", final)]:
        g.add_node(n, f)
    g.add_edge(START, "call_tool"); g.add_edge("call_tool", "run_tool")
    g.add_edge("run_tool", "final"); g.add_edge("final", END)
    graph = g.compile()
    """
)

# One node, two finished messages: only message_id can tell them apart.
_TWO_MESSAGES_ONE_NODE = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langchain_core.messages import AIMessage

    def respond(state):
        return {"messages": [AIMessage(content="First message."),
                             AIMessage(content="Second message.")]}

    g = StateGraph(MessagesState)
    g.add_node("respond", respond)
    g.add_edge(START, "respond")
    g.add_edge("respond", END)
    graph = g.compile()
    """
)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


async def _render(source: str, monkeypatch) -> str:
    ns: dict = {}
    exec(source, ns)
    agent = build_session_agent(ns["graph"])
    monkeypatch.setattr(cli, "Spinner", lambda *a, **k: None)
    buf = io.StringIO()
    with redirect_stdout(buf):
        await run_single_turn_agui(agent, "weather in paris?", "t-119", interactive=False)
    return _ANSI.sub("", buf.getvalue())


async def test_preamble_renders_before_its_tool_call_and_result_is_not_glued(monkeypatch):
    out = await _render(_TOOL_AGENT, monkeypatch)
    pre = out.index("Let me check the weather.")
    call = out.index("● get_weather")
    result = out.index("↳ Sunny, 24C")
    final = out.index("The weather in Paris is sunny, 24C.")
    assert pre < call < result < final, out
    # The result sits on its own line, never at the end of the preamble's line.
    result_line = next(ln for ln in out.splitlines() if "↳ Sunny, 24C" in ln)
    assert "Let me check" not in result_line, out


async def test_two_messages_from_one_node_are_two_blocks(monkeypatch):
    out = await _render(_TWO_MESSAGES_ONE_NODE, monkeypatch)
    assert "First message.Second message." not in out, out
    lines = [ln for ln in out.splitlines() if "message." in ln]
    assert lines == ["⏺ First message.", "⏺ Second message."], out


def test_piped_output_separates_messages(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "two119.py").write_text(_TWO_MESSAGES_ONE_NODE, encoding="utf-8")
    (tmp_path / "tool119.py").write_text(_TOOL_AGENT, encoding="utf-8")

    r = CliRunner().invoke(main, ["-a", "two119.py:graph", "hi"])
    assert r.exit_code == 0, r.output
    assert r.stdout == "First message.\nSecond message.\n", repr(r.stdout)

    r = CliRunner().invoke(main, ["-a", "tool119.py:graph", "hi"])
    assert r.exit_code == 0, r.output
    assert r.stdout == ("Let me check the weather.\nThe weather in Paris is sunny, 24C.\n"), repr(
        r.stdout
    )


def test_tokens_of_one_message_still_join(monkeypatch):
    # A token-streamed message: same node, same message_id -> one unbroken run.
    monkeypatch.setattr(cli, "_QUIET", True)
    print_chunk._streaming_text = False
    buf = io.StringIO()
    with redirect_stdout(buf):
        for tok in ("Hel", "lo", " world"):
            print_chunk({"status": "streaming", "chunk": tok, "node": "model", "message_id": "m1"})
    assert buf.getvalue() == "Hello world", repr(buf.getvalue())
