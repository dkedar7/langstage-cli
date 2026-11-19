"""The custom decision prompt shows shapes core accepts, and checks them the way core
does, for every interrupt core checks (gh #209).

The keyless demo's approval (``langstage_core.demo.tools``, "ask me") is a dict with
``action_requests`` and ``allowed_decisions`` but no ``review_configs``. Core checks an
answer to it as a HITL request, so ``respond`` needs a text ``message``. The CLI only
treated interrupts with ``review_configs`` that way: the prompt hinted a bare
``{"type": "respond"}``, let it through, and core's refusal came back as a failed turn
that dropped the user at the main prompt with the approval still pending.

Now the CLI reads the graph's own pending value and applies core's test to it, and a
decision core still refuses brings the approval menu back instead of failing the turn.
"""

import io
import os
import subprocess
import sys
import time
from contextlib import redirect_stdout

import pytest

from langstage_cli import cli

# What core puts on the frame for the demo's approval, and the graph's own value.
_DEMO_VALUE = {
    "action_requests": [{"action": "ask_user", "args": {"question": "What should I call you?"}}],
    "allowed_decisions": ["respond", "approve"],
}
_DEMO_FRAME = {**_DEMO_VALUE, "review_configs": []}
_DEMO = {**_DEMO_FRAME, cli._PENDING_VALUE: _DEMO_VALUE}

# A legacy HumanInterrupt list: core normalizes it into action_requests on the frame,
# but doesn't check answers to it (it reads its own payload).
_LEGACY = {
    "action_requests": [{"action": "write_file", "args": {"path": "x"}}],
    "review_configs": [],
    "allowed_decisions": ["approve", "respond"],
    cli._PENDING_VALUE: [{"action_request": {"action": "write_file", "args": {"path": "x"}}}],
}


def test_core_test_is_applied_to_the_graphs_own_value():
    assert cli._is_hitl_request(_DEMO)
    assert not cli._is_hitl_request(_LEGACY)
    # Without the graph's value (no checkpointer), review_configs is still the sign.
    assert not cli._is_hitl_request(_DEMO_FRAME)
    assert cli._is_hitl_request({**_DEMO_FRAME, "review_configs": [{"action_name": "a"}]})


def test_the_hint_shows_the_message_respond_needs():
    shapes = cli._decision_shapes(cli._allowed_verbs(_DEMO, 0), _DEMO)
    assert shapes == '{"type": "approve"} | {"type": "respond", "message": "reply"}'


def test_respond_without_a_message_is_caught_in_the_prompt():
    decisions, problem = cli._check_custom_decision({"type": "respond"}, 1, _DEMO)
    assert decisions == []
    assert 'respond needs a text "message"' in problem
    ok = {"type": "respond", "message": "Ada"}
    assert cli._check_custom_decision(ok, 1, _DEMO) == ([ok], None)


def test_an_interrupt_core_doesnt_check_still_gets_only_the_verb_check():
    assert cli._check_custom_decision({"type": "respond"}, 1, _LEGACY)[1] is None
    assert "not allowed" in cli._check_custom_decision({"type": "reject"}, 1, _LEGACY)[1]


# ---- End to end, in process, on the keyless demo ------------------------------------


def _demo_agent():
    pytest.importorskip("ag_ui_langgraph")
    from langgraph.checkpoint.memory import MemorySaver
    from langstage_core.demo.tools import graph

    from langstage_cli.agui_stream import build_session_agent

    return build_session_agent(graph.copy(update={"checkpointer": MemorySaver()}))


def _menu(monkeypatch, choices, typed):
    monkeypatch.setattr(cli, "_is_a_tty", lambda *a, **k: True)
    picks, lines, shown = iter(choices), iter(typed), []

    def select(options, prompt=""):
        shown.append(list(options))
        return next(picks)

    monkeypatch.setattr(cli, "select_option", select)
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(lines))
    return shown


async def test_the_demo_approval_refuses_a_bare_respond_in_place(monkeypatch):
    agent = _demo_agent()
    shown = _menu(
        monkeypatch, [1], ['{"type": "respond"}', '{"type": "respond", "message": "Ada"}']
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        _elapsed, had_error = await cli.run_single_turn_agui(agent, "ask me", "t-209", True)
    out = buf.getvalue()
    assert had_error is False, out
    assert '{"type": "respond", "message": "reply"}' in out, out
    assert 'respond needs a text "message". Nothing was sent; try again.' in out, out
    assert "Error" not in out, out
    assert "Ada" in out.split("try again.")[-1], out  # the agent got the second answer
    assert len(shown) == 1, out


async def test_a_decision_core_refuses_brings_the_approval_back(monkeypatch):
    # Whatever slips past the CLI's check (an older or different rule in core), core
    # refuses it before resuming. The approval is still pending, so ask it again.
    agent = _demo_agent()
    monkeypatch.setattr(cli, "_check_custom_decision", lambda v, n, i: ([v] * n, None))
    shown = _menu(monkeypatch, [1, 0], ['{"type": "respond"}'])
    buf = io.StringIO()
    with redirect_stdout(buf):
        _elapsed, had_error = await cli.run_single_turn_agui(agent, "ask me", "t-209b", True)
    out = buf.getvalue()
    assert had_error is False, out
    assert "answer the approval again" in out, out
    assert "✗ Error" not in out and "Re-run with -v" not in out, out
    assert len(shown) == 2, out  # the menu came back; "Approve all" then resumed
    assert "Resumed" in out, out


# ---- On a real terminal --------------------------------------------------------------


def _type_on_pty(tmp_path, args, steps):
    """Run the CLI on a real pty, sending each ``(wait_for, send)`` once the CLI waits."""
    import pty
    import select

    env = {k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "TERM")}
    env["LANGSTAGE_CLI_SESSIONS_DIR"] = str(tmp_path / "_sessions")
    env["LANGSTAGE_CONFIG_HOME"] = str(tmp_path / "_home")
    env["TERM"] = "dumb"
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
    out, steps, seen_upto = b"", list(steps), 0
    deadline = time.monotonic() + 90
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.5)
            if not ready:
                if proc.poll() is not None:
                    break
                if steps and steps[0][0] in out[seen_upto:]:
                    wait_for, send = steps.pop(0)
                    seen_upto = out.index(wait_for, seen_upto) + len(wait_for)
                    os.write(master, send)
                continue
            try:
                data = os.read(master, 4096)
            except OSError:  # EIO: the child closed the terminal
                break
            if not data:
                break
            out += data
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
        os.close(master)
    return proc.returncode, out.decode("utf-8", errors="replace")


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")
def test_the_demo_approval_on_a_real_terminal(tmp_path):
    pytest.importorskip("ag_ui_langgraph")
    code, out = _type_on_pty(
        tmp_path,
        ["-a", "langstage_core.demo.tools:graph", "ask me"],
        [
            (b"Choose 1-", b"2\n"),
            (b"go back to the menu", b'{"type": "respond"}\n'),
            (b"try again", b'{"type": "respond", "message": "Ada"}\n'),
        ],
    )
    assert '{"type": "respond", "message": "reply"}' in out, repr(out)
    assert 'respond needs a text "message". Nothing was sent; try again.' in out, repr(out)
    assert "Error" not in out and "Re-run with -v" not in out, repr(out)
    assert "Resumed" in out, repr(out)
    assert code == 0, out
