"""--no-interactive auto-approves interrupts and resumes the graph (gh #32).

Previously a LangGraph interrupt() under --no-interactive was silently dropped
(the loop broke without resuming), so the agent's post-interrupt work never ran
and the turn exited 0 looking successful. --no-interactive is documented as
"auto-approve tool calls", so it must approve all pending actions and resume.
"""

import textwrap

from click.testing import CliRunner

from langstage_cli.cli import main

_HITL_AGENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import interrupt
    from langchain_core.messages import AIMessage

    def ask(state):
        decision = interrupt({"question": "Approve this action?"})
        return {"messages": [AIMessage(content=f"Decision was: {decision}")]}

    g = StateGraph(MessagesState)
    g.add_node("ask", ask)
    g.add_edge(START, "ask")
    g.add_edge("ask", END)
    graph = g.compile(checkpointer=MemorySaver())
    """
)


def test_no_interactive_auto_approves_and_resumes(tmp_path, monkeypatch):
    (tmp_path / "hitl_agent.py").write_text(_HITL_AGENT)
    monkeypatch.chdir(tmp_path)

    r = CliRunner().invoke(main, ["-a", "hitl_agent.py:graph", "go", "--no-interactive"])
    assert r.exit_code == 0, r.output
    # The interrupt was auto-approved and the graph resumed — the post-interrupt
    # node ran and rendered its content, instead of the interrupt being dropped.
    assert "Auto-approving" in r.output
    assert "Decision was:" in r.output
