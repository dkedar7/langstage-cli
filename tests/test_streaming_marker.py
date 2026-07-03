"""Token streaming prints the cyan bullet once per turn, not per token (gh #34).

A token-streaming model emits one chunk per token; the CLI used to prefix the
`⏺` marker on every chunk, jamming a bullet before every token and garbling the
reply. The marker must print once at the start of a streamed AI turn.
"""

import textwrap

from click.testing import CliRunner

from langstage_cli import cli as c
from langstage_cli.cli import main


def test_marker_printed_once_per_text_run(capsys):
    c.print_chunk._streaming_text = False
    # A run of streamed text tokens -> ONE marker.
    for tok in ["a", "b", "c"]:
        c.print_chunk({"status": "streaming", "chunk": tok})
    # A tool call ends the text run...
    c.print_chunk({"status": "streaming", "tool_calls": [{"name": "t", "args": {}}]})
    # ...so the next run of text gets a fresh marker.
    for tok in ["d", "e"]:
        c.print_chunk({"status": "streaming", "chunk": tok})

    out = capsys.readouterr().out
    assert out.count("⏺") == 2, out  # one per text run, NOT one per token (which would be 5)


_TOK_AGENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langchain_core.messages import AIMessage
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    # GenericFakeChatModel streams its content token-by-token, like a real LLM.
    model = GenericFakeChatModel(messages=iter([AIMessage(content="alpha beta gamma")]))

    def respond(state):
        return {"messages": [model.invoke(state["messages"])]}

    g = StateGraph(MessagesState)
    g.add_node("respond", respond)
    g.add_edge(START, "respond")
    g.add_edge("respond", END)
    graph = g.compile()
    """
)


def test_token_stream_scriptable_when_piped(tmp_path, monkeypatch):
    # gh #53: a single-shot run under CliRunner (stdout is not a TTY) auto-enables
    # quiet output, so the piped reply is ONLY the streamed tokens — no `⏺` marker,
    # header box, "Loaded" line, or timing. (Per-turn marker dedup, gh #34, is still
    # covered by test_marker_printed_once_per_text_run driving print_chunk directly.)
    (tmp_path / "tok.py").write_text(_TOK_AGENT)
    monkeypatch.chdir(tmp_path)

    r = CliRunner().invoke(main, ["-a", "tok.py:graph", "hi", "--no-interactive"])
    assert r.exit_code == 0, r.output
    assert r.output.count("⏺") == 0, r.output  # no decoration on the scriptable path
    assert "Loaded" not in r.output and "You" not in r.output, r.output
    for word in ("alpha", "beta", "gamma"):
        assert word in r.output, r.output


# gh #40: the verbose branch must print the [node] label once per run too,
# not before every token (the #34 fix only covered the non-verbose branch).


def test_verbose_node_label_printed_once_per_run(capsys):
    c.print_chunk._streaming_text = False
    c.print_chunk._streaming_node = None
    for tok in ["a", "b", "c"]:
        c.print_chunk({"status": "streaming", "chunk": tok, "node": "respond"}, verbose=True)
    out = capsys.readouterr().out
    assert out.count("[respond]") == 1, out  # one label, NOT one per token (3)
    assert "a" in out and "b" in out and "c" in out  # the tokens still stream


def test_verbose_label_reprints_after_a_tool_call(capsys):
    c.print_chunk._streaming_text = False
    c.print_chunk._streaming_node = None
    for tok in ["a", "b"]:
        c.print_chunk({"status": "streaming", "chunk": tok, "node": "respond"}, verbose=True)
    c.print_chunk({"status": "streaming", "tool_calls": [{"name": "t", "args": {}}]}, verbose=True)
    for tok in ["c", "d"]:
        c.print_chunk({"status": "streaming", "chunk": tok, "node": "respond"}, verbose=True)
    out = capsys.readouterr().out
    assert out.count("[respond]") == 2, out  # one per text run


def test_verbose_label_reprints_on_node_change(capsys):
    c.print_chunk._streaming_text = False
    c.print_chunk._streaming_node = None
    c.print_chunk({"status": "streaming", "chunk": "x", "node": "alpha"}, verbose=True)
    c.print_chunk({"status": "streaming", "chunk": "y", "node": "beta"}, verbose=True)
    out = capsys.readouterr().out
    assert out.count("[alpha]") == 1 and out.count("[beta]") == 1, out
