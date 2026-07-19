"""HITL approval on a non-TTY fails cleanly instead of crashing (gh #86).

The arrow-key approval menu needs a real terminal. Run in the scriptable context the
README promotes — a piped / CI / cron single-shot invocation with stdin not a tty —
the CLI used to print the cursor-hide escape and the whole menu to **stdout** (the
stream a script captures, contractually reply-only), then crash inside `get_key()`
where `termios.tcgetattr()` raises on a non-tty, surfacing as a cryptic
`Error: (25, 'Inappropriate ioctl for device')` and exit 1.

The fix guards on `sys.stdin.isatty()` *before* anything is printed and exits with an
actionable message pointing at `--no-interactive`. It deliberately does NOT
auto-approve: `interrupt()` is a request for human review, and approving an action
nobody saw would be a worse failure than stopping. `--no-interactive` stays the
documented opt-in, and its behavior is unchanged (see test_no_interactive_approve.py).
"""

import textwrap

from click.testing import CliRunner

from langstage_cli.cli import main

_HITL_AGENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import interrupt
    from langchain_core.messages import AIMessage

    def ask(state):
        decision = interrupt({"action": "delete_file", "path": "/etc/hosts"})
        return {"messages": [AIMessage(content=f"Decision was: {decision}")]}

    g = StateGraph(MessagesState)
    g.add_node("ask", ask)
    g.add_edge(START, "ask")
    g.add_edge("ask", END)
    graph = g.compile(checkpointer=MemorySaver())
    """
)


def _invoke(tmp_path, monkeypatch):
    """One interactive (no --no-interactive) single-shot run whose stdin is not a
    terminal — CliRunner's stdin is a buffer, exactly like `</dev/null` or a pipe."""
    (tmp_path / "hitl_agent.py").write_text(_HITL_AGENT)
    monkeypatch.chdir(tmp_path)
    return CliRunner().invoke(main, ["-a", "hitl_agent.py:graph", "please act"])


def test_non_tty_approval_exits_with_an_actionable_error(tmp_path, monkeypatch):
    r = _invoke(tmp_path, monkeypatch)

    assert r.exit_code != 0, f"a run that could not get approval must not exit 0: {r.output!r}"
    assert "stdin is not a terminal" in r.stderr, r.stderr
    # The message must name the escape hatch, not just describe the failure.
    assert "--no-interactive" in r.stderr, r.stderr
    # And it must be the clean message, not the raw termios errno.
    assert "Inappropriate ioctl" not in r.output, r.output
    assert "(25," not in r.output, r.output


def test_non_tty_approval_leaks_nothing_to_stdout(tmp_path, monkeypatch):
    """stdout is the machine-readable stream. The menu, its cursor escapes, and the
    error itself must all stay off it."""
    r = _invoke(tmp_path, monkeypatch)

    assert r.stdout == "", f"stdout must stay clean for the pipe, got {r.stdout!r}"
    # Belt and braces: the specific artifacts the old code emitted.
    assert "\033[?25l" not in r.stdout  # cursor hide
    assert "How would you like to proceed?" not in r.stdout
    assert "Approve all actions" not in r.stdout


def test_no_interactive_still_auto_approves_on_the_same_input(tmp_path, monkeypatch):
    """The guard must not change the documented opt-in path: with --no-interactive
    the identical non-tty run still approves, resumes, and exits 0."""
    (tmp_path / "hitl_agent.py").write_text(_HITL_AGENT)
    monkeypatch.chdir(tmp_path)

    r = CliRunner().invoke(main, ["-a", "hitl_agent.py:graph", "please act", "--no-interactive"])
    assert r.exit_code == 0, r.output
    assert "Decision was:" in r.stdout
    assert "stdin is not a terminal" not in r.output
