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
