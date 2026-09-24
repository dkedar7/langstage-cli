"""A present-but-malformed ``langstage.toml`` is reported as MALFORMED (gh #140).

``--show-config`` used to say ``TOML: no langstage.toml ... found`` for a file that
exists but fails to parse — while the same command's stderr said it had ignored that
very file. The diagnostic body is core's ``describe()``, which since langstage-core
1.0.36 names the malformed file and the parse error; the CLI renders it unchanged, in
``--show-config`` and interactive ``/config`` alike.
"""

from pathlib import Path

from click.testing import CliRunner

from langstage_cli.cli import _live_resolved_report, main
from langstage_cli.config import CodeConfig

_BAD = '[agent]\nspec = "my_agent.py:graph\n'  # unterminated string


def _setup(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(tmp_path / "empty_home"))
    monkeypatch.delenv("LANGSTAGE_AGENT_SPEC", raising=False)
    monkeypatch.chdir(tmp_path)
    bad = tmp_path / "langstage.toml"
    bad.write_text(_BAD, encoding="utf-8")
    return bad


def test_show_config_names_the_malformed_file(tmp_path, monkeypatch):
    bad = _setup(tmp_path, monkeypatch)
    r = CliRunner().invoke(main, ["--show-config"])
    assert r.exit_code == 0, r.output
    assert "MALFORMED" in r.stdout, r.stdout
    assert str(bad) in r.stdout, r.stdout
    # The contradiction is gone: the body no longer claims no file exists.
    assert "no langstage.toml" not in r.stdout, r.stdout


def test_interactive_config_names_the_malformed_file(tmp_path, monkeypatch):
    bad = _setup(tmp_path, monkeypatch)
    cfg = CodeConfig.resolve(toml_start=tmp_path)
    report = _live_resolved_report({"_resolved_config": cfg}, {})
    assert "MALFORMED" in report, report
    assert str(bad) in report, report
    assert "no langstage.toml" not in report, report
