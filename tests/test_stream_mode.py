"""--stream-mode validation (gh #-dogfood).

`values` was advertised but unsupported by the CLI's render path; passing it
crashed with a fatal interpreter-shutdown error (a ValueError surfaced while the
spinner daemon thread held stdout). Now: the flag is a Choice {updates,messages},
and the resolved value (incl. LANGSTAGE_STREAM_MODE, which bypasses the flag) is
validated up front with a clean error.
"""

from click.testing import CliRunner

from langstage_cli.cli import main


def test_stream_mode_values_flag_rejected_cleanly():
    r = CliRunner().invoke(main, ["--demo", "--no-interactive", "--stream-mode", "values", "hi"])
    assert r.exit_code != 0
    assert "values" in r.output
    assert "Fatal" not in r.output  # no interpreter-shutdown crash


def test_stream_mode_messages_flag_accepted():
    # A valid mode must still be accepted by the Choice (parses, doesn't error on the flag).
    r = CliRunner().invoke(main, ["--stream-mode", "messages", "--show-config"])
    assert r.exit_code == 0, r.output
    assert "messages" in r.output


def test_stream_mode_values_via_env_rejected():
    r = CliRunner().invoke(
        main,
        ["--demo", "--no-interactive", "hi"],
        env={"LANGSTAGE_STREAM_MODE": "values"},
    )
    assert r.exit_code == 2, r.output
    assert "unsupported stream mode" in r.output.lower()
