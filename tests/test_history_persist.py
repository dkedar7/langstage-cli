"""Regression: `/history` works in the default (persist-on) config (gh #106).

Since #102 persistence is on by default and the durable checkpointer is an async-only
``AsyncSqliteSaver`` opened inside each turn's own event loop and closed when the turn
ends. ``cmd_history`` used the SYNC ``graph.get_state`` against that async, already-closed
saver, which scheduled ``aget_tuple`` as a never-awaited coroutine and surfaced
"Event loop is closed" (plus a RuntimeWarning) instead of the history — every user hit it
on a normal run. The fix reads history through the async API on a freshly-opened saver.

Hermetic: conftest's autouse ``_hermetic_sessions`` points LANGSTAGE_CLI_SESSIONS_DIR at a
temp dir, so this drives the real durable SQLite store without touching ``~/.langstage``.
Each test scaffolds its OWN uniquely-named agent module so the module-level graph object
is never shared across CliRunner invocations in the one pytest process (a real run is a
fresh process; the CLI mutates ``graph.checkpointer`` per turn).
"""

from click.testing import CliRunner

from langstage_cli.cli import main

_ECHO_AGENT = """
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import MessagesState
from langchain_core.messages import AIMessage


def respond(state):
    last = state["messages"][-1].content
    return {"messages": [AIMessage(content=f"echo: {last}")]}


_g = StateGraph(MessagesState)
_g.add_node("respond", respond)
_g.add_edge(START, "respond")
_g.add_edge("respond", END)
graph = _g.compile()
"""


def _write_agent(tmp_path, name):
    p = tmp_path / f"{name}.py"
    p.write_text(_ECHO_AGENT, encoding="utf-8")
    return f"{p}:graph"


def test_history_works_in_default_persist_on_config(tmp_path, monkeypatch, repl_via_stdin):
    # Persist is ON by default (no --no-persist): run one turn to write state to the
    # durable async store, then /history must read it back — not error on the closed
    # AsyncSqliteSaver.
    monkeypatch.chdir(tmp_path)
    spec = _write_agent(tmp_path, "hist_on_agent")
    r = CliRunner().invoke(main, ["-a", spec], input="hello there\n/history\n/quit\n")
    assert r.exit_code == 0, r.output
    # The #106 symptom must be gone...
    assert "Event loop is closed" not in r.output, r.output
    assert "Could not retrieve history" not in r.output, r.output
    assert "was never awaited" not in r.output, r.output
    # ...and the command must actually render the prior turn.
    assert "Conversation History" in r.output, r.output
    assert "hello there" in r.output, r.output


def test_history_still_works_with_persistence_off(tmp_path, monkeypatch, repl_via_stdin):
    # The --no-persist control from the issue: an in-memory checkpointer, so the sync
    # read path is exercised and must keep working (no regression).
    monkeypatch.chdir(tmp_path)
    spec = _write_agent(tmp_path, "hist_off_agent")
    r = CliRunner().invoke(main, ["-a", spec, "--no-persist"], input="ping\n/history\n/quit\n")
    assert r.exit_code == 0, r.output
    assert "Event loop is closed" not in r.output, r.output
    assert "Conversation History" in r.output, r.output
    assert "ping" in r.output, r.output
