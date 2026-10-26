"""A new message on a session paused at an approval is answered, not dropped (gh #191).

``-c "msg"`` on a session left paused (exit 2) used to re-raise the old interrupt in the
message's place. The message never reached the graph, ``--no-interactive`` approved the
old action and exited 0, and nothing said the message was gone. Now the pending approval
is resolved the way this run resolves any approval, then the message is sent; a run that
can't answer it (no terminal) exits 2 and says the message was not sent.
"""

import textwrap

import pytest
from click.testing import CliRunner

pytest.importorskip("ag_ui_langgraph")
pytest.importorskip("langgraph.checkpoint.sqlite.aio")

from langstage_cli import cli  # noqa: E402
from langstage_cli.cli import main  # noqa: E402

_AGENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langgraph.types import interrupt
    from langchain_core.messages import AIMessage

    def respond(state):
        last = state["messages"][-1].content
        if "deploy" in last:
            ok = interrupt("Deploy to production?")
            return {"messages": [AIMessage(content=f"deploy answered: {ok!r}")]}
        humans = [m.content for m in state["messages"] if m.type == "human"]
        return {"messages": [AIMessage(content=f"You said: {last} | humans: {humans}")]}

    g = StateGraph(MessagesState)
    g.add_node("respond", respond)
    g.add_edge(START, "respond")
    g.add_edge("respond", END)
    graph = g.compile()
    """
)


@pytest.fixture
def paused(tmp_path, monkeypatch):
    """A workspace whose most recent session is paused at the deploy approval."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "hitl_191.py").write_text(_AGENT, encoding="utf-8")
    (ws / "langstage.toml").write_text('[agent]\nspec = "hitl_191.py:graph"\n')
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(tmp_path / "empty_home"))
    monkeypatch.chdir(ws)
    r = CliRunner().invoke(main, ["please deploy"])
    assert r.exit_code == 2, r.output


def _history(runner: CliRunner) -> str:
    r = runner.invoke(main, ["-c", "--no-interactive", "check"])
    assert r.exit_code == 0, r.output
    return r.output


def test_no_terminal_refuses_and_says_the_message_was_not_sent(paused):
    runner = CliRunner()
    r = runner.invoke(main, ["-c", "what is 2+2"])
    assert r.exit_code == 2, r.output
    assert "paused at an approval from its previous turn" in r.output
    assert "Your message was not sent" in r.output
    assert "what is 2+2" not in _history(runner)


def test_no_interactive_approves_the_old_action_then_sends_the_message(paused):
    runner = CliRunner()
    r = runner.invoke(main, ["-c", "--no-interactive", "what is 2+2"])
    assert r.exit_code == 0, r.output
    assert "auto-approving it first" in r.output
    assert "deploy answered:" in r.output
    assert "You said: what is 2+2" in r.output
    assert _history(runner).count("what is 2+2") == 1  # in history exactly once


def test_terminal_answers_the_old_approval_then_sends_the_message(paused, monkeypatch):
    # A terminal answer at the approval prompt.
    monkeypatch.setattr(cli, "handle_interrupt_input", lambda *a, **k: "yes")
    r = CliRunner().invoke(main, ["-c", "what is 2+2"])
    assert r.exit_code == 0, r.output
    assert "answer it first" in r.output
    out = r.output
    assert out.index("deploy answered: 'yes'") < out.index("You said: what is 2+2")
    assert "humans: ['please deploy', 'what is 2+2']" in out


def test_a_session_that_is_not_paused_sends_the_message_once(paused):
    runner = CliRunner()
    runner.invoke(main, ["-c", "--no-interactive", "first"])
    r = runner.invoke(main, ["-c", "second"])
    assert r.exit_code == 0, r.output
    assert "paused at an approval" not in r.output
    assert "humans: ['please deploy', 'first', 'second']" in r.output
