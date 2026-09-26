"""The LangStage family's command-line exit codes.

Same scheme on every surface (langstage-core ADR 0007,
https://github.com/dkedar7/langstage-core/blob/main/docs/adr/0007-family-exit-codes.md):

- 0  success
- 1  failure: no agent / not configured, load or import error, turn error,
     ``--verify`` failed, ``init`` refused
- 2  paused on a human-in-the-loop interrupt (the run is fine but needs input)
- 64 usage error: bad or conflicting command-line arguments

click exits 2 on a usage error, which collides with "paused", so :class:`Command`
re-codes click usage errors to 64. Defined locally (not imported from
``langstage_core.cli``) so this package keeps its existing core floor.
"""

from __future__ import annotations

from typing import Any, List

import click

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_PAUSED = 2
EXIT_USAGE = 64


class Command(click.Command):
    """A ``click.Command`` whose usage errors (unknown option, bad choice, a missing
    ``-f`` file, ...) exit 64 instead of click's 2."""

    def parse_args(self, ctx: click.Context, args: List[str]) -> List[str]:
        try:
            return super().parse_args(ctx, args)
        except click.UsageError as exc:
            exc.exit_code = EXIT_USAGE
            raise

    def invoke(self, ctx: click.Context) -> Any:
        # A UsageError raised from inside the callback gets the same code.
        try:
            return super().invoke(ctx)
        except click.UsageError as exc:
            exc.exit_code = EXIT_USAGE
            raise
