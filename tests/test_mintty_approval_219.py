"""Approvals can be answered in a mintty / Git Bash terminal (gh #219).

On Windows a mintty pty is a pipe with an MSYS pty name. It counts as interactive, so
the REPL runs, but the arrow-key approval menu reads keys with ``msvcrt.getch()``,
which can't read it. Since gh #217 the first approval therefore exited 2. The menu now
falls back to the typed, line-based menu there: a number, or a word that starts one
option's name. ``NUL`` and real pipes still exit 2.
"""

import builtins

import pytest

from langstage_cli import cli


def _mintty(monkeypatch):
    monkeypatch.setattr(cli, "_stdin_drives_menu", lambda stream=None: False)
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda stream=None: True)


def _feed(monkeypatch, *lines):
    it = iter(lines)

    def fake_input(prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr(builtins, "input", fake_input)


def _no_keys(monkeypatch):
    def boom():
        raise AssertionError("the arrow-key menu must not read keys here")

    monkeypatch.setattr(cli, "get_key", boom)


@pytest.mark.parametrize(
    "answer, verb",
    [
        ("1", "approve"),
        ("2", "reject"),
        ("a", "approve"),
        ("approve", "approve"),
        ("R", "reject"),
        ("Reject", "reject"),
        ("", "approve"),
    ],
)
def test_mintty_answers_the_approval_with_a_typed_line(monkeypatch, capsys, answer, verb):
    _mintty(monkeypatch)
    _no_keys(monkeypatch)
    _feed(monkeypatch, answer)
    result = cli.handle_interrupt_input(num_actions=2)
    assert result == {"decisions": [{"type": verb}, {"type": verb}]}
    out = capsys.readouterr().out
    assert "1. Approve all actions" in out
    assert "\033[?25l" not in out  # no cursor escapes from the arrow-key menu


@pytest.mark.parametrize("answer", ["4", "e", "exit", "q", "quit"])
def test_mintty_leaving_the_menu_exits_paused(monkeypatch, answer):
    _mintty(monkeypatch)
    _no_keys(monkeypatch)
    _feed(monkeypatch, answer)
    with pytest.raises(SystemExit) as exc:
        cli.handle_interrupt_input(num_actions=1)
    assert exc.value.code == cli.EXIT_PAUSED


def test_mintty_end_of_input_exits_paused(monkeypatch):
    _mintty(monkeypatch)
    _no_keys(monkeypatch)
    _feed(monkeypatch)  # EOF at once
    with pytest.raises(SystemExit) as exc:
        cli.handle_interrupt_input(num_actions=1)
    assert exc.value.code == cli.EXIT_PAUSED


def test_mintty_unknown_answer_asks_again(monkeypatch, capsys):
    _mintty(monkeypatch)
    _no_keys(monkeypatch)
    _feed(monkeypatch, "maybe", "9", "a")
    assert cli.handle_interrupt_input(num_actions=1) == {"decisions": [{"type": "approve"}]}
    out = capsys.readouterr().out
    assert out.count("or the start of an option's name") == 2


def test_mintty_generic_interrupt_takes_a_typed_answer(monkeypatch):
    _mintty(monkeypatch)
    _no_keys(monkeypatch)
    _feed(monkeypatch, "p", "Alice")
    assert cli.handle_interrupt_input(is_generic=True) == "Alice"


def test_mintty_custom_decision_flow(monkeypatch):
    _mintty(monkeypatch)
    _no_keys(monkeypatch)
    _feed(monkeypatch, "3", '[{"type": "reject", "message": "not now"}]')
    assert cli.handle_interrupt_input(num_actions=1) == {
        "decisions": [{"type": "reject", "message": "not now"}]
    }


def test_mintty_honours_allowed_decisions(monkeypatch, capsys):
    # An approve-only action offers no "Reject all", so "r" matches nothing.
    _mintty(monkeypatch)
    _no_keys(monkeypatch)
    _feed(monkeypatch, "r", "a")
    interrupt = {
        "action_requests": [{"name": "send_email", "args": {}}],
        "review_configs": [{"action_name": "send_email", "allowed_decisions": ["approve"]}],
    }
    assert cli.handle_interrupt_input(num_actions=1, interrupt=interrupt) == {
        "decisions": [{"type": "approve"}]
    }
    assert "Reject all actions" not in capsys.readouterr().out


def test_ambiguous_prefix_asks_again(monkeypatch):
    _feed(monkeypatch, "rej", "2")
    assert cli._select_option_typed(["Reject all", "Reject one"], "Pick") == 1


def test_not_interactive_still_exits_paused(monkeypatch, capsys):
    # NUL or a real pipe: neither drives the menu nor counts as a person (gh #217).
    monkeypatch.setattr(cli, "_stdin_drives_menu", lambda stream=None: False)
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda stream=None: False)
    _no_keys(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        cli.handle_interrupt_input(num_actions=1)
    assert exc.value.code == cli.EXIT_PAUSED
    assert "approval required but stdin is not a terminal" in capsys.readouterr().err


def test_real_console_keeps_the_arrow_menu(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_stdin_drives_menu", lambda stream=None: True)
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda stream=None: True)
    monkeypatch.setattr(cli, "_DUMB_TERM", False)
    monkeypatch.setattr(cli, "get_key", lambda: "enter")
    assert cli.handle_interrupt_input(num_actions=1) == {"decisions": [{"type": "approve"}]}
    assert "Choose 1-" not in capsys.readouterr().out
