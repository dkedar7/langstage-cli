"""gh #188: session ids are printed so they can be told apart and typed back.
gh #189: one Ctrl-C at a typed approval prompt exits 2.

- ``--list-sessions`` and the ambiguous-``--resume`` error cut every id to 8
  characters, so pinned ``[configurable] thread_id``s sharing a prefix
  (``project-alpha`` / ``project-beta``) both showed as ``project-``. A pinned id is
  now shown in full and a CLI-minted UUID by its shortest unique prefix.
- The typed prompts (``TERM=dumb`` menu, free-text reply, custom JSON) block in
  ``input()`` inside the turn's ``asyncio.run``, whose SIGINT handler only schedules a
  cancel on the first press, so that press was swallowed.
"""

import asyncio
import signal
import uuid
from pathlib import Path

import pytest
from click.testing import CliRunner

from langstage_cli import cli, sessions
from langstage_cli.cli import main
from langstage_cli.exit_codes import EXIT_PAUSED


def _ws(tmp_path, monkeypatch) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(tmp_path / "empty_home"))
    monkeypatch.chdir(ws)
    return ws.resolve()


# --- gh #188 ---


def test_display_id_keeps_pinned_names_whole():
    assert sessions.display_id("project-alpha", ["project-beta"]) == "project-alpha"
    assert sessions.display_id("x") == "x"


def test_display_id_shortens_minted_uuids_to_a_unique_prefix():
    a = "abcdef12-0000-4000-8000-000000000001"
    b = "abcdef12-3000-4000-8000-000000000002"
    assert sessions.display_id(a) == "abcdef12"
    assert sessions.display_id(a, [a]) == "abcdef12"  # itself is not a rival
    assert sessions.display_id(a, [a, b]) == "abcdef12-0"
    assert sessions.display_id(b, [a, b]) == "abcdef12-3"


def test_list_sessions_shows_pinned_ids_in_full(tmp_path, monkeypatch):
    ws = _ws(tmp_path, monkeypatch)
    sessions.touch_session(ws, "project-alpha", first_message="alpha msg")
    sessions.touch_session(ws, "project-beta", first_message="beta msg")
    minted = str(uuid.uuid4())
    sessions.touch_session(ws, minted, first_message="minted msg")
    r = CliRunner().invoke(main, ["--demo", "--list-sessions"])
    assert r.exit_code == 0, r.output
    assert "project-alpha" in r.output and "project-beta" in r.output, r.output
    assert minted[:8] in r.output and minted not in r.output, r.output


def test_ambiguous_resume_lists_ids_resume_accepts(tmp_path, monkeypatch):
    ws = _ws(tmp_path, monkeypatch)
    sessions.touch_session(ws, "project-alpha", first_message="one")
    sessions.touch_session(ws, "project-beta", first_message="two")
    r = CliRunner().invoke(main, ["--demo", "--resume", "project-", "go"])
    assert r.exit_code == 1, r.output
    assert "project-alpha" in r.output and "project-beta" in r.output, r.output


# --- gh #189 ---


def _ctrl_c_then_answer(answer: str):
    """An ``input()`` that gets one Ctrl-C mid-read, and would answer if it survived."""

    def fake_input(prompt=""):
        signal.raise_signal(signal.SIGINT)
        return answer

    return fake_input


async def _in_loop(fn, *args):
    return fn(*args)


@pytest.mark.parametrize(
    "call",
    [
        lambda: cli._select_option_typed(["Approve", "Reject"], "Proceed?"),
        lambda: cli._approval_input(),
    ],
    ids=["dumb-term-menu", "reply-prompt"],
)
def test_one_ctrl_c_inside_asyncio_run_exits_paused(monkeypatch, call):
    monkeypatch.setattr("builtins.input", _ctrl_c_then_answer("1"))
    before = signal.getsignal(signal.SIGINT)
    with pytest.raises(SystemExit) as e:
        asyncio.run(_in_loop(call))
    assert e.value.code == EXIT_PAUSED
    assert signal.getsignal(signal.SIGINT) is before  # handler restored after the read


def test_prompt_line_restores_the_loops_handler(monkeypatch):
    seen = {}

    def fake_input(prompt=""):
        seen["during"] = signal.getsignal(signal.SIGINT)
        return "ok"

    monkeypatch.setattr("builtins.input", fake_input)

    async def body():
        outer = signal.getsignal(signal.SIGINT)
        assert cli._prompt_line("> ") == "ok"
        return outer, signal.getsignal(signal.SIGINT)

    outer, after = asyncio.run(body())
    assert seen["during"] is signal.default_int_handler
    assert after is outer
