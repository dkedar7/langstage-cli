"""The approval menu offers only the decisions the pending interrupt allows (gh #197).

"Approve all actions" and "Reject all actions" were always offered, so on LangChain's
HumanInTheLoopMiddleware(allowed_decisions=["approve", "edit"]) "Reject all" sent a
decision the middleware refuses and the turn errored. ``--no-interactive`` likewise
sent an approve to an action that doesn't allow one. Each is now offered (or sent) only
when every pending action allows it.
"""

import io
import textwrap
from contextlib import redirect_stdout

import pytest

from langstage_cli import cli


def _frame(*tools):
    """An interrupt frame for (name, allowed_decisions) pairs, as core emits it."""
    union = sorted({v for _, allowed in tools for v in allowed})
    return {
        "action_requests": [{"name": n, "args": {}} for n, _ in tools],
        "review_configs": [{"action_name": n, "allowed_decisions": a} for n, a in tools],
        "allowed_decisions": union,
    }


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(cli, "_is_a_tty", lambda *a, **k: True)


def _pick(monkeypatch, label):
    """Choose the menu entry ``label``, recording every menu shown."""
    shown = []

    def fake_select(options, prompt=""):
        shown.append(list(options))
        return options.index(label)

    monkeypatch.setattr(cli, "select_option", fake_select)
    return shown


@pytest.mark.parametrize(
    "frame, expected",
    [
        (
            _frame(("send_email", ["approve", "edit"])),
            ["Approve all actions", "Provide custom decision (JSON)", "Exit"],
        ),
        (
            _frame(("send_email", ["edit", "reject"])),
            ["Reject all actions", "Provide custom decision (JSON)", "Exit"],
        ),
        (
            _frame(("send_email", ["respond"])),
            ["Provide custom decision (JSON)", "Exit"],
        ),
        # Per tool: one of two actions doesn't allow reject, so "Reject all" can't apply.
        (
            _frame(("a", ["approve", "reject"]), ("b", ["approve"])),
            ["Approve all actions", "Provide custom decision (JSON)", "Exit"],
        ),
        # No allowed list at all (an older or plain interrupt): the full menu, as before.
        (
            None,
            [
                "Approve all actions",
                "Reject all actions",
                "Provide custom decision (JSON)",
                "Exit",
            ],
        ),
    ],
)
def test_menu_offers_only_allowed_bulk_decisions(frame, expected, tty, monkeypatch):
    shown = _pick(monkeypatch, "Exit")
    n = len(frame["action_requests"]) if frame else 1
    with pytest.raises(SystemExit) as exc:
        cli.handle_interrupt_input(n, interrupt=frame)
    assert exc.value.code == 2
    assert shown == [expected]


def test_each_entry_sends_its_own_decision(tty, monkeypatch):
    frame = _frame(("a", ["edit", "reject"]), ("b", ["reject"]))
    _pick(monkeypatch, "Reject all actions")
    assert cli.handle_interrupt_input(2, interrupt=frame) == {
        "decisions": [{"type": "reject"}, {"type": "reject"}]
    }
    _pick(monkeypatch, "Provide custom decision (JSON)")
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: '{"type": "reject"}')
    with redirect_stdout(io.StringIO()):
        assert cli.handle_interrupt_input(2, interrupt=frame) == {
            "decisions": [{"type": "reject"}, {"type": "reject"}]
        }


# ---- End to end on the issue's agent ------------------------------------------------

_AGENT = textwrap.dedent(
    """
    from langchain.agents import create_agent
    from langchain.agents.middleware import HumanInTheLoopMiddleware
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class Model(BaseChatModel):
        @property
        def _llm_type(self):
            return "fake-197"

        def bind_tools(self, tools, **kw):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kw):
            if messages[-1].type == "tool":
                msg = AIMessage(content=f"done: {messages[-1].content}")
            else:
                msg = AIMessage(content="", tool_calls=[
                    {"name": "send_email", "args": {"to": "a@example.org"}, "id": "c1"}
                ])
            return ChatResult(generations=[ChatGeneration(message=msg)])

    def send_email(to: str) -> str:
        \"\"\"Send an email.\"\"\"
        return f"sent to {to}"

    graph = create_agent(
        Model(),
        tools=[send_email],
        middleware=[HumanInTheLoopMiddleware(interrupt_on={
            "send_email": {"allowed_decisions": ALLOWED},
        })],
    )
    """
)


def _agent(allowed):
    pytest.importorskip("ag_ui_langgraph")
    from langgraph.checkpoint.memory import MemorySaver

    from langstage_cli.agui_stream import build_session_agent

    ns: dict = {"ALLOWED": allowed}
    exec(_AGENT, ns)
    return build_session_agent(ns["graph"].copy(update={"checkpointer": MemorySaver()}))


async def test_the_issue_agent_never_shows_reject_and_approve_runs_the_tool(tty, monkeypatch):
    agent = _agent(["approve", "edit"])
    shown = _pick(monkeypatch, "Approve all actions")
    buf = io.StringIO()
    with redirect_stdout(buf):
        _elapsed, had_error = await cli.run_single_turn_agui(agent, "email them", "t-197", True)
    out = buf.getvalue()
    assert had_error is False, out
    assert "done: sent to a@example.org" in out, out
    assert all("Reject all actions" not in menu for menu in shown)


async def test_no_interactive_stops_paused_when_approve_is_not_allowed(monkeypatch, capsys):
    agent = _agent(["edit", "reject"])
    with pytest.raises(SystemExit) as exc:
        await cli.run_single_turn_agui(agent, "email them", "t-197-ni", False)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "does not allow approve (allowed: edit, reject)" in err
    assert "ValueError" not in err
    # Nothing was sent: the thread is still paused on the same approval.
    assert await cli._pending_interrupt(agent, "t-197-ni")


async def test_no_interactive_still_auto_approves_when_allowed(monkeypatch):
    agent = _agent(["approve", "edit"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        _elapsed, had_error = await cli.run_single_turn_agui(agent, "email them", "t-197-ok", False)
    assert had_error is False
    assert "done: sent to a@example.org" in buf.getvalue()
