"""Regression tests for #20: `--show-config` must reflect CLI flags.

Before the fix, the --show-config branch resolved config WITHOUT the CLI
overrides, so flags showed as `[default]` and the reported source could
contradict the real run.
"""
import re

from click.testing import CliRunner

from langstage_cli.cli import main


def test_show_config_reflects_cli_flags():
    with CliRunner().isolated_filesystem():  # no stray toml
        r = CliRunner().invoke(
            main, ["-a", "fromcli.py:graph", "-v", "--stream-mode", "values", "--show-config"]
        )
    assert r.exit_code == 0, r.output
    # CLI-set values appear with the [override] source, not [default].
    assert re.search(r"agent_spec\s*=\s*fromcli\.py:graph\s*\[override\]", r.output), r.output
    assert re.search(r"stream_mode\s*=\s*values\s*\[override\]", r.output), r.output
    assert re.search(r"verbose\s*=\s*True\s*\[override\]", r.output), r.output


def test_show_config_cli_flag_beats_env():
    with CliRunner().isolated_filesystem():
        r = CliRunner().invoke(
            main,
            ["-a", "fromcli.py:graph", "--show-config"],
            env={"LANGSTAGE_AGENT_SPEC": "fromenv.py:graph"},
        )
    assert r.exit_code == 0, r.output
    # the CLI flag wins (matches what a real run does), not the env var
    assert re.search(r"agent_spec\s*=\s*fromcli\.py:graph\s*\[override\]", r.output), r.output
    assert "fromenv.py:graph" not in r.output.split("agent_spec")[1].split("\n")[0]


def test_show_config_without_flags_still_reports_env():
    with CliRunner().isolated_filesystem():
        r = CliRunner().invoke(
            main, ["--show-config"], env={"LANGSTAGE_AGENT_SPEC": "fromenv.py:graph"}
        )
    assert r.exit_code == 0, r.output
    # no flags → env is correctly the winning source (no regression)
    assert re.search(r"agent_spec\s*=\s*fromenv\.py:graph\s*\[env:", r.output), r.output
