"""A custom approval decision is checked before it is sent, and a session wedged by a
refused one recovers (gh #195).

"Provide custom decision (JSON)" sent any valid JSON. When the agent's HITL middleware
refused it (a typo like ``{"type": "aprove"}``, or the whole ``{"decisions": [...]}``
envelope), LangGraph kept that resume value on the checkpoint and replayed it on every
later resume. "Approve all actions" failed with the same error, the menu came back
forever, and ``-c --no-interactive`` on the persisted session exited 1.

Now the decision is checked against the interrupt's allowed decisions (and LangChain's
decision shapes) and refused in place with nothing sent, a pasted envelope is unwrapped,
and a thread already holding a refused answer drops it before the next resume.
"""

import io
import logging
import textwrap
from contextlib import redirect_stdout

import pytest
from click.testing import CliRunner

from langstage_cli import cli
from langstage_cli.cli import main

# LangChain's HumanInTheLoopMiddleware on a keyless model: the first turn calls
# send_email, and once the tool has answered the model says "done".
_HITL_AGENT = textwrap.dedent(
    """
    from langchain.agents import create_agent
    from langchain.agents.middleware import HumanInTheLoopMiddleware
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class Model(BaseChatModel):
        @property
        def _llm_type(self):
            return "fake-195"

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
            "send_email": {"allowed_decisions": ["approve", "reject"]},
        })],
    )
    """
)

# The interrupt frame core emits for that agent.
_FRAME = {
    "action_requests": [{"name": "send_email", "args": {"to": "a@example.org"}}],
    "review_configs": [{"action_name": "send_email", "allowed_decisions": ["approve", "reject"]}],
    "allowed_decisions": ["approve", "reject"],
}


def _typed(monkeypatch, *lines):
    it = iter(lines)
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(it))


def _choices(monkeypatch, *choices):
    it = iter(choices)
    shown = []

    def fake_select(options, prompt=""):
        shown.append(prompt)
        return next(it)

    monkeypatch.setattr(cli, "select_option", fake_select)
    return shown


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(cli, "_is_a_tty", lambda *a, **k: True)


# ---- Checking the decision ----------------------------------------------------------


@pytest.mark.parametrize(
    "value, problem",
    [
        (
            {"type": "aprove"},
            "decision 1 (send_email): type 'aprove' is not allowed; allowed: approve, reject",
        ),
        (
            {"type": "edit", "edited_action": {"name": "send_email", "args": {}}},
            "'edit' is not allowed",
        ),
        (None, 'A decision is a JSON object with a "type"'),
        ({"decisions": [{"kind": "approve"}]}, 'A decision is a JSON object with a "type"'),
        ([{"type": "approve"}] * 3, "Got 3 decisions for 2 pending action(s)"),
        ({"type": "reject", "message": 5}, 'a reject "message" must be text'),
    ],
)
def test_bad_decisions_are_refused(value, problem):
    frame = {**_FRAME, "action_requests": _FRAME["action_requests"] * 2}
    decisions, got = cli._check_custom_decision(value, 2, frame)
    assert decisions == []
    assert problem in got


_FRAME2 = {**_FRAME, "action_requests": _FRAME["action_requests"] * 2}


def test_one_decision_applies_to_every_action():
    decisions, problem = cli._check_custom_decision({"type": "reject", "message": "no"}, 2, _FRAME2)
    assert problem is None
    assert decisions == [{"type": "reject", "message": "no"}] * 2


def test_a_pasted_envelope_is_unwrapped():
    one = {"decisions": [{"type": "approve"}]}
    assert cli._check_custom_decision(one, 2, _FRAME2) == ([{"type": "approve"}] * 2, None)
    each = {"decisions": [{"type": "approve"}, {"type": "reject"}]}
    assert cli._check_custom_decision(each, 2, _FRAME2) == (each["decisions"], None)


def test_legacy_alias_verbs_pass():
    # core rewrites accept -> approve before the middleware sees it.
    assert cli._check_custom_decision({"type": "accept"}, 1, _FRAME)[1] is None


def test_langchain_shapes_are_checked_only_for_its_interrupts():
    frame = {
        **_FRAME,
        "review_configs": [{"action_name": "send_email", "allowed_decisions": ["edit", "respond"]}],
    }
    assert "edited_action" in cli._check_custom_decision({"type": "edit"}, 1, frame)[1]
    assert (
        'respond needs a text "message"'
        in cli._check_custom_decision({"type": "respond"}, 1, frame)[1]
    )
    edit = {"type": "edit", "edited_action": {"name": "send_email", "args": {"to": "b"}}}
    assert cli._check_custom_decision(edit, 1, frame) == ([edit], None)
    # A plain interrupt() payload (no review_configs) reads its own edit shape.
    legacy = {"type": "edit", "args": {"path": "/tmp/hosts"}}
    assert (
        cli._check_custom_decision(
            legacy, 1, {"action_requests": [{"action": "x"}], "review_configs": []}
        )[1]
        is None
    )


def test_allowed_verbs_are_per_tool():
    frame = {
        "action_requests": [{"name": "a", "args": {}}, {"name": "b", "args": {}}],
        "review_configs": [
            {"action_name": "a", "allowed_decisions": ["approve"]},
            {"action_name": "b", "allowed_decisions": ["approve", "reject"]},
        ],
        "allowed_decisions": ["approve", "reject"],
    }
    decisions, problem = cli._check_custom_decision({"type": "reject"}, 2, frame)
    assert "allowed: approve." in problem


