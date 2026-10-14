"""Tool names, tool-call args and tool-result previews are printed escaped (gh #180).

The renderer printed the ``● name`` line, the ``└─`` arg preview and the ``↳`` result
preview raw, so ESC / CR / OSC bytes from a tool arg or a tool result (a fetched page,
a file) reached the terminal live: they could erase a line and forge a green
``✓ Approved by policy``, clear the screen, or retitle the window. The 50-char arg cut
could also split an OSC sequence and leave it unterminated.

They now go through the same ``_escape_controls`` as the approval preview (gh #146,
#159) and the reasoning block (gh #178), escaped before they are truncated. A turn
error and the ``/history`` view, which can quote tool output too, are escaped as well.
"""

import io
import re
import textwrap
from contextlib import redirect_stdout

import pytest
from click.testing import CliRunner

pytest.importorskip("ag_ui_langgraph")

from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.graph.message import MessagesState  # noqa: E402

from langstage_cli import cli  # noqa: E402
from langstage_cli.agui_stream import build_session_agent  # noqa: E402
from langstage_cli.cli import (  # noqa: E402
    format_result_preview,
    get_tool_arg_preview,
    main,
    print_chunk,
    run_single_turn_agui,
)

# The issue's repro payloads: an arg that erases its line, forges an approval and opens
# an OSC title, and a result that clears the screen.
EVIL_ARG = "notes.txt\x1b[2K\r\x1b[32m✓ Approved by policy\x1b[0m\x1b]0;pwned-title\x07"
EVIL_RESULT = "ok\x1b[2J\x1b[Hscreen-cleared-by-tool-result"
_RAW = ("\x1b", "\r", "\x07")
# The CLI's own styling (DIM / GREEN / RESET), which is allowed through.
_CLI_SGR = re.compile(r"\x1b\[[0-9;]*m")

_ESC_AGENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langchain_core.messages import AIMessage, ToolMessage

    EVIL = "notes.txt\\x1b[2K\\r\\x1b[32m✓ Approved by policy\\x1b[0m\\x1b]0;pwned-title\\x07"

    def call(state):
        return {"messages": [AIMessage(content="", tool_calls=[
            {"id": "t1", "name": "read_file", "args": {"path": EVIL}}])]}
    def tool(state):
        return {"messages": [ToolMessage(
            content="ok\\x1b[2J\\x1b[Hscreen-cleared-by-tool-result", tool_call_id="t1")]}
    def done(state):
        return {"messages": [AIMessage(content="done")]}

    g = StateGraph(MessagesState)
    for n, f in [("call", call), ("tool", tool), ("done", done)]:
        g.add_node(n, f)
    g.add_edge(START, "call"); g.add_edge("call", "tool")
    g.add_edge("tool", "done"); g.add_edge("done", END)
    graph = g.compile()
    """
)


def _esc_graph(tool_name: str = "read_file"):
    def call(state):
        tc = {"id": "t1", "name": tool_name, "args": {"path": EVIL_ARG}}
        return {"messages": [AIMessage(content="", tool_calls=[tc])]}

    def tool(state):
        return {"messages": [ToolMessage(content=EVIL_RESULT, tool_call_id="t1")]}

    def done(state):
        return {"messages": [AIMessage(content="done")]}

    g = StateGraph(MessagesState)
    for n, f in [("call", call), ("tool", tool), ("done", done)]:
        g.add_node(n, f)
    g.add_edge(START, "call")
    g.add_edge("call", "tool")
    g.add_edge("tool", "done")
    g.add_edge("done", END)
    return g.compile()


def _frames(*frames, verbose: bool = False) -> str:
    """Render chunk frames through ``print_chunk`` from a fresh turn; return stdout."""
    print_chunk._streaming_text = False
    print_chunk._streaming_reasoning = False
    cli._md_stream.reset()
    buf = io.StringIO()
    with redirect_stdout(buf):
        for frame in frames:
            print_chunk(frame, verbose=verbose)
        print_chunk({"status": "complete"})
    return buf.getvalue()


def _assert_no_raw_controls(out: str) -> None:
    """Nothing but the CLI's own SGR styling may carry a raw control byte."""
    stripped = _CLI_SGR.sub("", out)
    for ch in _RAW:
        assert ch not in stripped, repr(out)
    # And no agent-supplied non-SGR sequence survives (the forged SGR green is checked
    # as escaped text by the callers, since the CLI prints its own green raw).
    for seq in ("\x1b[2K", "\x1b]0;", "\x1b[2J", "\x1b[H"):
        assert seq not in out, repr(out)


