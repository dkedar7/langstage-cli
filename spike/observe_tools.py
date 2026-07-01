"""SPIKE phase A: observe what a tool-using graph emits on both streams, so we
can map tool-call + tool-result rendering. Keyless, deterministic graph.

Run: uv run python -m spike.observe_tools
"""
import asyncio
import uuid

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from langgraph_stream_parser import prepare_agent_input, stream_graph_updates

from spike.agui_adapter import _simulate_agui_2067


def build_tool_graph():
    def agent1(state):
        return {"messages": [AIMessage(content="", tool_calls=[
            {"name": "get_weather", "args": {"city": "Portland"}, "id": "call_1"},
        ])]}

    def tools(state):
        last = state["messages"][-1]
        return {"messages": [
            ToolMessage(content="Sunny, 72F", tool_call_id=tc["id"], name=tc["name"])
            for tc in last.tool_calls
        ]}

    def agent2(state):
        return {"messages": [AIMessage(content="It's sunny and 72F in Portland.")]}

    b = StateGraph(MessagesState)
    b.add_node("agent1", agent1)
    b.add_node("tools", tools)
    b.add_node("agent2", agent2)
    b.add_edge(START, "agent1")
    b.add_edge("agent1", "tools")
    b.add_edge("tools", "agent2")
    b.add_edge("agent2", END)
    return b.compile(checkpointer=InMemorySaver())


def observe_current(graph):
    print("--- CURRENT (StreamParser) chunks ---")
    input_data = prepare_agent_input(message="weather in Portland?")
    for c in stream_graph_updates(graph, input_data, stream_mode="updates"):
        keys = {k: v for k, v in c.items() if k in ("tool_calls", "tool_result", "chunk", "status")}
        print("  ", keys)


async def observe_agui(graph):
    print("--- AG-UI raw events (type + salient fields) ---")
    _simulate_agui_2067()
    from ag_ui.core.types import RunAgentInput, UserMessage
    from ag_ui_langgraph import LangGraphAgent

    if getattr(graph, "checkpointer", None) is None:
        graph.checkpointer = InMemorySaver()
    agent = LangGraphAgent(name="obs", graph=graph)
    run_input = RunAgentInput(
        thread_id="obs", run_id=str(uuid.uuid4()), state={},
        messages=[UserMessage(id=str(uuid.uuid4()), role="user", content="weather in Portland?")],
        tools=[], context=[], forwarded_props={},
    )
    async for ev in agent.run(run_input):
        t = type(ev).__name__
        fields = {}
        for attr in ("tool_call_name", "tool_call_id", "delta", "role", "message_id"):
            if hasattr(ev, attr):
                fields[attr] = getattr(ev, attr)
        if t == "MessagesSnapshotEvent":
            fields["messages"] = [
                (getattr(m, "role", "?"), (getattr(m, "content", "") or "")[:30],
                 getattr(m, "tool_call_id", None))
                for m in ev.messages
            ]
        print(f"  {t:26} {fields}")


def main():
    graph = build_tool_graph()
    observe_current(graph)
    print()
    asyncio.run(observe_agui(build_tool_graph()))


if __name__ == "__main__":
    main()
