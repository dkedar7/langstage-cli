"""A typed answer to a plain ``interrupt("…")`` reaches the graph as text (gh #206).

The answer was tried as JSON first, by the CLI and again by core's resume wire, so
what the graph received depended on what the user happened to type: ``94110`` arrived
as an int (and crashed an agent that called ``.strip()`` on it), ``true`` as a bool,
``1e3`` as a float, and ``null`` as ``None``, which LangGraph reads as "no answer" and
asks the same question again. ``02139`` stayed a string, so two answers to one
question had different types.

Now the answer is the text that was typed, as ``input()`` would return it. The one
exception keeps gh #99: a complete JSON object or array is sent as that dict or list.
"""

import io
import os
import subprocess
import sys
import textwrap
import time
from contextlib import redirect_stdout

import pytest
from click.testing import CliRunner

pytest.importorskip("ag_ui_langgraph")
pytest.importorskip("fastapi")

from langstage_cli import cli  # noqa: E402
from langstage_cli.agui_stream import build_session_agent, text_resume  # noqa: E402
from langstage_cli.cli import _generic_answer, run_single_turn_agui  # noqa: E402

# Echoes exactly what interrupt() returned, with its type.
_ECHO_AGENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import interrupt
    from langchain_core.messages import AIMessage

    def respond(state):
        ans = interrupt("What is your ZIP code?")
        return {"messages": [AIMessage(content=f"answer={ans!r} type={type(ans).__name__}")]}

    g = StateGraph(MessagesState)
    g.add_node("r", respond)
    g.add_edge(START, "r")
    g.add_edge("r", END)
    graph = g.compile(checkpointer=MemorySaver())
    """
)

# The issue's agent: it asked for text, so it treats the answer as text.
_ZIP_AGENT = _ECHO_AGENT.replace(
    'f"answer={ans!r} type={type(ans).__name__}"',
    'f"Shipping to ZIP {ans.strip()} (type={type(ans).__name__})"',
)

_TEXT = [
    "94110",
    "02139",
    "true",
    "false",
    "null",
    "1e3",
    "-4.5",
    "yes",
    "Alice",
    '"quoted"',
    "  padded  ",
    "",
    "   ",
    "[sic] yes",
    "[1, 2",
    '{"a": 1} or so',
    "{not json}",
]


@pytest.mark.parametrize("typed", _TEXT)
def test_an_answer_is_the_text_that_was_typed(typed):
    assert _generic_answer(typed) == typed
    assert type(_generic_answer(typed)) is str


@pytest.mark.parametrize(
    ("typed", "value"),
    [
        ('["Alice", "B"]', ["Alice", "B"]),
        ('{"a": 1}', {"a": 1}),
        ('  {"a": [1, null, true]}  ', {"a": [1, None, True]}),
        ("[]", []),
        ("{}", {}),
    ],
)
def test_a_complete_json_object_or_array_is_sent_as_data(typed, value):
    assert _generic_answer(typed) == value


@pytest.mark.parametrize("text", ["94110", "true", "null", "1e3", "", "Alice", '"q"', "é ✓"])
def test_text_survives_the_json_decode_cores_wire_applies(text):
    import json

    assert json.loads(text_resume(text)) == text


def test_only_text_is_encoded_for_the_wire():
    decisions = {"decisions": [{"type": "approve"}]}
    assert text_resume(decisions) is decisions
    assert text_resume(["Alice", "B"]) == ["Alice", "B"]
    assert text_resume(None) is None


def _build(source: str):
    ns: dict = {}
    exec(source, ns)
    return build_session_agent(ns["graph"])


async def _answer(monkeypatch, source: str, typed: str, thread: str) -> tuple[str, int, bool]:
    """Run one turn, answer its interrupt with ``typed``. Returns (output, menus, error)."""
    agent = _build(source)
    menus = []

    def menu(options, prompt=""):
        menus.append(prompt)
        if len(menus) > 2:  # the question came back: stop instead of looping forever
            raise AssertionError("the same question was asked again")
        return 0

    monkeypatch.setattr(cli, "_is_a_tty", lambda *a, **k: True)
    monkeypatch.setattr(cli, "select_option", menu)
    monkeypatch.setattr("builtins.input", lambda *a, **k: typed)
    buf = io.StringIO()
    with redirect_stdout(buf):
        _elapsed, had_error = await run_single_turn_agui(agent, "hi", thread, interactive=True)
    return buf.getvalue(), len(menus), had_error


@pytest.mark.parametrize("typed", _TEXT)
async def test_the_graph_receives_the_typed_text(monkeypatch, typed):
    out, menus, had_error = await _answer(monkeypatch, _ECHO_AGENT, typed, f"t-{typed!r}")
    assert had_error is False, out
    assert f"answer={typed!r} type=str" in out, out
    assert menus == 1, out  # `null` used to bring the question back


@pytest.mark.parametrize(
    ("typed", "shown"),
    [
        ('["Alice", "B"]', "answer=['Alice', 'B'] type=list"),
        ('{"a": 1}', "answer={'a': 1} type=dict"),
    ],
)
async def test_the_graph_receives_a_structured_answer_as_data(monkeypatch, typed, shown):
    out, menus, had_error = await _answer(monkeypatch, _ECHO_AGENT, typed, f"t-{typed!r}")
    assert had_error is False, out
    assert shown in out, out
    assert menus == 1, out


@pytest.mark.parametrize("zip_code", ["94110", "02139"])
async def test_the_issues_zip_agent_gets_a_string_either_way(monkeypatch, zip_code):
    out, _menus, had_error = await _answer(monkeypatch, _ZIP_AGENT, zip_code, f"zip-{zip_code}")
    assert had_error is False, out
    assert f"Shipping to ZIP {zip_code} (type=str)" in out, out
    assert "AttributeError" not in out, out


async def test_the_prompt_says_how_the_answer_is_sent(monkeypatch):
    out, _menus, _err = await _answer(monkeypatch, _ECHO_AGENT, "94110", "t-hint")
    assert "sent as text" in out, out
    assert out.index("sent as text") < out.index("answer='94110'"), out


@pytest.mark.parametrize("typed", ["94110", "null", "Alice"])
async def test_text_survives_the_legacy_resume_wire_too(monkeypatch, typed):
    # Core falls back to forwarded_props.command.resume on an adapter without resume[];
    # that reader JSON-decodes a string as well.
    agui = pytest.importorskip("langstage_core.agui")
    if not hasattr(agui, "_supports_agui_resume"):
        pytest.skip("this core has no resume-wire switch to force")
    monkeypatch.setattr(agui, "_supports_agui_resume", lambda agent: False)
    out, menus, had_error = await _answer(monkeypatch, _ECHO_AGENT, typed, f"legacy-{typed}")
    assert had_error is False, out
    assert f"answer={typed!r} type=str" in out, out
    assert menus == 1, out


async def test_no_interactive_still_resumes_with_an_empty_string():
    agent = _build(_ECHO_AGENT)
    buf = io.StringIO()
    with redirect_stdout(buf):
        _elapsed, had_error = await run_single_turn_agui(agent, "hi", "t-auto", interactive=False)
    assert had_error is False, buf.getvalue()
    assert "answer='' type=str" in buf.getvalue(), buf.getvalue()


def test_the_numbered_menu_on_a_dumb_terminal_sends_text(tmp_path, monkeypatch):
    # TERM=dumb answers the menu by typing a number, then the answer on the next line.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LANGSTAGE_CLI_SESSIONS_DIR", str(tmp_path / "_sessions"))
    monkeypatch.setenv("TERM", "dumb")
    (tmp_path / "zip_agent.py").write_text(_ECHO_AGENT, encoding="utf-8")
    monkeypatch.setattr(cli, "_is_a_tty", lambda stream: True)
    typed = iter(["1", "94110"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(typed))
    r = CliRunner().invoke(cli.main, ["-a", "zip_agent.py:graph", "ship my order"])
    assert r.exit_code == 0, r.output
    assert "answer='94110' type=str" in r.output, r.output
    assert "sent as text" in r.output, r.output
    assert "\x1b" not in r.output, repr(r.output)


def test_a_paused_session_answered_later_gets_text(tmp_path, monkeypatch):
    # Left paused (exit 2), then answered on the next run: the same prompt, the same rule.
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LANGSTAGE_CLI_SESSIONS_DIR", str(tmp_path / "_sessions"))
    (tmp_path / "zip_agent.py").write_text(
        _ECHO_AGENT.replace("checkpointer=MemorySaver()", ""), encoding="utf-8"
    )
    paused = CliRunner().invoke(cli.main, ["-a", "zip_agent.py:graph", "ship my order"])
    assert paused.exit_code == 2, paused.output

    monkeypatch.setattr(cli, "_is_a_tty", lambda stream: True)
    monkeypatch.setattr(cli, "select_option", lambda *a, **k: 0)
    answers = iter(["true", "null"])  # the old question, then the new message's question
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    r = CliRunner().invoke(cli.main, ["-a", "zip_agent.py:graph", "-c", "and another"])
    assert r.exit_code == 0, r.output
    assert "answer='true' type=str" in r.output, r.output
    assert "answer='null' type=str" in r.output, r.output


def _type_on_pty(tmp_path, term, args, steps):
    """Run the CLI on a real pty; for each ``(wait_for, send)`` step, send once seen."""
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
    steps = list(steps)
    seen_upto = 0
    deadline = time.monotonic() + 90
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
            while steps and steps[0][0] in out[seen_upto:]:
                wait_for, send = steps.pop(0)
                seen_upto = out.index(wait_for, seen_upto) + len(wait_for)
                os.write(master, send)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
        os.close(master)
    return proc.returncode, out.decode("utf-8", errors="replace")


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")
@pytest.mark.parametrize("typed", ["94110", "null", "true"])
def test_text_typed_at_the_real_prompt_reaches_the_graph_as_text(tmp_path, typed):
    (tmp_path / "zip_agent.py").write_text(_ECHO_AGENT, encoding="utf-8")
    code, out = _type_on_pty(
        tmp_path,
        "dumb",
        ["-a", "zip_agent.py:graph", "ship my order"],
        [(b"Choose 1-", b"1\n"), (b"sent as text", typed.encode() + b"\n")],
    )
    assert f"answer={typed!r} type=str" in out, repr(out)
    assert out.count("What is your ZIP code?") == 1, repr(out)  # asked once
    assert code == 0, out


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")
def test_arrow_key_menu_then_typed_text_on_a_real_terminal(tmp_path):
    (tmp_path / "zip_agent.py").write_text(_ZIP_AGENT, encoding="utf-8")
    code, out = _type_on_pty(
        tmp_path,
        "xterm-256color",
        ["-a", "zip_agent.py:graph", "ship my order"],
        [(b"Provide a response", b"\r"), (b"sent as text", b"94110\n")],
    )
    assert "Shipping to ZIP 94110 (type=str)" in out, repr(out)
    assert "AttributeError" not in out, repr(out)
    assert code == 0, out
