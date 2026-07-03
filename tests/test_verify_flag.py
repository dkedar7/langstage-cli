"""`--verify` preflights the configured agent with a real turn (ADR 0004 adoption).

It delegates to `langstage_core.agui.verify`, so a green here means the agent
actually completed a turn — a real CI gate — not just that it imported. Exit 0 on
success, non-zero on failure, with the diagnostic on stderr so stdout stays clean.
"""

import textwrap

from click.testing import CliRunner

from langstage_cli.cli import main

_BROKEN_AGENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END, MessagesState

    def boom(state):
        raise RuntimeError("tool exploded")

    b = StateGraph(MessagesState)
    b.add_node("boom", boom)
    b.add_edge(START, "boom")
    b.add_edge("boom", END)
    graph = b.compile()
    """
)


def test_verify_demo_passes_exit_zero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["--demo", "--verify"])
    assert r.exit_code == 0, r.output
    assert "verified" in r.output  # the pass verdict, on stdout
    # A preflight is not a chat: no header/marker leaks.
    assert "⏺" not in r.output and "\x1b[" not in r.output, r.output


def test_verify_broken_agent_fails_exit_one(tmp_path, monkeypatch):
    (tmp_path / "broken.py").write_text(_BROKEN_AGENT)
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["-a", "broken.py:graph", "--verify"])
    assert r.exit_code == 1
    assert r.stdout.strip() == "", r.stdout  # stdout stays clean for scripting
    assert "verification failed" in r.stderr, r.stderr
