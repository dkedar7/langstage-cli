"""--stream-mode is deprecated and inert (gh #62).

`--stream-mode {auto,updates,messages}` (and `LANGSTAGE_STREAM_MODE` / `[ui]
stream_mode`) was advertised as three streaming behaviors, but since the AG-UI
streaming migration all three render identically — the setting has no effect. The
dead knob is now: hidden from `--help`, omitted from `--show-config`, no longer
resolved from env / TOML, and accepted-and-ignored on the CLI (so existing
`--stream-mode X` invocations don't hard-error) with a one-line deprecation notice.

The unrelated "a finished AIMessage still renders" behavior (the reason the old
default was 'auto') is intrinsic to the AG-UI path now and is kept as a regression.
"""

import textwrap

from click.testing import CliRunner

from langstage_cli.cli import main

# A graph whose node returns a *finished* AIMessage (no token streaming).
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


def test_finished_aimessage_still_renders(tmp_path, monkeypatch):
    """Regression: a finished (non-token-streamed) AIMessage's content must render."""
    (tmp_path / "fin_agent.py").write_text(FINISHED_AIMESSAGE_AGENT)
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["-a", "fin_agent.py:graph", "--no-interactive", "ping"])
    assert r.exit_code == 0, r.output
    assert "FINISHED_MARKER_42" in r.output


def test_stream_mode_is_omitted_from_show_config():
    # gh #62: the deprecated no-op knob is no longer advertised in --show-config.
    r = CliRunner().invoke(main, ["--show-config"])
    assert r.exit_code == 0, r.output
    assert "stream_mode" not in r.output


def test_stream_mode_flag_is_accepted_and_ignored_with_a_notice():
    # An existing `--stream-mode X` invocation must not hard-error; it is accepted,
    # ignored, and prints a one-line deprecation notice.
    r = CliRunner().invoke(main, ["--demo", "--no-interactive", "--stream-mode", "messages", "hi"])
    assert r.exit_code == 0, r.output
    assert "deprecated" in r.output.lower()


def test_stream_mode_env_is_ignored_not_rejected():
    # LANGSTAGE_STREAM_MODE used to be validated (and an unsupported value exited 2).
    # It is now simply ignored — a run proceeds normally regardless of its value.
    r = CliRunner().invoke(
        main,
        ["--demo", "--no-interactive", "hi"],
        env={"LANGSTAGE_STREAM_MODE": "values"},
    )
    assert r.exit_code == 0, r.output
    assert "unsupported stream mode" not in r.output.lower()
