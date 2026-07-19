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
        r = CliRunner().invoke(main, ["-a", "fromcli.py:graph", "-v", "--show-config"])
    assert r.exit_code == 0, r.output
    # CLI-set values appear with the [override] source, not [default].
    assert re.search(r"agent_spec\s*=\s*fromcli\.py:graph\s*\[override\]", r.output), r.output
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


def test_show_config_and_slash_config_render_the_same_diagnostic(tmp_path, monkeypatch):
    # Consolidation guard (gh #64/#66 class): `--show-config` and interactive `/config`
    # both render the ONE describe() diagnostic (fields + sources + the [configurable]
    # table), so they can't drift. Lock it — every resolved line from --show-config must
    # also appear in /config (both driven with the same --demo flag so the agent matches).
    (tmp_path / "langstage.toml").write_text(
        '[configurable]\nmodel_name = "gpt-4o-mini"\ntemperature = "0.2"\n'
    )
    monkeypatch.chdir(tmp_path)
    show = CliRunner().invoke(main, ["--show-config", "--demo"]).output
    slash = CliRunner().invoke(main, ["--demo"], input="/config\n/quit\n").output
    slash_lines = {ln.strip() for ln in slash.splitlines()}
    for ln in show.splitlines():
        s = ln.strip()
        if not s or s.startswith("⏺"):  # skip blanks / status (⏺) notices
            continue
        assert s in slash_lines, f"/config is missing a --show-config line: {s!r}"
    # the [configurable] table (the #66 seam) is present in both.
    assert "model_name: gpt-4o-mini" in show and "model_name: gpt-4o-mini" in slash


def test_show_config_includes_the_configurable_table(tmp_path, monkeypatch):
    # gh #66: the [configurable] table is honored (reaches the graph) and shown by
    # interactive /config, but --show-config omitted it — the two views disagreed.
    (tmp_path / "langstage.toml").write_text(
        '[agent]\nspec = "agent.py:graph"\n[configurable]\nmodel_name = "gpt-4o-mini"\ntemperature = "0.2"\n'
    )
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["--show-config"])
    assert r.exit_code == 0, r.output
    assert "LangGraph configurable:" in r.output
    assert "model_name: gpt-4o-mini" in r.output
    assert "temperature: 0.2" in r.output


def test_show_config_omits_server_only_keys():
    """The terminal CLI starts no server and titles the header from the graph
    name, so host/port/debug/title are inert and must not be advertised (gh #36).
    stream_mode is likewise omitted — it's a deprecated no-op (gh #62) — and so is
    async_mode, inert since ADR 0003 collapsed everything onto one path (gh #88)."""
    with CliRunner().isolated_filesystem():
        r = CliRunner().invoke(main, ["--show-config"])
    assert r.exit_code == 0, r.output
    for key in ("host", "port", "debug", "title", "stream_mode", "async_mode"):
        assert not re.search(rf"^\s*{key}\s*=", r.output, re.MULTILINE), f"{key} should be omitted"
    # ...but keys the CLI actually honors are still shown.
    assert re.search(r"^\s*agent_spec\s*=", r.output, re.MULTILINE), r.output


def test_show_config_title_env_not_advertised_as_effective():
    """Setting LANGSTAGE_TITLE must not show up as an in-effect value on a surface
    that ignores it (it would mislead the user). (gh #36)"""
    with CliRunner().isolated_filesystem():
        r = CliRunner().invoke(main, ["--show-config"], env={"LANGSTAGE_TITLE": "MyCoolAgent"})
    assert r.exit_code == 0, r.output
    assert "MyCoolAgent" not in r.output
    assert not re.search(r"^\s*title\s*=", r.output, re.MULTILINE)
