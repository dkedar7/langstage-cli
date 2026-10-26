"""A mistyped option is never pointed at a hidden, deprecated no-op flag (gh #193)."""

import pytest
from click.testing import CliRunner

from langstage_cli.cli import main

_HIDDEN = ("--agui", "--async-mode", "--sync-mode", "--stream-mode")


@pytest.mark.parametrize("typo", ["--agnet", "--model", "--sync", "--stream", "--asyn"])
def test_suggestions_skip_hidden_flags(typo):
    r = CliRunner().invoke(main, [typo, "x", "hi"])
    assert r.exit_code == 64, r.output
    assert typo in r.output
    for flag in _HIDDEN:
        assert flag not in r.output, r.output


def test_a_near_miss_still_suggests_the_documented_option():
    r = CliRunner().invoke(main, ["--agnet", "x", "hi"])
    assert "--agent" in r.output


def test_hidden_flags_still_parse():
    r = CliRunner().invoke(main, ["--agui", "--version"])
    assert r.exit_code == 0, r.output
