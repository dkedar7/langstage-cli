"""Sessions remember which agent made them (gh #215).

The session index stored no agent, so ``-a b.py -c`` fed agent A's transcript (tool
calls and all) to agent B without a word, ``--list-sessions`` couldn't tell sessions
apart, and continuing a session paused at A's approval with B dropped the approval and
stacked a second human message, exit 0. Now each index entry records its agent:
``--continue`` takes this agent's most recent session, ``--resume`` of another agent's
session says so, and one paused at another agent's approval is refused (exit 1).
"""

import json
import textwrap

import pytest
from click.testing import CliRunner

pytest.importorskip("ag_ui_langgraph")
pytest.importorskip("langgraph.checkpoint.sqlite.aio")

from langstage_cli import cli, sessions  # noqa: E402
from langstage_cli.cli import main  # noqa: E402

# Reports the message types it was handed: another agent's tool transcript shows up as
# 'tool' entries, a dropped approval as two 'human' entries in a row.
_OTHER = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langchain_core.messages import AIMessage

    def respond(state):
        return {"messages": [AIMessage(content=f"I see {[m.type for m in state['messages']]}")]}

    g = StateGraph(MessagesState)
    g.add_node("respond", respond)
    g.add_edge(START, "respond")
    g.add_edge("respond", END)
    graph = g.compile()
    """
)

# Agent A: calls a tool (so its transcript has a 'tool' message) and pauses on "ask me".
# A file agent, not the packaged demo: a module-level graph is shared across this test
# process, and an earlier test's checkpointer on it would read as the agent's own.
_FIRST = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langgraph.types import interrupt
    from langchain_core.messages import AIMessage, ToolMessage

    def respond(state):
        last = state["messages"][-1].content
        if "ask me" in last:
            ok = interrupt("Proceed?")
            return {"messages": [AIMessage(content=f"answered: {ok!r}")]}
        return {"messages": [
            AIMessage(content="", tool_calls=[{"name": "kb", "args": {}, "id": "t1"}]),
            ToolMessage(content="42", tool_call_id="t1"),
            AIMessage(content="the answer is 42"),
        ]}

    g = StateGraph(MessagesState)
    g.add_node("respond", respond)
    g.add_edge(START, "respond")
    g.add_edge("respond", END)
    graph = g.compile()
    """
)

_DEMO = "first_agent.py:graph"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    d = tmp_path / "ws"
    d.mkdir()
    (d / "other_agent.py").write_text(_OTHER, encoding="utf-8")
    (d / "first_agent.py").write_text(_FIRST, encoding="utf-8")
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(tmp_path / "empty_home"))
    monkeypatch.chdir(d)
    return d


def _run(*args):
    return CliRunner().invoke(main, list(args))


def _index(ws):
    return sessions.load_index(ws.resolve())


def _newest(ws):
    idx = _index(ws)
    return max(idx, key=lambda t: idx[t]["updated"])


def test_index_records_the_agent_and_the_list_shows_it(ws):
    assert _run("-a", _DEMO, "use a tool").exit_code == 0
    assert _run("-a", "other_agent.py:graph", "hello").exit_code == 0
    agents = sorted(e.get("agent") for e in _index(ws).values())
    assert agents == [f"{(ws / n).resolve()}:graph" for n in ("first_agent.py", "other_agent.py")]

    out = _run("--list-sessions").output
    assert "other_agent.py:graph" in out  # shown relative to the workspace
    assert _DEMO in out
    assert str(ws) + "/other_agent.py" not in out.replace("\\", "/")


def test_bare_file_spec_and_its_graph_suffix_are_one_agent(ws):
    assert _run("-a", "other_agent.py:graph", "one").exit_code == 0
    r = _run("-a", "other_agent.py", "-c", "two")
    assert r.exit_code == 0, r.output
    assert "I see ['human', 'ai', 'human']" in r.output


