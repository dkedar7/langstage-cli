"""Deferred-backlog fixes (gh #160, #158, #121, #144, #152)."""

import random
import re
import textwrap
import uuid

import pytest
from click.testing import CliRunner

from langstage_cli import cli as c
from langstage_cli.cli import main, render_markdown

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# --- gh #160: markdown spans crossing streamed-chunk boundaries -----------------------

_SAMPLES = [
    "This is **very important stuff** done.",
    "Use *two words* and `a code span` plus [the docs](https://x.io/a_(b)) now.",
    "Line one **bold**\nline *two words* here\n\n- item `x y`",
    "Before\n```python\nx = 1  # **not bold**\n```\nAfter **bold text**",
    "5 * 3 = 15 and a \\*literal\\* star",
    "***both styles*** then ~~~\nfenced ~~~\n~~~\nout *it*",
    "unclosed **bold at end",
]


def _stream(chunks):
    """What print_chunk writes for a token-streamed reply, minus the leading marker."""
    c.print_chunk._streaming_text = False
    c._md_stream.reset()
    out = []
    for i, tok in enumerate(chunks):
        out.append(c._md_stream.feed(tok))
    out.append(c._md_stream.flush())
    return "".join(out)


def _norm(s: str) -> str:
    # Streaming a code-block line in pieces wraps each piece in its own color span.
    return s.replace(f"{c.RESET}{c.CYAN}", "")


def _word_chunks(text):
    return re.findall(r"\S+|\s+", text)


@pytest.mark.parametrize("text", _SAMPLES)
def test_streamed_markdown_matches_whole_string_render(text):
    assert _norm(_stream(_word_chunks(text))) == _norm(render_markdown(text))


@pytest.mark.parametrize("text", _SAMPLES)
def test_streamed_markdown_any_chunking(text):
    rng = random.Random(160)
    for _ in range(50):
        cuts = sorted(rng.sample(range(1, len(text)), min(6, len(text) - 1)))
        chunks = [text[a:b] for a, b in zip([0] + cuts, cuts + [len(text)])]
        assert _norm(_stream(chunks)) == _norm(render_markdown(text)), chunks


def test_issue_160_repro_is_styled_through_print_chunk(capsys):
    c.print_chunk._streaming_text = False
    c._md_stream.reset()
    for tok in ["This", " ", "is", " ", "**very", " ", "important", " ", "stuff**", " ", "done."]:
        c.print_chunk({"status": "streaming", "chunk": tok, "node": "n", "message_id": "m"})
    c.print_chunk({"status": "complete"})
    out = capsys.readouterr().out
    assert f"{c.BOLD}very important stuff{c.RESET}" in out
    assert "**" not in out
    assert _ANSI.sub("", out).endswith("This is very important stuff done.")


def test_plain_text_still_streams_token_by_token():
    c._md_stream.reset()
    assert c._md_stream.feed("hello") == "hello"
    assert c._md_stream.feed(" world") == " world"


def test_held_span_is_flushed_before_a_tool_call(capsys):
    c.print_chunk._streaming_text = False
    c._md_stream.reset()
    c.print_chunk({"status": "streaming", "chunk": "see **x", "node": "n"})
    c.print_chunk({"status": "streaming", "tool_calls": [{"name": "t", "args": {}}]})
    out = _ANSI.sub("", capsys.readouterr().out)
    assert out.index("see **x") < out.index("● t")


# --- gh #158: --help leaks a literal backspace ----------------------------------------


def test_help_has_no_backspace_and_lists_spec_formats():
    out = CliRunner().invoke(main, ["--help"]).output
    assert "\b" not in out
    assert re.search(r"^\s*- path/to/file.py:agent\s", out, re.M), out


# An echo agent in the test's own dir: `--demo` would share (and mutate) the core stub
# graph object across tests.
_ECHO_AGENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langchain_core.messages import AIMessage

    def respond(state):
        return {"messages": [AIMessage(content="You said: " + state["messages"][-1].content)]}

    g = StateGraph(MessagesState)
    g.add_node("respond", respond)
    g.add_edge(START, "respond")
    g.add_edge("respond", END)
    graph = g.compile()
    """
)


def _echo(tmp_path, monkeypatch):
    (tmp_path / "echo_agent.py").write_text(_ECHO_AGENT)
    monkeypatch.chdir(tmp_path)
    return ["-a", "echo_agent.py:graph"]


# --- gh #121: the literal message "init" with an explicit agent ------------------------


def test_init_message_reaches_an_explicit_agent(tmp_path, monkeypatch):
    r = CliRunner().invoke(main, [*_echo(tmp_path, monkeypatch), "init"])
    assert r.exit_code == 0, r.output
    assert "You said: init" in r.output
    assert not (tmp_path / "my_agent.py").exists()
    assert not (tmp_path / "langstage.toml").exists()


# --- gh #144: -f with a non-UTF-8 prompt file -----------------------------------------


def test_prompt_file_in_cp1252_is_read(tmp_path, monkeypatch):
    args = _echo(tmp_path, monkeypatch)
    text = "Summarize this café résumé — it’s great"
    (tmp_path / "prompt.md").write_bytes(text.encode("cp1252"))
    r = CliRunner().invoke(main, [*args, "-f", "prompt.md"])
    assert r.exit_code == 0, r.output
    assert text in r.output


def test_prompt_file_utf8_bom_is_stripped(tmp_path, monkeypatch):
    args = _echo(tmp_path, monkeypatch)
    (tmp_path / "prompt.md").write_bytes("﻿hello".encode("utf-8"))
    r = CliRunner().invoke(main, [*args, "-f", "prompt.md"])
    assert r.exit_code == 0, r.output
    assert "You said: hello" in r.output
    assert "﻿" not in r.output


# --- gh #152: node attribution with the CLI's durable (async sqlite) checkpointer -----

_TOOL_AGENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langchain_core.messages import AIMessage, ToolMessage

    def call_tool(state):
        return {"messages": [AIMessage(content="Let me check the weather.",
            tool_calls=[{"name": "get_weather", "args": {"city": "Paris"}, "id": "call_1"}])]}
    def run_tool(state):
        return {"messages": [ToolMessage(content="Sunny, 24C", tool_call_id="call_1")]}
    def final(state):
        return {"messages": [AIMessage(content="The weather in Paris is sunny, 24C.")]}

    g = StateGraph(MessagesState)
    for n, f in [("call_tool", call_tool), ("run_tool", run_tool), ("final", final)]:
        g.add_node(n, f)
    g.add_edge(START, "call_tool"); g.add_edge("call_tool", "run_tool")
    g.add_edge("run_tool", "final"); g.add_edge("final", END)
    graph = g.compile()
    """
)


def test_text_frames_carry_their_own_node_with_async_sqlite(tmp_path):
    import asyncio

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langstage_core import load_agent_spec

    from langstage_cli.agui_stream import agui_stream_updates, build_session_agent

    (tmp_path / "tool_agent.py").write_text(_TOOL_AGENT)

    async def run():
        nodes = {}
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "s.sqlite")) as saver:
            g = load_agent_spec(f"{tmp_path / 'tool_agent.py'}:graph")
            g.checkpointer = saver
            agent = build_session_agent(g)
            async for ch in agui_stream_updates(agent, "weather?", str(uuid.uuid4())):
                if "chunk" in ch:
                    nodes[ch["chunk"]] = ch.get("node")
        return nodes

    nodes = asyncio.run(run())
    assert nodes["Let me check the weather."] == "call_tool"
    assert nodes["The weather in Paris is sunny, 24C."] == "final"
