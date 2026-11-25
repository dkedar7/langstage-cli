"""An approval with stdin from ``NUL`` exits 2 instead of hanging (gh #217).

On Windows ``NUL`` is a character device, so ``isatty()`` is True for ``<NUL``. The
approval guard used a plain ``isatty()``, let the run into the arrow-key menu, and
``msvcrt.getch()`` (which reads the console, not stdin) then waited forever. Task
Scheduler, services, CI runners and ``subprocess.run(..., stdin=DEVNULL)`` all hand a
process ``NUL``, so an unattended approval-gated run never ended. The guard now asks
whether stdin can actually drive the menu.
"""

import subprocess
import sys
import textwrap

import pytest
from click.testing import CliRunner

from langstage_cli import cli
from langstage_cli.cli import main


class _Stream:
    def __init__(self, tty: bool):
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _on_windows(monkeypatch, *, console=None, name=None):
    monkeypatch.setattr(cli, "IS_WINDOWS", True)
    monkeypatch.setattr(cli, "_win_is_console", lambda stream: console)
    monkeypatch.setattr(cli, "_win_handle_name", lambda stream: name)


def test_windows_console_drives_the_menu(monkeypatch):
    _on_windows(monkeypatch, console=True)
    assert cli._stdin_drives_menu(_Stream(tty=True)) is True


def test_windows_nul_does_not_drive_the_menu(monkeypatch):
    # isatty() is True for NUL, but GetConsoleMode fails on it.
    _on_windows(monkeypatch, console=False)
    assert cli._stdin_drives_menu(_Stream(tty=True)) is False


def test_windows_console_check_unavailable_keeps_isatty(monkeypatch):
    _on_windows(monkeypatch, console=None)
    assert cli._stdin_drives_menu(_Stream(tty=True)) is True


def test_windows_mintty_pty_does_not_drive_the_menu(monkeypatch):
    # A mintty pty is interactive for line input, but msvcrt.getch() can't read it,
    # so the menu keeps refusing it cleanly (exit 2) rather than hanging.
    _on_windows(monkeypatch, name="\\msys-dd50a72ab4668b33-pty0-from-master")
    assert cli._stdin_is_interactive(_Stream(tty=False)) is True
    assert cli._stdin_drives_menu(_Stream(tty=False)) is False


@pytest.mark.parametrize("tty", [True, False])
def test_posix_is_plain_isatty(monkeypatch, tty):
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    assert cli._stdin_drives_menu(_Stream(tty=tty)) is tty


_HITL_AGENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import interrupt
    from langchain_core.messages import AIMessage

    def ask(state):
        decision = interrupt({"action": "delete_file", "path": "/tmp/x"})
        return {"messages": [AIMessage(content=f"Decision was: {decision}")]}

    g = StateGraph(MessagesState)
    g.add_node("ask", ask)
    g.add_edge(START, "ask")
    g.add_edge("ask", END)
    graph = g.compile(checkpointer=MemorySaver())
    """
)


def test_nul_stdin_approval_exits_paused_without_reading_keys(tmp_path, monkeypatch):
    # Simulate Windows `<NUL`: isatty() True, not a console.
    (tmp_path / "hitl_agent.py").write_text(_HITL_AGENT)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "IS_WINDOWS", True)
    monkeypatch.setattr(cli, "_win_is_console", lambda stream: False)
    monkeypatch.setattr(cli, "_is_a_tty", lambda stream: True)

    def _no_keys():
        raise AssertionError("the menu must not read keys from NUL")

    monkeypatch.setattr(cli, "get_key", _no_keys)
    r = CliRunner().invoke(main, ["-a", "hitl_agent.py:graph", "please act"])

    assert r.exit_code == 2, (r.exit_code, r.output, r.exception)
    assert "approval required but stdin is not a terminal" in r.stderr, r.stderr
    assert "--no-interactive" in r.stderr, r.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="NUL passes isatty() only on Windows")
def test_real_devnull_run_exits_2_instead_of_hanging():
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from langstage_cli.cli import main; main()",
            "-a",
            "langstage_core.demo.tools:graph",
            "ask me",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "approval required but stdin is not a terminal" in proc.stderr, proc.stderr
