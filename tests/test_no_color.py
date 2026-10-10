"""``NO_COLOR`` turns color off on a live terminal too (gh #172).

https://no-color.org: when ``NO_COLOR`` is set to a non-empty value (any value), a CLI
adds no ANSI color. ``main()`` only stripped color on the quiet / non-TTY path, so a
terminal session still got the colored header box, spinner and prompt. The TTY tests
below force that branch the way the rest of the suite does (``_is_a_tty`` -> True);
the pty test runs the real process on a real terminal (POSIX only).
"""

import os
import re
import subprocess
import sys
import time

import pytest
from click.testing import CliRunner

from langstage_cli import cli


def _tty_run(tmp_path, monkeypatch, no_color):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_is_a_tty", lambda stream: True)
    if no_color is not None:
        monkeypatch.setenv("NO_COLOR", no_color)
    r = CliRunner().invoke(cli.main, ["--demo", "hi"])
    assert r.exit_code == 0, r.output
    assert "You said: hi" in r.output, r.output
    # The interactive chrome is still there: this is the terminal path, not quiet mode.
    assert "Loaded" in r.output and "╭" in r.output, r.output
    return r.output


@pytest.mark.parametrize("value", ["1", "true", "0"])
def test_no_color_strips_every_escape_on_a_terminal(tmp_path, monkeypatch, value):
    # Any non-empty value counts, "0" included.
    out = _tty_run(tmp_path, monkeypatch, value)
    assert "\x1b[" not in out, repr(out)


def test_empty_no_color_keeps_color(tmp_path, monkeypatch):
    # An empty NO_COLOR is "not set", per the convention.
    assert "\x1b[" in _tty_run(tmp_path, monkeypatch, "")


def test_terminal_without_no_color_keeps_color(tmp_path, monkeypatch):
    assert "\x1b[" in _tty_run(tmp_path, monkeypatch, None)


def test_spinner_clears_its_line_without_an_escape_when_color_is_off(capsys):
    cli._disable_ansi()
    spinner = cli.Spinner("Thinking")
    spinner.start()
    time.sleep(0.1)  # let it draw a frame
    spinner.stop()
    out = capsys.readouterr().out
    assert "Thinking" in out
    assert "\x1b[" not in out, repr(out)
    # The last frame is overwritten with spaces and the cursor returned to column 0.
    assert re.search(r"\r {%d,}\r$" % len("⠋ Thinking... 0s"), out), repr(out)


def _run_on_pty(tmp_path, no_color):
    import pty
    import select

    env = {k: v for k, v in os.environ.items() if k != "NO_COLOR"}
    env["LANGSTAGE_CLI_SESSIONS_DIR"] = str(tmp_path / "_sessions")
    if no_color is not None:
        env["NO_COLOR"] = no_color
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, "-c", "from langstage_cli.cli import main; main()", "--demo", "hi"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=tmp_path,
        env=env,
        close_fds=True,
    )
    os.close(slave)
    out = b""
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
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
        os.close(master)
    return proc.returncode, out.decode("utf-8", errors="replace")


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")
def test_no_color_on_a_real_terminal(tmp_path):
    code, colored = _run_on_pty(tmp_path, None)
    assert code == 0, colored
    assert "You said: hi" in colored and "\x1b[" in colored, repr(colored)

    code, plain = _run_on_pty(tmp_path, "1")
    assert code == 0, plain
    assert "You said: hi" in plain and "╭" in plain, repr(plain)
    # No color and no line-erase. A private terminal-mode toggle a terminal library might
    # write (``ESC[?1034h``) is not output styling, so it doesn't count.
    assert not re.search(r"\x1b\[(?!\?)", plain), repr(plain)
