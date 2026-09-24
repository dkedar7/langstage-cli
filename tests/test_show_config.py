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


def test_show_config_and_slash_config_render_the_same_diagnostic(
    tmp_path, monkeypatch, repl_via_stdin
):
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


def test_show_config_attributes_a_global_only_value_to_the_global_file(tmp_path, monkeypatch):
    """gh #101: a value set ONLY in the global ~/.langstage/config.toml must be attributed
    to the GLOBAL file in --show-config, not to the project langstage.toml.

    Before the fix every toml-sourced value was stamped with the LAST file read (the
    project file when both are merged), so ``workspace_root`` — set only globally — was
    mislabelled ``[toml (langstage.toml)]`` and sent a debugging user to the wrong file.
    """
    home = tmp_path / "home"
    home.mkdir()
    # workspace.root ONLY in the global config; agent.spec ONLY in the project config.
    (home / "config.toml").write_text('[workspace]\nroot = "/tmp/GLOBAL_ONLY_DIR"\n')
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "langstage.toml").write_text('[agent]\nspec = "my_agent.py:graph"\n')
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(home))
    monkeypatch.chdir(proj)

    r = CliRunner().invoke(main, ["--show-config"])
    assert r.exit_code == 0, r.output
    # The global-only value names the GLOBAL file (config.toml), not the project file.
    assert re.search(r"workspace_root\s*=.*\[toml \(config\.toml\)\]", r.output), r.output
    # The project-only value still names the project file.
    assert re.search(r"agent_spec\s*=.*\[toml \(langstage\.toml\)\]", r.output), r.output


def test_show_config_title_env_not_advertised_as_effective():
    """Setting LANGSTAGE_TITLE must not show up as an in-effect value on a surface
    that ignores it (it would mislead the user). (gh #36)"""
    with CliRunner().isolated_filesystem():
        r = CliRunner().invoke(main, ["--show-config"], env={"LANGSTAGE_TITLE": "MyCoolAgent"})
    assert r.exit_code == 0, r.output
    assert "MyCoolAgent" not in r.output
    assert not re.search(r"^\s*title\s*=", r.output, re.MULTILINE)


def test_show_config_attributes_deepagent_spec_to_the_var_actually_set():
    """gh #107: a value set via the oldest legacy alias DEEPAGENT_SPEC must be attributed
    to DEEPAGENT_SPEC — the var the user actually set — not to DEEPAGENT_AGENT_SPEC, the
    canonical-legacy key the resolver copies it onto (a var absent from the environment,
    contradicting --show-config's own stderr deprecation note)."""
    with CliRunner().isolated_filesystem():  # no stray toml / other spec vars
        r = CliRunner().invoke(main, ["--show-config"], env={"DEEPAGENT_SPEC": "my_agent.py:graph"})
    assert r.exit_code == 0, r.output
    # The [source] column names the var the user set...
    assert re.search(r"agent_spec\s*=\s*my_agent\.py:graph\s*\[env:DEEPAGENT_SPEC\]", r.output), (
        r.output
    )
    # ...and never the injected canonical-legacy key that was never in the environment.
    assert "[env:DEEPAGENT_AGENT_SPEC]" not in r.output, r.output


def test_show_config_surfaces_session_persistence_on(tmp_path, monkeypatch):
    """gh #108: --show-config (the scriptable inspector) must surface the persist/session
    config the way interactive /status does — persistence on/off, its source, store path —
    finishing #102 item 3. Default config: persistence ON."""
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["--show-config"])
    assert r.exit_code == 0, r.output
    assert "Session persistence:" in r.output, r.output
    assert re.search(r"persist\s*=\s*True\s*\[default\]", r.output), r.output
    # the source hint, matching describe()'s style
    assert "(env: LANGSTAGE_PERSIST, toml: session.persist)" in r.output, r.output
    # the store path is shown when persistence is on
    assert re.search(r"sessions_store\s*=\s*\S+\.sqlite", r.output), r.output


def test_show_config_surfaces_persist_off_so_the_off_switch_is_verifiable(tmp_path, monkeypatch):
    """gh #108: --no-persist must be visible in --show-config so "did my off-switch work?"
    is answerable from the scriptable surface (it isn't from /status, which only prints
    Persist: when persistence is ON)."""
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["--no-persist", "--show-config"])
    assert r.exit_code == 0, r.output
    assert re.search(r"persist\s*=\s*False\s*\[override\]", r.output), r.output


def test_show_config_persist_source_is_the_toml_key_when_set_there(tmp_path, monkeypatch):
    """gh #108: [session] persist in langstage.toml is attributed to the TOML key."""
    (tmp_path / "langstage.toml").write_text("[session]\npersist = false\n")
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["--show-config"])
    assert r.exit_code == 0, r.output
    assert re.search(r"persist\s*=\s*False\s*\[toml \(session\.persist\)\]", r.output), r.output


def test_show_config_does_not_flag_documented_session_persist_as_unknown(tmp_path, monkeypatch):
    """gh #112: [session] persist is a documented, honored key, so --show-config must NOT
    list it under "unknown TOML keys" — in the exact output that attributes persist to it.

    Regression for the post-#108 gap: core's unknown_toml_keys() flagged session.persist
    because it isn't a CodeConfig dataclass field (persist is resolved out-of-band). The fix
    registers session.persist in CodeConfig._TOML's known set so it stops being false-flagged,
    while genuine typos stay flagged (asserted below)."""
    (tmp_path / "langstage.toml").write_text("[session]\npersist = true\n")
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["--show-config"])
    assert r.exit_code == 0, r.output
    # The documented key must NOT be reported as unknown...
    assert "unknown TOML keys" not in r.output, r.output
    assert "session.persist" not in r.output.split("Session persistence:")[0], r.output
    # ...yet it is still honored and attributed to its TOML source (the two lines agree now).
    assert re.search(r"persist\s*=\s*True\s*\[toml \(session\.persist\)\]", r.output), r.output


def test_show_config_still_flags_a_typod_session_key_as_unknown(tmp_path, monkeypatch):
    """gh #112: the fix must NOT disable the unknown-key detector — a typo'd key under the
    same [session] table (perssist) and a bogus table must still be surfaced as unknown."""
    (tmp_path / "langstage.toml").write_text("[session]\nperssist = true\n[bogus]\nx = 1\n")
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["--show-config"])
    assert r.exit_code == 0, r.output
    assert re.search(r"unknown TOML keys.*session\.perssist", r.output), r.output
    assert re.search(r"unknown TOML keys.*bogus\.x", r.output), r.output
