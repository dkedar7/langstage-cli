"""`--show-config` / `/config` show a bare toml `spec = "file.py"` resolved (gh #213).

The run loads a colon-less spec from a project `langstage.toml` relative to that
toml's directory (gh #116), but the diagnostics printed it raw, so from a
subdirectory they named a file that isn't there. The `file.py:graph` form was
already shown resolved, because core rebases it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from langstage_cli import cli
from langstage_cli.cli import main


def _spec_line(output: str) -> str:
    return next(line for line in output.splitlines() if re.match(r"\s*agent_spec\s*=", line))


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("LANGSTAGE_AGENT_SPEC", "DEEPAGENT_AGENT_SPEC"):
        monkeypatch.delenv(var, raising=False)
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    (proj / "my_agent.py").write_text("graph = None\n", encoding="utf-8")
    monkeypatch.chdir(proj / "sub")
    return proj


def _write_toml(proj: Path, spec: str) -> None:
    (proj / "langstage.toml").write_text(f'[agent]\nspec = "{spec}"\n', encoding="utf-8")


@pytest.mark.parametrize("spec", ["my_agent.py", "my_agent.py:graph"])
def test_show_config_resolves_against_the_toml_dir(project: Path, spec: str) -> None:
    _write_toml(project, spec)
    res = CliRunner().invoke(main, ["--show-config"])
    assert res.exit_code == 0, res.output
    line = _spec_line(res.output)
    assert str(project / "my_agent.py") in line, line
    assert "[toml" in line  # provenance is kept


def test_bare_tilde_spec_is_expanded(
    project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    _write_toml(project, "~/agent.py")
    res = CliRunner().invoke(main, ["--show-config"])
    assert res.exit_code == 0, res.output
    line = _spec_line(res.output)
    assert "~" not in line.split("[")[0], line
    assert str(home / "agent.py") in line, line


def test_env_bare_spec_keeps_its_spelling(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A spec from the environment stays cwd-relative (gh #30) and is shown as typed.
    monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", "my_agent.py")
    res = CliRunner().invoke(main, ["--show-config"])
    assert res.exit_code == 0, res.output
    assert re.search(r"agent_spec\s*=\s*my_agent\.py\s", _spec_line(res.output) + " ")


def test_describe_does_not_mutate_the_config(project: Path) -> None:
    from langstage_cli.config import CodeConfig

    _write_toml(project, "my_agent.py")
    cfg = CodeConfig.resolve(toml_start=Path.cwd())
    report = cli._describe(cfg)
    assert str(project / "my_agent.py") in _spec_line(report)
    assert (
        cfg.agent_spec == "my_agent.py"
    )  # the run's own base-dir handling still sees the raw value
