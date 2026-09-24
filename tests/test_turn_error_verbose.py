"""Turn-time agent errors honor ``-v`` like load errors do (gh #153).

An exception inside a node used to print a locationless ``Error: KeyError:
'MISSING_KEY'`` whether or not ``-v`` was given, with no "re-run with -v" hint. Now
``-v`` runs the turn with ``LANGSTAGE_DEBUG`` set, so core puts the traceback on the
error frame and the CLI prints it; without ``-v`` the error line points at ``-v``.
"""

import os
import textwrap

from click.testing import CliRunner

from langstage_cli.cli import main

_TURN_ERR = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState

    def respond(state):
        data = {"a": 1}
        return {"messages": [data["MISSING_KEY"]]}   # KeyError deep in user code

    g = StateGraph(MessagesState)
    g.add_node("respond", respond)
    g.add_edge(START, "respond")
    g.add_edge("respond", END)
    graph = g.compile()
    """
)

_HINT = "Re-run with -v for the full traceback."


def _write(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LANGSTAGE_DEBUG", raising=False)
    (tmp_path / "turn_err153.py").write_text(_TURN_ERR, encoding="utf-8")


def test_turn_error_without_v_points_at_v(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch)
    r = CliRunner().invoke(main, ["--no-interactive", "-a", "turn_err153.py:graph", "x"])
    assert r.exit_code == 1, r.output
    assert "Error: KeyError: 'MISSING_KEY'" in r.stderr, r.output
    assert _HINT in r.stderr, r.output
    assert "Traceback" not in r.output, r.output  # opt-in only
    assert r.stdout.strip() == "", repr(r.stdout)  # the pipe stays clean


def test_turn_error_with_v_prints_the_node_traceback(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch)
    r = CliRunner().invoke(main, ["--no-interactive", "-v", "-a", "turn_err153.py:graph", "x"])
    assert r.exit_code == 1, r.output
    assert "Error: KeyError: 'MISSING_KEY'" in r.stderr, r.output
    # The traceback names the user's file and the failing line.
    assert "Traceback (most recent call last)" in r.stderr, r.output
    assert "turn_err153.py" in r.stderr, r.output
    assert 'data["MISSING_KEY"]' in r.stderr, r.output
    assert _HINT not in r.output, r.output
    # -v's debug switch is scoped to the turn; nothing leaks into the process env.
    assert "LANGSTAGE_DEBUG" not in os.environ


def test_turn_error_interactive_render_hint(tmp_path, monkeypatch):
    # The rich (non-quiet) render gets the same hint.
    from langstage_cli import cli

    monkeypatch.setattr(cli, "_QUIET", False)
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.print_chunk({"status": "error", "error": "KeyError: 'k'"}, verbose=False)
        cli.print_chunk(
            {"status": "error", "error": "KeyError: 'k'", "traceback": "Traceback ...\nKeyError"},
            verbose=True,
        )
    out = buf.getvalue()
    assert out.count(_HINT) == 1, out
    assert "Traceback ..." in out, out
