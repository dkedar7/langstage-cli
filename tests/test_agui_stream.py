"""Tests for the in-process AG-UI streaming path (ADR 0002, ADR 0003).

Started life behind the experimental `--agui` flag; since langstage-core 1.0 this is
the only streaming path and that flag is a deprecated no-op (gh #88).

Skipped unless the agui extra is installed. The dev extra pulls it so CI runs these.
"""

import asyncio
from typing import Iterator, List

import pytest

pytest.importorskip("ag_ui_langgraph")
pytest.importorskip("fastapi")

from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.graph import END, START, MessagesState, StateGraph  # noqa: E402
from langgraph.types import interrupt  # noqa: E402
from langstage_core import load_agent_spec  # noqa: E402

from langstage_cli.agui_stream import agui_stream_updates, build_session_agent  # noqa: E402


def _collect(agent, msg: str) -> List[dict]:
    async def go():
        return [c async for c in agui_stream_updates(agent, msg, "t-test")]

    return asyncio.run(go())


def test_text_parity_on_demo_stub():
    """The keyless echo stub round-trips through AG-UI and reflects the input."""
    agent = build_session_agent(load_agent_spec("langstage_core.demo.stub:graph"))
    chunks = _collect(agent, "hello agui test")
    text = "".join(c["chunk"] for c in chunks if "chunk" in c)
    assert "hello agui test" in text
    assert chunks[-1]["status"] == "complete"


@tool
def get_weather(city: str) -> str:
    """Get the weather."""
    return "Sunny, 72F"


class _FakeToolModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "fake-tool"

    def bind_tools(self, tools, **kwargs):
        return self

    def _stream(
        self, messages: List[BaseMessage], stop=None, run_manager=None, **kwargs
    ) -> Iterator[ChatGenerationChunk]:
        if any(isinstance(m, ToolMessage) for m in messages):
            for tok in ["It's ", "72F."]:
                yield ChatGenerationChunk(message=AIMessageChunk(content=tok))
        else:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[{"name": "get_weather", "args": "", "id": "c1", "index": 0}],
                )
            )
            for seg in ('{"city": ', '"Portland"}'):
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        tool_call_chunks=[{"name": None, "args": seg, "id": None, "index": 0}],
                    )
                )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        chunks = list(self._stream(messages, stop=stop, run_manager=run_manager, **kwargs))
        msg = chunks[0].message
        for c in chunks[1:]:
            msg = msg + c.message
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content=msg.content, tool_calls=getattr(msg, "tool_calls", [])
                    )
                )
            ]
        )


def test_tool_call_and_result_map_to_chunks():
    from langgraph.prebuilt import create_react_agent

    agent = build_session_agent(create_react_agent(_FakeToolModel(), [get_weather]))
    chunks = _collect(agent, "weather?")
    tool_calls = [c["tool_calls"][0] for c in chunks if "tool_calls" in c]
    assert tool_calls and tool_calls[0]["name"] == "get_weather"
    assert tool_calls[0]["args"] == {"city": "Portland"}  # args reconstructed from deltas
    assert any("tool_result" in c for c in chunks)  # AG-UI surfaces the tool result
    assert "72F" in "".join(c["chunk"] for c in chunks if "chunk" in c)


def test_interrupt_surfaces_as_interrupt_chunk():
    def gate(state):
        decision = interrupt({"action_requests": [{"tool": "approve", "args": {"x": 1}}]})
        return {"messages": [AIMessage(content=f"ok {decision}")]}

    b = StateGraph(MessagesState)
    b.add_node("gate", gate)
    b.add_edge(START, "gate")
    b.add_edge("gate", END)
    agent = build_session_agent(b.compile(checkpointer=InMemorySaver()))

    chunks = _collect(agent, "go")
    interrupts = [c for c in chunks if c.get("status") == "interrupt"]
    assert interrupts, chunks
    assert interrupts[0]["interrupt"]["action_requests"][0]["tool"] == "approve"


def test_interrupt_resume_continues_the_graph():
    """gate 2: resuming via forwarded_props.command.resume drives the graph past
    the interrupt (the default path's Command(resume=...) semantics)."""

    def gate(state):
        decision = interrupt({"action_requests": [{"tool": "approve", "args": {}}]})
        return {"messages": [AIMessage(content=f"resolved: {decision}")]}

    b = StateGraph(MessagesState)
    b.add_node("gate", gate)
    b.add_edge(START, "gate")
    b.add_edge("gate", END)
    agent = build_session_agent(b.compile(checkpointer=InMemorySaver()))

    async def go():
        # turn 1 -> interrupt
        c1 = [c async for c in agui_stream_updates(agent, "go", "resume-test")]
        assert any(c.get("status") == "interrupt" for c in c1), c1
        # resume with a decision -> the graph must continue, not re-interrupt
        c2 = [
            c
            async for c in agui_stream_updates(
                agent, "go", "resume-test", resume={"decisions": [{"type": "accept"}]}
            )
        ]
        return c2

    resumed = asyncio.run(go())
    assert not any(c.get("status") == "interrupt" for c in resumed), resumed
    text = "".join(c["chunk"] for c in resumed if "chunk" in c)
    assert "resolved:" in text and "accept" in text


def _snapshot_tool_graph():
    """The issue #91 shape: custom nodes returning finished messages, ToolMessage
    appended manually (no ToolNode) -> everything via MessagesSnapshotEvent, no
    streaming ToolCall events. Before langstage-core 1.0.24 the snapshot path
    dropped tool calls/results here; the >=1.0.24 floor delivers the fix."""
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
    g.add_edge(START, "call_tool")
    g.add_edge("call_tool", "run_tool")
    g.add_edge("run_tool", "final")
    g.add_edge("final", END)
    return g.compile()


def test_non_streaming_tool_agent_surfaces_tool_call_and_result():
    """gh #91: a non-token tool agent's tool call + result must reach the CLI stream
    (they render via print_chunk's `● name` / `↳ result` branches). Requires the
    langstage-core snapshot fix delivered by the >=1.0.24 floor."""
    agent = build_session_agent(_snapshot_tool_graph())
    chunks = _collect(agent, "weather in paris?")
    calls = [c for c in chunks if "tool_calls" in c]
    results = [c for c in chunks if "tool_result" in c]
    assert calls and calls[0]["tool_calls"][0]["name"] == "get_weather", \
        "tool call dropped on the CLI snapshot path (needs langstage-core >= 1.0.24, gh #91)"
    assert results and results[0]["tool_result"] == "Sunny, 24C", \
        "tool result dropped on the CLI snapshot path (needs langstage-core >= 1.0.24, gh #91)"
