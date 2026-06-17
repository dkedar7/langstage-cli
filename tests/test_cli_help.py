"""Regression tests for #22: user-facing strings use the canonical LANGSTAGE_*
names, and a `--version` flag exists.
"""

from click.testing import CliRunner

from langstage_cli.cli import main


def test_version_flag_exists():
    r = CliRunner().invoke(main, ["--version"])
    assert r.exit_code == 0, r.output
    assert "langstage-cli" in r.output
    assert "0.0.0" not in r.output  # reports the real version, not the fallback


def test_help_leads_with_canonical_names():
    r = CliRunner().invoke(main, ["--help"])
    assert r.exit_code == 0, r.output
    out = r.output
    # canonical LANGSTAGE_* / langstage.toml are present...
    assert "LANGSTAGE_AGENT_SPEC" in out
    assert "LANGSTAGE_WORKSPACE_ROOT" in out
    assert "LANGSTAGE_STREAM_MODE" in out
    assert "langstage.toml" in out
    # ...and the legacy names are no longer presented as the only config vocab
    assert "DEEPAGENT_AGENT_SPEC" not in out
