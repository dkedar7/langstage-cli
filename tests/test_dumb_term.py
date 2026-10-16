"""``TERM=dumb`` gets no escape sequences at all (gh #182).

A dumb terminal (Emacs ``M-x shell``/``compile``, ``vim :!``, serial consoles) prints
escapes as literal text. ``main()`` honored ``NO_COLOR`` but not ``TERM=dumb``, and the
spinner and approval menu wrote line clears and cursor hide/show on their own. Now
``TERM=dumb`` strips color, the spinner prints one plain line, and the approval menu is
a numbered list answered by typing a number.
"""

import io
import os
import subprocess
import sys
import time
from contextlib import redirect_stdout

import pytest
from click.testing import CliRunner

from langstage_cli import cli


@pytest.fixture
def dumb(monkeypatch):
    monkeypatch.setattr(cli, "_DUMB_TERM", True)


def _typed(monkeypatch, *lines):
    it = iter(lines)
    shown = []

    def fake_input(prompt=""):
        shown.append(prompt)
        line = next(it)
        if isinstance(line, BaseException):
            raise line
        return line

    monkeypatch.setattr("builtins.input", fake_input)
    return shown


@pytest.mark.parametrize("value", ["dumb", "DUMB", " dumb "])
def test_term_dumb_is_detected(monkeypatch, value):
    monkeypatch.setenv("TERM", value)
    assert cli._is_dumb_term()


@pytest.mark.parametrize("value", ["xterm-256color", "", "dumber"])
def test_other_terms_are_not_dumb(monkeypatch, value):
    monkeypatch.setenv("TERM", value)
    assert not cli._is_dumb_term()


def test_term_dumb_strips_every_escape_on_a_terminal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_is_a_tty", lambda stream: True)
    monkeypatch.setenv("TERM", "dumb")
    r = CliRunner().invoke(cli.main, ["--demo", "hi"])
    assert r.exit_code == 0, r.output
    assert "You said: hi" in r.output and "Loaded" in r.output, r.output
    assert "\x1b" not in r.output, repr(r.output)
    # The spinner never redraws: no carriage return anywhere.
    assert "\r" not in r.output, repr(r.output)


def test_spinner_prints_one_plain_line_on_a_dumb_terminal(dumb, capsys):
    spinner = cli.Spinner("Thinking")
    spinner.start()
    time.sleep(0.2)  # an animating spinner would have drawn a couple of frames by now
    spinner.stop()
    assert capsys.readouterr().out == "Thinking...\n"


def test_menu_is_numbered_and_typed_on_a_dumb_terminal(dumb, monkeypatch):
    monkeypatch.setattr(cli, "get_key", lambda: pytest.fail("no keypress reading"))
    prompts = _typed(monkeypatch, "2")
    buf = io.StringIO()
    with redirect_stdout(buf):
        choice = cli.select_option(["Approve", "Reject", "Exit"], "How would you like to proceed?")
    assert choice == 1
    out = buf.getvalue()
    assert "  1. Approve\n  2. Reject\n  3. Exit\n" in out, repr(out)
    assert "\x1b" not in out + "".join(prompts)


def test_menu_enter_picks_the_first_option(dumb, monkeypatch):
    _typed(monkeypatch, "")
    with redirect_stdout(io.StringIO()):
        assert cli.select_option(["Approve", "Reject"]) == 0


def test_menu_asks_again_on_a_bad_choice(dumb, monkeypatch):
    prompts = _typed(monkeypatch, "9", "yes", "2")
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert cli.select_option(["Approve", "Reject"]) == 1
    assert len(prompts) == 3
    assert buf.getvalue().count("Enter a number from 1 to 2.") == 2


@pytest.mark.parametrize("abort", [KeyboardInterrupt(), EOFError()], ids=["ctrl-c", "eof"])
def test_leaving_the_typed_menu_exits_paused(dumb, monkeypatch, abort):
    _typed(monkeypatch, abort)
    with redirect_stdout(io.StringIO()), pytest.raises(SystemExit) as exc:
        cli.select_option(["Approve", "Exit"])
    assert exc.value.code == 2


def _run_on_pty(tmp_path, term, args, answers=b""):
    import pty
    import select

    env = {k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "TERM")}
    env["LANGSTAGE_CLI_SESSIONS_DIR"] = str(tmp_path / "_sessions")
    env["TERM"] = term
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, "-c", "from langstage_cli.cli import main; main()", *args],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=tmp_path,
        env=env,
        close_fds=True,
    )
    os.close(slave)
    out = b""
    sent = False
    deadline = time.monotonic() + 60
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.5)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            try:
                data = os.read(master, 4096)
            except OSError:  # EIO: the child closed the terminal
                break
            if not data:
                break
            out += data
            if answers and not sent and b"Choose 1-" in out:
                os.write(master, answers)
                sent = True
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
        os.close(master)
    return proc.returncode, out.decode("utf-8", errors="replace")


_HITL_AGENT = """
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import MessagesState
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt
from langchain_core.messages import AIMessage

def ask(state):
    decision = interrupt({"action": "delete_file", "path": "/etc/hosts"})
    return {"messages": [AIMessage(content=f"Decision was: {decision!r}")]}

g = StateGraph(MessagesState)
g.add_node("ask", ask)
g.add_edge(START, "ask")
g.add_edge("ask", END)
graph = g.compile(checkpointer=MemorySaver())
"""


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")
def test_no_escapes_on_a_real_dumb_terminal(tmp_path):
    code, out = _run_on_pty(tmp_path, "dumb", ["--demo", "hi"])
    assert code == 0, out
    assert "You said: hi" in out and "╭" in out, repr(out)
    assert "\x1b" not in out, repr(out)


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")
def test_approval_menu_works_on_a_real_dumb_terminal(tmp_path):
    (tmp_path / "hitl_agent.py").write_text(_HITL_AGENT, encoding="utf-8")
    code, out = _run_on_pty(tmp_path, "dumb", ["-a", "hitl_agent.py:graph", "go"], b"1\n")
    assert "1. " in out and "Choose 1-" in out, repr(out)
    assert "Decision was" in out, repr(out)
    assert code == 0, out
    assert "\x1b" not in out, repr(out)
