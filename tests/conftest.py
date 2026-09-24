"""Shared test fixtures.

``main()`` toggles module-level process state for scriptable single-shot output
(gh #53): it sets ``cli._QUIET`` and blanks the ANSI color constants when a
single-shot run is piped (CliRunner's stdout is not a TTY). That is correct for a
real one-shot process, but in a pytest process it would leak into the next test —
a later ``print_chunk`` call would see ``_QUIET=True`` and skip its markers. This
autouse fixture snapshots and restores that mutable module state around every test
so invocations stay isolated.
"""

import pytest

from langstage_cli import cli as _cli

# The ANSI constants main() may blank via _disable_ansi().
_ANSI_NAMES = (
    "RESET",
    "BOLD",
    "DIM",
    "ITALIC",
    "UNDERLINE",
    "BLUE",
    "CYAN",
    "GREEN",
    "YELLOW",
    "RED",
    "MAGENTA",
    "WHITE",
    "GRAY",
    "BRIGHT_CYAN",
    "BRIGHT_BLUE",
    "BRIGHT_GREEN",
    "BRIGHT_YELLOW",
)


@pytest.fixture(autouse=True)
def _reset_cli_global_state():
    saved_quiet = _cli._QUIET
    saved_ansi = {name: getattr(_cli, name) for name in _ANSI_NAMES}
    # Start each test from the interactive default (decorations + color on).
    _cli._QUIET = False
    yield
    _cli._QUIET = saved_quiet
    for name, value in saved_ansi.items():
        setattr(_cli, name, value)


@pytest.fixture(autouse=True)
def _hermetic_sessions(tmp_path, monkeypatch):
    """Redirect session persistence (gh #102) to a per-test temp dir.

    Persistence is ON by default, so ANY test that runs a real turn through ``main()``
    would otherwise write a session store under the real ``~/.langstage/sessions``.
    Pointing ``LANGSTAGE_CLI_SESSIONS_DIR`` at a temp dir keeps the whole suite hermetic
    and off the developer's home — without touching config resolution (it's a dedicated
    env var, distinct from ``LANGSTAGE_CONFIG_HOME``).
    """
    monkeypatch.setenv("LANGSTAGE_CLI_SESSIONS_DIR", str(tmp_path / "_sessions"))


@pytest.fixture(autouse=True)
def _isolate_published_workspace():
    """Undo ``apply_workspace``'s env publishing between tests.

    ``apply_workspace`` (ADR 0005) exports the resolved workspace as
    ``LANGSTAGE_WORKSPACE_ROOT`` / ``DEEPAGENT_WORKSPACE_ROOT`` in ``os.environ`` — a
    real product behavior, but it bypasses monkeypatch, so a test that runs a turn leaks
    a workspace path (often a since-deleted temp dir) into the next test, corrupting its
    config resolution / chdir. Snapshot and restore those two vars around every test.
    """
    import os

    names = ("LANGSTAGE_WORKSPACE_ROOT", "DEEPAGENT_WORKSPACE_ROOT")
    saved = {name: os.environ.get(name) for name in names}
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.fixture
def repl_via_stdin(monkeypatch):
    """Drive the interactive REPL with CliRunner's ``input=``.

    Piped stdin is read as ONE single-shot message (gh #127), and CliRunner's stdin is
    never a terminal, so a test that types REPL lines (``/config``, ``/quit``, ...) opts
    back into the REPL by making the "read piped stdin" step report a terminal.
    """
    monkeypatch.setattr(_cli, "_read_piped_stdin", lambda: None)
