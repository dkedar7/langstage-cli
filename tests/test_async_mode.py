"""--async-mode/--sync-mode and --agui are deprecated and inert (gh #88).

CHANGELOG 0.6.0 (ADR 0003) said `--agui`/`--async`/`--stream-mode` were
accepted-and-ignored "for one release". `--stream-mode` was duly retired in #62;
`--async-mode`/`--sync-mode` and `--agui` were still shipping 20 patch releases
later — still inert, and still advertised as functional in `--help`, the README,
`--show-config`, `/status` ("Mode: async"), and `/config`. Since ADR 0003 collapsed
every turn onto the single async AG-UI path there is no second behavior left for
either flag to select: `--sync-mode` and `--async-mode` produced byte-identical
output, and `use_agui` was never read at all.

They now follow #62's posture exactly: hidden from `--help`, omitted from
`--show-config` and `/status`, no longer resolved from `[ui] async_mode`, and
accepted-and-ignored on the CLI (so existing invocations don't hard-error) with a
one-line deprecation notice.
"""

import re

from click.testing import CliRunner

from langstage_cli.cli import cmd_status, main


def test_async_mode_flag_is_accepted_and_ignored_with_a_notice():
    # An existing `--async-mode` invocation must not hard-error; it is accepted,
    # ignored, and prints a one-line deprecation notice.
    r = CliRunner().invoke(main, ["--demo", "--no-interactive", "--async-mode", "hi"])
    assert r.exit_code == 0, r.output
    assert "--async-mode/--sync-mode is deprecated" in r.output


def test_sync_mode_flag_is_accepted_and_ignored_with_a_notice():
    # The off-spelling of the same flag gets the same treatment.
    r = CliRunner().invoke(main, ["--demo", "--no-interactive", "--sync-mode", "hi"])
    assert r.exit_code == 0, r.output
    assert "--async-mode/--sync-mode is deprecated" in r.output


def test_agui_flag_is_accepted_and_ignored_with_a_notice():
    r = CliRunner().invoke(main, ["--demo", "--no-interactive", "--agui", "hi"])
    assert r.exit_code == 0, r.output
    assert "--agui is deprecated" in r.output


def test_no_notice_when_the_dead_flags_are_not_passed():
    # The deprecation notices must be opt-in noise only — a normal run stays clean.
    r = CliRunner().invoke(main, ["--demo", "--no-interactive", "hi"])
    assert r.exit_code == 0, r.output
    assert "deprecated" not in r.output.lower()


def test_dead_flags_are_hidden_from_help():
    # The issue's core complaint: the flags were advertised as functional controls.
    r = CliRunner().invoke(main, ["--help"])
    assert r.exit_code == 0, r.output
    for flag in ("--async-mode", "--sync-mode", "--agui"):
        assert flag not in r.output, f"{flag} should no longer be advertised in --help"


def test_async_mode_is_omitted_from_show_config():
    # gh #88: `--show-config` printed `async_mode = True [override]`, presenting a dead
    # knob as an active override. It is no longer part of the diagnostic at all.
    with CliRunner().isolated_filesystem():  # no stray toml
        r = CliRunner().invoke(main, ["--show-config"])
    assert r.exit_code == 0, r.output
    assert "async_mode" not in r.output


def test_async_mode_flag_does_not_reappear_in_show_config_as_an_override():
    # Even when the (accepted-and-ignored) flag is passed alongside --show-config.
    with CliRunner().isolated_filesystem():
        r = CliRunner().invoke(main, ["--async-mode", "--show-config"])
    assert r.exit_code == 0, r.output
    assert not re.search(r"^\s*async_mode\s*=", r.output, re.MULTILINE), r.output


def test_toml_async_mode_is_ignored_not_an_error(tmp_path, monkeypatch):
    # A langstage.toml carrying the retired `[ui] async_mode` key — the README used to
    # ship exactly this example — must keep loading. The key is ignored; the run and
    # the rest of the file are unaffected. A config that suddenly failed to load would
    # be a nastier regression than the dead knob being retired.
    (tmp_path / "langstage.toml").write_text(
        '[ui]\nasync_mode = true\n\n[configurable]\nthread_id = "keeps-working"\n'
    )
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["--demo", "--no-interactive", "hi"])
    assert r.exit_code == 0, r.output
    assert "async_mode" not in r.output
    assert "error" not in r.output.lower()


def test_status_does_not_report_a_streaming_mode(capsys):
    # gh #88: `/status` printed "Mode: async" / "Mode: sync" — a distinction the runtime
    # does not have. The line is gone; the rest of the status block is untouched.
    cmd_status("", {"config": {"configurable": {"thread_id": "abcdef123456"}}, "verbose": True})
    out = capsys.readouterr().out
    assert "Mode:" not in out
    assert "async" not in out
    assert "Agent:" in out and "Verbose:" in out


def test_config_key_lookup_no_longer_knows_async_mode(capsys):
    # `/config async_mode` used to echo the dead value back; it is now an unknown key.
    from langstage_cli.cli import cmd_config

    cmd_config("async_mode", {"config": {}})
    assert "Unknown config key: async_mode" in capsys.readouterr().out
