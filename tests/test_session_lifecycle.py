"""The run uses the live thread id, and sessions are recorded by their first turn.

gh #139: ``/clear`` and ``/reset`` put a new thread id in the config, but the loop had
read the id once at startup, so every later turn still ran on the old thread with the
full history.

gh #150: a session was recorded at REPL startup, so opening the CLI and quitting left an
empty "(no message yet)" session that the next ``-c`` resumed instead of the real one.
"""

import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

pytest.importorskip("ag_ui_langgraph")
pytest.importorskip("langgraph.checkpoint.sqlite.aio")

from langstage_cli import sessions  # noqa: E402
from langstage_cli.cli import main  # noqa: E402

_COUNT_AGENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langchain_core.messages import AIMessage

    def respond(state):
        return {"messages": [AIMessage(content=f"[agent sees {len(state['messages'])} messages]")]}

    g = StateGraph(MessagesState)
    g.add_node("respond", respond)
    g.add_edge(START, "respond")
    g.add_edge("respond", END)
    graph = g.compile()
    """
)


def _setup(tmp_path, monkeypatch, name: str) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / f"{name}.py").write_text(_COUNT_AGENT, encoding="utf-8")
    (ws / "langstage.toml").write_text(f'[agent]\nspec = "{name}.py:graph"\n')
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(tmp_path / "empty_home"))
    monkeypatch.chdir(ws)
    return ws.resolve()


def _counts(output: str) -> list:
    return [line for line in output.splitlines() if "agent sees" in line]


# --- gh #139 ---


@pytest.mark.parametrize("command", ["/clear", "/reset"])
@pytest.mark.parametrize("persist_args", [[], ["--no-persist"]], ids=["persist", "no-persist"])
def test_clear_and_reset_start_a_fresh_thread(
    tmp_path, monkeypatch, repl_via_stdin, command, persist_args
):
    _setup(tmp_path, monkeypatch, f"count_{command[1:]}_{len(persist_args)}")
    r = CliRunner().invoke(main, persist_args, input=f"one\ntwo\n{command}\nthree\n/quit\n")
    assert r.exit_code == 0, r.output
    counts = _counts(r.output)
    assert len(counts) == 3, r.output
    assert "sees 1 " in counts[0] and "sees 3 " in counts[1], counts
    assert "sees 1 " in counts[2], counts  # was 5: the old thread's full history


def test_continue_after_clear_resumes_the_post_clear_thread(tmp_path, monkeypatch, repl_via_stdin):
    ws = _setup(tmp_path, monkeypatch, "count_cont_clear")
    r = CliRunner().invoke(main, [], input="one\n/clear\ntwo\n/quit\n")
    assert r.exit_code == 0, r.output
    assert len(sessions.list_sessions(ws)) == 2  # the cleared thread is its own session
    r = CliRunner().invoke(main, ["-c", "--no-interactive", "three"])
    assert "sees 3 " in _counts(r.output)[0], r.output  # "two" + reply + "three"


# --- gh #150 ---


@pytest.mark.parametrize("keys", ["/quit\n", ""], ids=["quit", "eof"])
def test_open_and_quit_leaves_no_session_and_continue_finds_the_real_one(
    tmp_path, monkeypatch, repl_via_stdin, keys
):
    ws = _setup(tmp_path, monkeypatch, f"count_open_{len(keys)}")
    r = CliRunner()
    assert "sees 1 " in r.invoke(main, ["--no-interactive", "remember apples"]).output
    assert "sees 3 " in r.invoke(main, ["-c", "--no-interactive", "and bananas"]).output

    opened = r.invoke(main, [], input=keys)  # open the REPL, send nothing, leave
    assert opened.exit_code == 0, opened.output

    listed = sessions.list_sessions(ws)
    assert len(listed) == 1, listed
    assert listed[0][1]["first_message"] == "remember apples"
    final = r.invoke(main, ["-c", "--no-interactive", "final check"])
    assert "sees 5 " in final.output, final.output  # was 1: the empty session


def test_first_repl_turn_records_the_session(tmp_path, monkeypatch, repl_via_stdin):
    ws = _setup(tmp_path, monkeypatch, "count_first_turn")
    r = CliRunner().invoke(main, [], input="hello\n/quit\n")
    assert r.exit_code == 0, r.output
    listed = sessions.list_sessions(ws)
    assert len(listed) == 1 and listed[0][1]["first_message"] == "hello", listed
