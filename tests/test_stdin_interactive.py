"""Which stdin counts as interactive, including Windows mintty / Git Bash (gh #127 follow-up).

Piped stdin is one single-shot message (gh #127). On Windows, mintty / Git Bash hands the
process a *pipe* with an MSYS pty name (``isatty()`` is False), so without a check an
interactive ``langstage-cli`` there would wait for Ctrl-D and send everything as one
message. ``NUL`` is the reverse case: ``isatty()`` is True, but nobody is typing. A real
pty can't run in CI, so the classifier is tested with mocked handle names, plus a real
subprocess check that an actual pipe and ``NUL``/``/dev/null`` are not interactive.
"""

import subprocess
import sys

import pytest

from langstage_cli import cli


class _Stream:
    def __init__(self, tty: bool):
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.mark.parametrize(
    "name,expected",
    [
        ("\\msys-dd50a72ab4668b33-pty0-from-master", True),
        ("\\msys-1888ae32e00d56aa-pty12-to-master", True),
        ("\\cygwin-e022582115c10879-pty3-from-master", True),
        ("\\Device\\NamedPipe\\msys-dd50a72ab4668b33-pty0-from-master", True),
        # Real pipes and files are not a pty.
        ("\\msys-dd50a72ab4668b33-pipe-0x1F", False),
        ("\\1888ae32e00d56aa-24412-pipe-nt-0x2", False),  # Git Bash `echo hi |`
        ("\\Device\\NamedPipe\\Win32Pipes.00001234.00000002", False),
        ("\\Users\\me\\prompt.txt", False),
        ("\\msys-XYZ-pty0-from-master", False),  # not hex
        ("", False),
    ],
)
def test_msys_pty_name_classifier(name, expected):
    assert cli._is_msys_pty_name(name) is expected


def _on_windows(monkeypatch, *, name=None, console=None):
    monkeypatch.setattr(cli, "IS_WINDOWS", True)
    monkeypatch.setattr(cli, "_win_handle_name", lambda stream: name)
    monkeypatch.setattr(cli, "_win_is_console", lambda stream: console)


def test_windows_mintty_pty_pipe_is_interactive(monkeypatch):
    _on_windows(monkeypatch, name="\\msys-dd50a72ab4668b33-pty0-from-master")
    assert cli._stdin_is_interactive(_Stream(tty=False)) is True


def test_windows_real_pipe_is_not_interactive(monkeypatch):
    _on_windows(monkeypatch, name="\\Device\\NamedPipe\\Win32Pipes.00001234.00000002")
    assert cli._stdin_is_interactive(_Stream(tty=False)) is False


def test_windows_unknowable_pipe_name_is_not_interactive(monkeypatch):
    _on_windows(monkeypatch, name=None)
    assert cli._stdin_is_interactive(_Stream(tty=False)) is False


def test_windows_console_is_interactive(monkeypatch):
    _on_windows(monkeypatch, console=True)
    assert cli._stdin_is_interactive(_Stream(tty=True)) is True


def test_windows_nul_is_not_interactive(monkeypatch):
    # isatty() is True for NUL, but GetConsoleMode fails on it.
    _on_windows(monkeypatch, console=False)
    assert cli._stdin_is_interactive(_Stream(tty=True)) is False


def test_windows_console_check_unavailable_keeps_isatty(monkeypatch):
    _on_windows(monkeypatch, console=None)
    assert cli._stdin_is_interactive(_Stream(tty=True)) is True


@pytest.mark.parametrize("tty", [True, False])
def test_posix_is_plain_isatty(monkeypatch, tty):
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    assert cli._stdin_is_interactive(_Stream(tty=tty)) is tty


_PROBE = "from langstage_cli.cli import _stdin_is_interactive; print(_stdin_is_interactive())"


def test_real_pipe_is_not_interactive():
    out = subprocess.run(
        [sys.executable, "-c", _PROBE], input=b"hi\n", capture_output=True, check=True
    )
    assert out.stdout.strip() == b"False", out


def test_devnull_is_not_interactive():
    # On Windows this is NUL, which isatty() calls a terminal.
    out = subprocess.run(
        [sys.executable, "-c", _PROBE], stdin=subprocess.DEVNULL, capture_output=True, check=True
    )
    assert out.stdout.strip() == b"False", out