def test_menu_reprompts_on_a_refused_decision_and_shows_the_shapes(tty, monkeypatch):
    shown = _choices(monkeypatch, 2)
    _typed(monkeypatch, '{"type":"aprove"}', '{"type":"approve"}')
    buf = io.StringIO()
    with redirect_stdout(buf):
        resume = cli.handle_interrupt_input(1, interrupt=_FRAME)
    out = buf.getvalue()
    assert resume == {"decisions": [{"type": "approve"}]}
    assert "type 'aprove' is not allowed" in out and "Nothing was sent" in out
    assert '{"type": "approve"} | {"type": "reject", "message": "why"}' in out
    assert '"edit"' not in out  # not offered for this tool
    assert len(shown) == 1  # re-prompted in place


# ---- End to end ---------------------------------------------------------------------


async def test_a_typo_is_refused_then_the_corrected_decision_runs_the_tool(tty, monkeypatch):
    pytest.importorskip("ag_ui_langgraph")
    from langgraph.checkpoint.memory import MemorySaver

    from langstage_cli.agui_stream import build_session_agent

    ns: dict = {}
    exec(_HITL_AGENT, ns)
    agent = build_session_agent(ns["graph"].copy(update={"checkpointer": MemorySaver()}))
    _choices(monkeypatch, 2)
    _typed(monkeypatch, '{"type":"aprove"}', '{"decisions":[{"type":"approve"}]}')
    buf = io.StringIO()
    with redirect_stdout(buf):
        _elapsed, had_error = await cli.run_single_turn_agui(agent, "email them", "t-195", True)
    out = buf.getvalue()
    assert had_error is False, out
    assert "done: sent to a@example.org" in out, out


async def test_a_thread_holding_a_refused_answer_takes_the_next_one(monkeypatch, caplog, capsys):
    # A thread wedged by an older CLI: the refused answer is already on the checkpoint.
    pytest.importorskip("ag_ui_langgraph")
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from langstage_cli.agui_stream import build_session_agent

    ns: dict = {}
    exec(_HITL_AGENT, ns)
    graph = ns["graph"].copy(update={"checkpointer": MemorySaver()})
    cfg = {"configurable": {"thread_id": "t-wedged"}}
    await graph.ainvoke({"messages": [("user", "email them")]}, cfg)
    with pytest.raises(ValueError, match="aprove"):
        await graph.ainvoke(Command(resume={"decisions": [{"type": "aprove"}]}), cfg)
    with pytest.raises(ValueError, match="aprove"):  # LangGraph replays the stale answer
        await graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), cfg)

    agent = build_session_agent(graph)
    buf = io.StringIO()
    with caplog.at_level(logging.WARNING), redirect_stdout(buf):
        _elapsed, had_error = await cli.run_single_turn_agui(
            agent, "and now?", "t-wedged", interactive=False
        )
    out = buf.getvalue()
    assert had_error is False, out
    assert "aprove" not in out, out
    assert "done: sent to a@example.org" in out, out
    # Recovered by core (gh #227), so ag-ui never falls back to the deprecated wire.
    logs = caplog.text + capsys.readouterr().err
    assert "forwardedProps.command.resume is deprecated" not in out + logs, out + logs


def test_continue_recovers_a_persisted_session_wedged_by_a_refused_answer(
    tmp_path, monkeypatch, caplog
):
    pytest.importorskip("ag_ui_langgraph")
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "hitl_195.py").write_text(_HITL_AGENT, encoding="utf-8")
    (ws / "langstage.toml").write_text('[agent]\nspec = "hitl_195.py:graph"\n')
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(tmp_path / "empty_home"))
    monkeypatch.chdir(ws)
    runner = CliRunner()

    # An older CLI sent the typo unchecked, then the user left at the menu.
    answers = iter([{"decisions": [{"type": "aprove"}]}])

    def old_menu(*_a, **_k):
        try:
            return next(answers)
        except StopIteration:
            raise SystemExit(2)

    monkeypatch.setattr(cli, "_is_a_tty", lambda *a, **k: True)
    monkeypatch.setattr(cli, "handle_interrupt_input", old_menu)
    # ...and with a core older than 1.0.58, which didn't check it either.
    monkeypatch.setattr("langstage_core.resume.decision_problems", lambda *_a, **_k: [])
    r = runner.invoke(main, ["email them"])
    assert "aprove" in r.output, r.output
    monkeypatch.undo()
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(tmp_path / "empty_home"))
    monkeypatch.chdir(ws)

    # Core 1.0.58's resume path drops the stale answer (core gh #227), without the
    # ag-ui "forwardedProps.command.resume is deprecated" log the cli's own recovery
    # caused by clearing the interrupt before core read it.
    with caplog.at_level(logging.WARNING):
        r = runner.invoke(main, ["-c", "--no-interactive", "x"])
    assert r.exit_code == 0, r.output
    assert "aprove" not in r.output, r.output
    assert "done: sent to a@example.org" in r.output, r.output
    assert "deprecated" not in r.output + caplog.text, r.output + caplog.text