def test_continue_skips_another_agents_session(ws):
    assert _run("-a", _DEMO, "use a tool").exit_code == 0
    r = _run("-a", "other_agent.py:graph", "-c", "hello")
    assert r.exit_code == 0, r.output
    assert "I see ['human']" in r.output  # a fresh session, not the demo's transcript
    assert "No prior session for this agent" in r.output


def test_continue_picks_this_agents_session_past_a_newer_other_one(ws):
    assert _run("-a", "other_agent.py:graph", "mine").exit_code == 0
    assert _run("-a", _DEMO, "use a tool").exit_code == 0
    r = _run("-a", "other_agent.py:graph", "-c", "again")
    assert r.exit_code == 0, r.output
    assert "I see ['human', 'ai', 'human']" in r.output


def test_continue_still_takes_a_session_recorded_before_agents_were(ws):
    assert _run("-a", "other_agent.py:graph", "old").exit_code == 0
    idx = _index(ws)
    for entry in idx.values():
        entry.pop("agent")
    sessions._save_index(ws.resolve(), idx)
    r = _run("-a", "other_agent.py:graph", "-c", "new")
    assert r.exit_code == 0, r.output
    assert "I see ['human', 'ai', 'human']" in r.output
    # The resume fills the agent into the old entry.
    assert all(e.get("agent") for e in _index(ws).values())


def test_resume_of_another_agents_session_says_so(ws):
    assert _run("-a", _DEMO, "use a tool").exit_code == 0
    tid = _newest(ws)
    r = _run("-a", "other_agent.py:graph", "--resume", tid, "hello")
    assert r.exit_code == 0, r.output
    assert f"was created by {_DEMO}; resuming it with other_agent.py:graph" in r.output
    assert "'tool'" in r.output  # the user asked for that session, so it is used
    # It stays attributed to the agent that made it.
    assert _index(ws)[tid]["agent"] == f"{(ws / 'first_agent.py').resolve()}:graph"


def test_resume_of_another_agents_paused_session_is_refused(ws):
    r = _run("-a", _DEMO, "ask me")
    assert r.exit_code == 2, r.output
    tid = _newest(ws)
    assert sessions.thread_is_paused(ws.resolve(), tid)

    r = _run("-a", "other_agent.py:graph", "--resume", tid, "hello again")
    assert r.exit_code == 1, r.output
    assert "is paused at an approval from" in r.output
    assert f"-a {_DEMO} --resume" in r.output
    assert "I see" not in r.output  # nothing reached the other agent

    # Its own agent still finds the approval and resolves it.
    r = _run("-a", _DEMO, "--resume", tid, "--no-interactive", "and now")
    assert r.exit_code == 0, r.output
    assert "paused at an approval from its previous turn" in r.output


def test_a_finished_session_is_not_paused(ws):
    assert _run("-a", "other_agent.py:graph", "hello").exit_code == 0
    assert not sessions.thread_is_paused(ws.resolve(), _newest(ws))
    assert not sessions.thread_is_paused(ws.resolve(), "no-such-thread")


def test_session_agent_id_forms(tmp_path):
    f = tmp_path / "a.py"
    assert cli._session_agent_id("a.py", tmp_path, "graph") == f"{f.resolve()}:graph"
    assert cli._session_agent_id(f"{f}:agent", tmp_path, "agent") == f"{f.resolve()}:agent"
    assert cli._session_agent_id("pkg.mod", tmp_path, "graph") == "pkg.mod:graph"
    assert cli._session_agent_id("pkg.mod:g", tmp_path, "g") == "pkg.mod:g"
    assert cli._shown_agent(None, tmp_path) == "-"
    assert cli._shown_agent(f"{f.resolve()}:graph", tmp_path.resolve()) == "a.py:graph"


def test_index_stays_valid_json(ws):
    assert _run("-a", "other_agent.py:graph", "hello").exit_code == 0
    path = sessions._index_path(ws.resolve())
    assert json.loads(path.read_text(encoding="utf-8"))["sessions"]
