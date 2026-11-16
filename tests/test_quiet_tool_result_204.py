"""A quiet single-shot run never prints nothing and exits 0 (gh #204).

A turn that ends on a tool result, the shape a ``@tool(return_direct=True)`` agent
produces, has its answer in that result: no assistant text follows it. Quiet mode
(piped stdout, or ``-q``) dropped tool frames as chatter, while the "did the turn
produce anything" check counted them, so ``answer=$(langstage-cli ...)`` captured an
empty string with exit 0 and nothing on stderr.

Quiet mode now prints the result(s) the turn ended on as the reply. When stdout would
still be empty (a tool call with no result, an empty result, a model that read the
result and wrote nothing, reasoning only) the run reports the empty turn and exits 1.
A terminal renders exactly as before.
"""

import asyncio
import contextlib
import io
import re
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

pytest.importorskip("ag_ui_langgraph")

from langstage_cli import agui_stream, cli  # noqa: E402
from langstage_cli.agui_stream import build_session_agent  # noqa: E402
from langstage_cli.cli import main  # noqa: E402

_HEAD = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langchain_core.messages import AIMessage, ToolMessage

    def _chain(*steps):
        g = StateGraph(MessagesState)
        prev = START
        for i, step in enumerate(steps):
            g.add_node(f"n{i}", step)
            g.add_edge(prev, f"n{i}")
            prev = f"n{i}"
        g.add_edge(prev, END)
        return g.compile()

    def call(*calls, text=""):
        def step(state):
            return {"messages": [AIMessage(content=text, tool_calls=[
                {"name": name, "args": {"q": "x"}, "id": cid} for name, cid in calls])]}
        return step

    def result(*results):
        def step(state):
            return {"messages": [ToolMessage(content=text, tool_call_id=cid, name="lookup")
                                 for text, cid in results]}
        return step

    def say(text):
        def step(state):
            return {"messages": [AIMessage(content=text)]}
        return step
    """
)


def _agent(body: str) -> str:
    return _HEAD + textwrap.dedent(body)


# The `return_direct` shape: a tool call, its result, and the graph ends.
_RETURN_DIRECT = _agent(
    """
    graph = _chain(call(("lookup", "c1")), result(("ANSWER for x", "c1")))
    """
)

# The usual shape: the model reads the result and replies.
_TOOL_THEN_TEXT = _agent(
    """
    graph = _chain(call(("lookup", "c1")), result(("RAW TOOL OUTPUT", "c1")),
                   say("The answer is 42."))
    """
)

# A tool call nobody ran: no result, no reply.
_CALL_ONLY = _agent(
    """
    graph = _chain(call(("lookup", "c1")))
    """
)

# Two tools in the last step, both returned directly.
_TWO_RESULTS = _agent(
    """
    graph = _chain(call(("lookup", "c1"), ("lookup", "c2")),
                   result(("first answer", "c1"), ("second answer", "c2")))
    """
)

# An earlier tool round the model read, then a tool whose result ends the turn.
_TWO_ROUNDS = _agent(
    """
    graph = _chain(call(("search", "c1")), result(("intermediate notes", "c1")),
                   call(("lookup", "c2")), result(("FINAL ANSWER", "c2")))
    """
)

# The model read the result and wrote an empty message: it had the last word.
_TOOL_THEN_EMPTY = _agent(
    """
    graph = _chain(call(("lookup", "c1")), result(("RAW TOOL OUTPUT", "c1")), say(""))
    """
)

# A tool that returned nothing.
_EMPTY_RESULT = _agent(
    """
    graph = _chain(call(("lookup", "c1")), result(("", "c1")))
    """
)

# A preamble before the tool call, then the result ends the turn.
_PREAMBLE_THEN_RESULT = _agent(
    """
    graph = _chain(call(("lookup", "c1"), text="Looking that up."),
                   result(("ANSWER for x", "c1")))
    """
)


def _ws(tmp_path, monkeypatch, source: str) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "agent_204.py").write_text(source, encoding="utf-8")
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(tmp_path / "empty_home"))
    monkeypatch.chdir(ws)
    return ws


def _run(tmp_path, monkeypatch, source: str, *args: str, **kwargs):
    _ws(tmp_path, monkeypatch, source)
    return CliRunner().invoke(main, [*args, "-a", "agent_204.py:graph", "find x"], **kwargs)


# --- the turn ends on a tool result -------------------------------------------------


def test_piped_run_prints_the_final_tool_result(tmp_path, monkeypatch):
    r = _run(tmp_path, monkeypatch, _RETURN_DIRECT)
    assert r.exit_code == 0, r.output
    assert r.stdout == "ANSWER for x\n", repr(r.stdout)  # once, and nothing else


def test_quiet_flag_prints_the_final_tool_result(tmp_path, monkeypatch):
    r = _run(tmp_path, monkeypatch, _RETURN_DIRECT, "-q")
    assert r.exit_code == 0, r.output
    assert r.stdout == "ANSWER for x\n", repr(r.stdout)


def test_final_tool_result_without_persistence(tmp_path, monkeypatch):
    r = _run(tmp_path, monkeypatch, _RETURN_DIRECT, "--no-persist")
    assert r.exit_code == 0, r.output
    assert r.stdout == "ANSWER for x\n", repr(r.stdout)


def test_piped_stdin_message_prints_the_final_tool_result(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch, _RETURN_DIRECT)
    r = CliRunner().invoke(main, ["-a", "agent_204.py:graph"], input="find x\n")
    assert r.exit_code == 0, r.output
    assert r.stdout == "ANSWER for x\n", repr(r.stdout)


def test_real_return_direct_agent(tmp_path, monkeypatch):
    """The issue's own agent: LangChain's ``create_agent`` with a ``return_direct`` tool."""
    pytest.importorskip("langchain.agents")
    source = textwrap.dedent(
        """
        from langchain_core.tools import tool
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        from langchain_core.messages import AIMessage
        from langchain.agents import create_agent

        @tool(return_direct=True)
        def lookup(q: str) -> str:
            \"\"\"Look up q.\"\"\"
            return f"ANSWER for {q}"

        class M(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kw):
                return self

        model = M(responses=[AIMessage(content="", tool_calls=[
            {"name": "lookup", "args": {"q": "x"}, "id": "c1"}])])
        graph = create_agent(model, tools=[lookup])
        """
    )
    try:
        from langchain.agents import create_agent  # noqa: F401
    except ImportError:
        pytest.skip("langchain.agents.create_agent not available")
    r = _run(tmp_path, monkeypatch, source)
    assert r.exit_code == 0, r.output
    assert r.stdout == "ANSWER for x\n", repr(r.stdout)


def test_several_final_results_print_one_per_line(tmp_path, monkeypatch):
    r = _run(tmp_path, monkeypatch, _TWO_RESULTS)
    assert r.exit_code == 0, r.output
    assert r.stdout == "first answer\nsecond answer\n", repr(r.stdout)


def test_only_the_last_tool_round_is_the_answer(tmp_path, monkeypatch):
    r = _run(tmp_path, monkeypatch, _TWO_ROUNDS)
    assert r.exit_code == 0, r.output
    assert r.stdout == "FINAL ANSWER\n", repr(r.stdout)
    assert "intermediate" not in r.stdout


# --- turns that already had a reply are unchanged -----------------------------------


def test_text_after_the_tool_result_is_still_the_only_output(tmp_path, monkeypatch):
    r = _run(tmp_path, monkeypatch, _TOOL_THEN_TEXT)
    assert r.exit_code == 0, r.output
    assert r.stdout == "The answer is 42.\n", repr(r.stdout)
    assert "RAW TOOL OUTPUT" not in r.output


def test_a_preamble_is_the_reply_and_the_result_stays_tool_chatter(tmp_path, monkeypatch):
    # The turn already printed assistant text, so stdout isn't empty: quiet output is
    # what it has always been for this turn.
    r = _run(tmp_path, monkeypatch, _PREAMBLE_THEN_RESULT)
    assert r.exit_code == 0, r.output
    assert r.stdout == "Looking that up.\n", repr(r.stdout)


# --- nothing to print is a failure, never a silent success --------------------------


@pytest.mark.parametrize(
    "source",
    [_CALL_ONLY, _TOOL_THEN_EMPTY, _EMPTY_RESULT],
    ids=["tool-call-no-result", "empty-message-after-result", "empty-result"],
)
def test_nothing_on_stdout_exits_1_with_a_diagnostic(tmp_path, monkeypatch, source):
    r = _run(tmp_path, monkeypatch, source)
    assert r.exit_code == 1, r.output
    assert r.stdout.strip() == "", repr(r.stdout)
    assert "RAW TOOL OUTPUT" not in r.output  # the model's empty last word isn't replaced
    assert "produced no output" in r.stderr, r.stderr


def _stream_of(*chunks):
    async def _fake(agent, message, thread_id, resume=None):
        for chunk in chunks:
            yield chunk

    return _fake


def _quiet_turn(monkeypatch, *chunks) -> tuple[bool, str, str]:
    """One quiet turn over canned frames, with an agent that has no readable state."""
    monkeypatch.setattr(agui_stream, "agui_stream_updates", _stream_of(*chunks))
    monkeypatch.setattr(cli, "_QUIET", True)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        _elapsed, had_error = asyncio.run(cli.run_single_turn_agui(object(), "hi", "t-204", False))
    return had_error, out.getvalue(), err.getvalue()


def test_reasoning_only_turn_is_an_empty_turn_in_quiet_mode(monkeypatch):
    had_error, out, err = _quiet_turn(
        monkeypatch,
        {"status": "streaming", "reasoning": "thinking it over", "node": "model"},
        {"status": "complete"},
    )
    assert had_error and out == "", (had_error, out)
    assert "produced no output" in err, err


def test_frames_alone_decide_when_there_is_no_state_to_read(monkeypatch):
    had_error, out, _err = _quiet_turn(
        monkeypatch,
        {"status": "streaming", "tool_calls": [{"name": "lookup", "args": {}, "id": "c1"}]},
        {"status": "streaming", "tool_result": "ANSWER for x", "id": "c1", "name": "lookup"},
        {"status": "complete"},
    )
    assert not had_error and out == "ANSWER for x", (had_error, out)


def test_structured_tool_result_prints_as_json(monkeypatch):
    had_error, out, _err = _quiet_turn(
        monkeypatch,
        {"status": "streaming", "tool_calls": [{"name": "lookup", "args": {}, "id": "c1"}]},
        {"status": "streaming", "tool_result": {"city": "Zürich", "temp": 24}, "id": "c1"},
        {"status": "complete"},
    )
    assert not had_error, out
    assert out == '{"city": "Zürich", "temp": 24}', repr(out)


def test_an_errored_turn_does_not_print_a_tool_result_as_the_reply(monkeypatch):
    had_error, out, err = _quiet_turn(
        monkeypatch,
        {"status": "streaming", "tool_calls": [{"name": "lookup", "args": {}, "id": "c1"}]},
        {"status": "streaming", "tool_result": "partial", "id": "c1", "name": "lookup"},
        {"status": "error", "error": "boom"},
    )
    assert had_error and out == "", (had_error, out)
    assert "boom" in err, err


# --- a terminal renders exactly as before -------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _tty_turn(source: str, monkeypatch) -> tuple[bool, str]:
    ns: dict = {}
    exec(source, ns)
    agent = build_session_agent(ns["graph"])
    monkeypatch.setattr(cli, "Spinner", lambda *a, **k: None)
    assert cli._QUIET is False
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _elapsed, had_error = asyncio.run(
            cli.run_single_turn_agui(agent, "find x", "t-204-tty", interactive=False)
        )
    return had_error, _ANSI.sub("", buf.getvalue())


def test_terminal_shows_the_tool_card_once_and_no_extra_reply(monkeypatch):
    had_error, out = _tty_turn(_RETURN_DIRECT, monkeypatch)
    assert not had_error
    assert "● lookup" in out and "↳ ANSWER for x" in out, out
    assert out.count("ANSWER for x") == 1, out  # the card only: no second, bare copy


def test_terminal_tool_call_without_a_result_still_renders_and_succeeds(monkeypatch):
    # Unchanged: in a terminal the tool card is output the user sees.
    had_error, out = _tty_turn(_CALL_ONLY, monkeypatch)
    assert not had_error
    assert "● lookup" in out, out
