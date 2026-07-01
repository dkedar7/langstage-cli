"""SPIKE phase B: how does AG-UI represent a LangGraph interrupt(), vs the cli's
interrupt chunk — and can we resume in-process? Keyless.

Run: uv run python -m spike.observe_interrupt
"""
import asyncio
import uuid

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import interrupt

from langgraph_stream_parser import prepare_agent_input, stream_graph_updates

from spike.agui_adapter import _simulate_agui_2067


def build_interrupt_graph():
    def gate(state):
        decision = interrupt({"action_requests": [{"tool": "approve_purchase", "args": {"amount": 50}}]})
        return {"messages": [AIMessage(content=f"decision was: {decision}")]}

    b = StateGraph(MessagesState)
    b.add_node("gate", gate)
    b.add_edge(START, "gate")
    b.add_edge("gate", END)
    return b.compile(checkpointer=InMemorySaver())


def observe_current():
    print("--- CURRENT (StreamParser) ---")
    g = build_interrupt_graph()
    cfg = {"configurable": {"thread_id": "int-1"}}
    for c in stream_graph_updates(g, prepare_agent_input(message="buy it"), config=cfg, stream_mode="updates"):
        print("  turn1:", {k: v for k, v in c.items() if k in ("status", "interrupt", "chunk")})
    # resume with a decision
    for c in stream_graph_updates(g, prepare_agent_input(decisions=[{"type": "accept"}]), config=cfg, stream_mode="updates"):
        print("  resume:", {k: v for k, v in c.items() if k in ("status", "interrupt", "chunk")})


async def observe_agui():
    print("--- AG-UI raw events ---")
    _simulate_agui_2067()
    from ag_ui.core.types import RunAgentInput, UserMessage
    from ag_ui_langgraph import LangGraphAgent

    g = build_interrupt_graph()
    agent = LangGraphAgent(name="int", graph=g)
    tid = "int-2"
    ri = RunAgentInput(
        thread_id=tid, run_id=str(uuid.uuid4()), state={},
        messages=[UserMessage(id="1", role="user", content="buy it")],
        tools=[], context=[], forwarded_props={},
    )
    async for ev in agent.run(ri):
        t = type(ev).__name__
        if t in ("RawEvent", "StepStartedEvent", "StepFinishedEvent"):
            continue
        f = {}
        for a in ("name", "value", "delta", "role"):
            if hasattr(ev, a):
                f[a] = getattr(ev, a)
        print(f"  turn1 {t:24}", f if f else "")

    # resume: AG-UI carries resume on RunAgentInput.resume, as typed ResumeEntry
    # items (interrupt_id + status). The interrupt id comes from LangGraph state.
    print("  --- resume via RunAgentInput.resume (ResumeEntry) ---")
    from ag_ui.core.types import ResumeEntry

    state = g.get_state({"configurable": {"thread_id": tid}})
    iid = state.tasks[0].interrupts[0].id
    print(f"  interrupt_id from state: {iid}")

    ri2 = RunAgentInput(
        thread_id=tid, run_id=str(uuid.uuid4()), state={},
        messages=[UserMessage(id="1", role="user", content="buy it")],
        tools=[], context=[], forwarded_props={},
        resume=[ResumeEntry(interrupt_id=iid, status="resolved", payload={"type": "accept"})],
    )
    async for ev in agent.run(ri2):
        t = type(ev).__name__
        if t in ("RawEvent", "StepStartedEvent", "StepFinishedEvent", "StateSnapshotEvent"):
            continue
        f = {}
        if t == "MessagesSnapshotEvent":
            f["msgs"] = [(getattr(m, "role", "?"), (getattr(m, "content", "") or "")[:45]) for m in ev.messages]
        print(f"  resume {t:24}", f if f else "")


def main():
    observe_current()
    print()
    asyncio.run(observe_agui())


if __name__ == "__main__":
    main()
