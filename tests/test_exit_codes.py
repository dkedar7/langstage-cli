"""The LangStage family exit codes (langstage-core ADR 0007).

0 success / 1 failure / 2 paused on a HITL interrupt / 64 usage error. click (like
argparse) exits 2 on a usage error, which collides with "paused", so the command
overrides it to 64.
"""

import textwrap

import pytest
from click.testing import CliRunner

from langstage_cli import exit_codes
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


def _run(args, tmp_path, monkeypatch, **kw):
    monkeypatch.chdir(tmp_path)
    return CliRunner().invoke(main, args, **kw)


def test_constants_match_the_family_scheme():
    assert (
        exit_codes.EXIT_OK,
        exit_codes.EXIT_FAIL,
        exit_codes.EXIT_PAUSED,
        exit_codes.EXIT_USAGE,
    ) == (0, 1, 2, 64)


# ---- 64: usage errors -----------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["--bogus-flag"],  # unknown option (click: 2)
        ["--stream-mode", "nope", "hi"],  # bad choice (click: 2)
        ["-f", "does-not-exist.md"],  # Path(exists=True) (click: 2)
        ["--demo", "-a", "x.py:g", "hi"],  # conflicting flags (was 1)
        ["--continue", "--resume", "abc", "hi"],  # conflicting flags (was 1)
        ["-f", "p.md", "hi"],  # MESSAGE and -f together (was 1)
        [""],  # empty MESSAGE argument (was 1)
    ],
)
def test_usage_errors_exit_64(args, tmp_path, monkeypatch):
    (tmp_path / "p.md").write_text("hello", encoding="utf-8")
    r = _run(args, tmp_path, monkeypatch)
    assert r.exit_code == 64, (r.exit_code, r.output)


def test_help_and_version_still_exit_0(tmp_path, monkeypatch):
    assert _run(["--help"], tmp_path, monkeypatch).exit_code == 0
    assert _run(["--version"], tmp_path, monkeypatch).exit_code == 0


# ---- 2: paused on a HITL interrupt --------------------------------------------------


def test_interrupt_that_needs_a_human_exits_2(tmp_path, monkeypatch):
    # Interactive single-shot with no terminal to answer the approval: the run is fine
    # but paused on input (was 1).
    (tmp_path / "hitl_agent.py").write_text(_HITL_AGENT)
    r = _run(["-a", "hitl_agent.py:graph", "please act"], tmp_path, monkeypatch)
    assert r.exit_code == 2, r.output
    assert "stdin is not a terminal" in r.stderr


def test_no_interactive_auto_approve_completes_0(tmp_path, monkeypatch):
    (tmp_path / "hitl_agent.py").write_text(_HITL_AGENT)
    r = _run(["-a", "hitl_agent.py:graph", "--no-interactive", "please act"], tmp_path, monkeypatch)
    assert r.exit_code == 0, r.output


# ---- 1: failure / 0: success ----------------------------------------------------------


def test_no_agent_is_1(tmp_path, monkeypatch):
    monkeypatch.delenv("LANGSTAGE_AGENT_SPEC", raising=False)
    monkeypatch.delenv("DEEPAGENT_AGENT_SPEC", raising=False)
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(tmp_path / "no-global"))
    monkeypatch.setattr("langstage_cli.cli._default_agent_path", lambda: None)
    r = _run(["hi"], tmp_path, monkeypatch)
    assert r.exit_code == 1, r.output


def test_load_error_is_1(tmp_path, monkeypatch):
    r = _run(["-a", "nope_missing.py:graph", "hi"], tmp_path, monkeypatch)
    assert r.exit_code == 1, r.output


def test_verify_ok_0(tmp_path, monkeypatch):
    assert _run(["--demo", "--verify"], tmp_path, monkeypatch).exit_code == 0


def test_single_shot_ok_0(tmp_path, monkeypatch):
    assert _run(["--demo", "--no-interactive", "hi"], tmp_path, monkeypatch).exit_code == 0


def test_init_refusal_is_1(tmp_path, monkeypatch):
    (tmp_path / "my_agent.py").write_text("x = 1\n")
    assert _run(["init"], tmp_path, monkeypatch).exit_code == 1


@pytest.mark.parametrize("is_generic, exit_choice", [(False, 3), (True, 1)])
def test_choosing_exit_at_the_approval_menu_is_paused_2(is_generic, exit_choice, monkeypatch):
    # Leaving at the approval menu leaves the turn paused on the interrupt (was 0).
    from langstage_cli import cli as cli_mod

    monkeypatch.setattr(cli_mod, "_is_a_tty", lambda stream: True)
    monkeypatch.setattr(cli_mod, "select_option", lambda options, prompt="": exit_choice)
    with pytest.raises(SystemExit) as exc:
        cli_mod.handle_interrupt_input(1, is_generic=is_generic)
    assert exc.value.code == 2