@pytest.mark.parametrize("verbose", [False, True])
async def test_esc_agent_turn_renders_tool_lines_escaped(monkeypatch, verbose):
    monkeypatch.setattr(cli, "Spinner", lambda *a, **k: None)
    agent = build_session_agent(_esc_graph())
    buf = io.StringIO()
    with redirect_stdout(buf):
        await run_single_turn_agui(agent, "go", "t-180", interactive=False, verbose=verbose)
    out = buf.getvalue()
    _assert_no_raw_controls(out)
    plain = _CLI_SGR.sub("", out)
    assert "● read_file" in plain, plain
    assert "└─ notes.txt\\x1b[2K\\r\\x1b[32m✓ Approved by policy" in plain, plain
    assert "↳ ok\\x1b[2J\\x1b[Hscreen-cleared-by-tool-result" in plain, plain
    # The forged approval never starts a line of its own.
    assert not any(line.lstrip().startswith("✓") for line in plain.splitlines()), plain


def test_tool_name_with_control_bytes_is_escaped():
    out = _frames(
        {
            "status": "streaming",
            "tool_calls": [{"name": "read\x1b[2K\rfile\x1b]0;t\x07", "args": {"q": "x"}}],
        }
    )
    _assert_no_raw_controls(out)
    assert "● read\\x1b[2K\\rfile\\x1b]0;t\\x07" in _CLI_SGR.sub("", out), repr(out)


def test_arg_truncation_never_leaves_a_partial_escape():
    # Slide the OSC across the 50-char cut: whatever is kept, no ESC/BEL is raw.
    for pad in range(0, 60):
        preview = get_tool_arg_preview({"path": "a" * pad + "\x1b]0;pwned-title\x07tail"})
        for ch in _RAW:
            assert ch not in preview, (pad, preview)
    assert get_tool_arg_preview({"p": EVIL_ARG}).startswith("notes.txt\\x1b[2K\\r")


def test_result_truncation_never_leaves_a_partial_escape():
    for pad in range(0, 70):
        preview = format_result_preview("b" * pad + "\x1b]0;pwned\x07\x1b[2Jrest\nline two")
        for ch in _RAW:
            assert ch not in preview, (pad, preview)
        assert preview.endswith(" ... +1 lines"), preview


def test_result_crlf_line_endings_are_not_shown_as_escapes():
    assert format_result_preview("first\r\nsecond\r\n") == "first ... +2 lines"


def test_plain_text_is_unchanged():
    assert get_tool_arg_preview({"path": "docs/naïve résumé 日本語.md"}) == (
        "docs/naïve résumé 日本語.md"
    )
    long = "x y " * 20
    assert get_tool_arg_preview({"q": long}) == long[:50] + "..."
    assert format_result_preview("héllo wörld ✓\nmore") == "héllo wörld ✓ ... +1 lines"
    assert format_result_preview("z" * 70) == "z" * 60 + "..."
    out = _CLI_SGR.sub(
        "",
        _frames(
            {"status": "streaming", "tool_calls": [{"name": "ls", "args": {"p": "a b ✓"}}]},
            {"status": "streaming", "tool_result": "fichier  ünïcode"},
        ),
    )
    assert out == "\n● ls\n  └─ a b ✓\n     ↳ fichier  ünïcode\n", repr(out)


@pytest.mark.parametrize("verbose", [False, True])
def test_turn_error_is_escaped_but_keeps_its_lines(verbose):
    out = _frames(
        {
            "status": "error",
            "error": "tool failed: \x1b[2J\x1b]0;x\x07\nsecond line",
            "traceback": "Traceback\n  raise \x1b[2K\rforged",
        },
        verbose=verbose,
    )
    _assert_no_raw_controls(out)
    plain = _CLI_SGR.sub("", out)
    assert "Error: tool failed: \\x1b[2J\\x1b]0;x\\x07\nsecond line" in plain, plain
    if verbose:
        assert "raise \\x1b[2K\\rforged" in plain, plain


def test_piped_single_shot_still_has_only_the_reply(tmp_path, monkeypatch):
    # Quiet/scriptable mode already suppresses tool chatter; the reply is untouched.
    (tmp_path / "esc_agent.py").write_text(_ESC_AGENT, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["--no-persist", "-a", "esc_agent.py:graph", "go"])
    assert r.exit_code == 0, r.output
    assert r.stdout == "done\n", repr(r.stdout)
    assert "notes.txt" not in r.stderr and "screen-cleared" not in r.stderr, r.stderr


def test_history_view_escapes_stored_tool_results(capsys):
    from langgraph.checkpoint.memory import InMemorySaver

    from langstage_cli.cli import cmd_history

    graph = _esc_graph()
    graph.checkpointer = InMemorySaver()
    config = {"configurable": {"thread_id": "h-180"}}
    graph.invoke({"messages": [("user", "go")]}, config)
    cmd_history("", {"graph": graph, "config": config, "session_info": {}})
    out = capsys.readouterr().out
    _assert_no_raw_controls(out)
    assert "ok\\x1b[2J\\x1b[Hscreen-cleared-by-tool-result" in out, repr(out)
