"""The CLI auto-attaches a checkpointer so the conversation loop has memory (gh #38).

The interactive loop sends only the latest message each turn and relies on the
graph's checkpointer (keyed by thread_id) for multi-turn memory. A bring-your-own
graph compiled without one — like the README's minimal example — used to be
amnesiac (every turn saw one message) and /history errored "No checkpointer set".
The loader now attaches an in-memory default when the graph has none.
"""

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from langstage_cli.cli import _ensure_checkpointer


def _counting_graph(checkpointer=None):
    def respond(state):
        return {"messages": [AIMessage(content=f"n={len(state['messages'])}")]}

    b = StateGraph(MessagesState)
    b.add_node("respond", respond)
    b.add_edge(START, "respond")
    b.add_edge("respond", END)
    return b.compile(checkpointer=checkpointer)


def test_attaches_checkpointer_when_absent():
    g = _counting_graph()
    assert getattr(g, "checkpointer", None) is None
    _ensure_checkpointer(g)
    assert g.checkpointer is not None


def test_leaves_user_supplied_checkpointer_untouched():
    saver = InMemorySaver()
    g = _counting_graph(checkpointer=saver)
    _ensure_checkpointer(g)
    assert g.checkpointer is saver


def test_memory_accumulates_across_turns():
    g = _counting_graph()
    _ensure_checkpointer(g)
    cfg = {"configurable": {"thread_id": "t1"}}
    g.invoke({"messages": [("user", "first")]}, cfg)
    out = g.invoke({"messages": [("user", "second")]}, cfg)
    # Turn 2 sees the prior turn's messages (memory works), not just the latest.
    assert len(out["messages"]) >= 3
