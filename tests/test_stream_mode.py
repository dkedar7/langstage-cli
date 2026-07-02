"""--stream-mode validation + the 'auto' default (gh #-dogfood).

`values` was advertised but unsupported by the CLI's render path; passing it
crashed with a fatal interpreter-shutdown error (a ValueError surfaced while the
spinner daemon thread held stdout). Now: the flag is a Choice {auto,updates,
messages}, and the resolved value (incl. LANGSTAGE_STREAM_MODE, which bypasses
the flag) is validated up front with a clean error.

Separately, single 'messages' mode only carries LLM *token* streams, so a node
that returns a finished (non-token-streamed) AIMessage rendered an empty turn.
The default is now 'auto' (dual updates+messages) so that agent shape — the one
the README's own "Creating Your Own Agent" example produces — still renders.
"""

import textwrap

from click.testing import CliRunner

from langstage_cli.cli import main

# A graph whose node returns a *finished* AIMessage (no token streaming) — the
# shape that was a silent blank turn under bare 'messages'.
FINISHED_AIMESSAGE_AGENT = textwrap.dedent(
    """
    from langchain_core.messages import AIMessage
    from langgraph.graph import START, END, StateGraph, MessagesState

    def _respond(state):
        return {"messages": [AIMessage(content="FINISHED_MARKER_42")]}

    builder = StateGraph(MessagesState)
    builder.add_node("respond", _respond)
    builder.add_edge(START, "respond")
    builder.add_edge("respond", END)
    graph = builder.compile()
    """
)


def test_default_auto_renders_finished_aimessage(tmp_path, monkeypatch):
    """Regression: the default mode must render a finished AIMessage's content."""
    (tmp_path / "fin_agent.py").write_text(FINISHED_AIMESSAGE_AGENT)
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["-a", "fin_agent.py:graph", "--no-interactive", "ping"])
    assert r.exit_code == 0, r.output
    assert "FINISHED_MARKER_42" in r.output


def test_default_stream_mode_is_auto():
    r = CliRunner().invoke(main, ["--show-config"])
    assert r.exit_code == 0, r.output
    assert "auto" in r.output


def test_stream_mode_values_flag_rejected_cleanly():
    r = CliRunner().invoke(main, ["--demo", "--no-interactive", "--stream-mode", "values", "hi"])
    assert r.exit_code != 0
    assert "values" in r.output
    assert "Fatal" not in r.output  # no interpreter-shutdown crash


def test_stream_mode_messages_flag_accepted():
    # A valid mode must still be accepted by the Choice (parses, doesn't error on the flag).
    r = CliRunner().invoke(main, ["--stream-mode", "messages", "--show-config"])
    assert r.exit_code == 0, r.output
    assert "messages" in r.output


def test_stream_mode_values_via_env_rejected():
    r = CliRunner().invoke(
        main,
        ["--demo", "--no-interactive", "hi"],
        env={"LANGSTAGE_STREAM_MODE": "values"},
    )
    assert r.exit_code == 2, r.output
    assert "unsupported stream mode" in r.output.lower()
