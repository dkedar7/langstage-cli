"""Relative paths in a TOML config resolve against that file's directory (gh #132, #133).

langstage-core 1.0.36 resolves a relative ``[agent] spec`` / ``[workspace] root`` against
the directory of the TOML file that defined it, for the project ``langstage.toml`` and
the global ``~/.langstage/config.toml`` alike. The CLI dropped its own spec-only rebase
(gh #116) in favour of that, which also brought ``[workspace] root`` along.
"""

import textwrap
from pathlib import Path

from click.testing import CliRunner

from langstage_cli.cli import main

_CWD_AGENT = textwrap.dedent(
    """
    import os
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langchain_core.messages import AIMessage

    def r(s):
        return {"messages": [AIMessage(content=f"agent cwd = {os.getcwd()}")]}

    g = StateGraph(MessagesState)
    g.add_node("r", r)
    g.add_edge(START, "r")
    g.add_edge("r", END)
    graph = g.compile()
    """
)


def _agent_cwd(stdout: str) -> Path:
    line = next(ln for ln in stdout.splitlines() if ln.startswith("agent cwd = "))
    return Path(line.removeprefix("agent cwd = ")).resolve()


def test_relative_workspace_root_resolves_against_the_toml_dir_from_a_subdir(tmp_path, monkeypatch):
    # gh #132's repro: from project/sub the agent must run in project/workdir, and no
    # stray sub/workdir may be created.
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(tmp_path / "empty_home"))
    project = tmp_path / "project"
    (project / "workdir").mkdir(parents=True)
    (project / "sub").mkdir()
    (project / "my_agent.py").write_text(_CWD_AGENT, encoding="utf-8")
    (project / "langstage.toml").write_text(
        '[agent]\nspec = "my_agent.py:graph"\n[workspace]\nroot = "workdir"\n',
        encoding="utf-8",
    )

    monkeypatch.chdir(project / "sub")
    r = CliRunner().invoke(main, ["--no-interactive", "hi"])
    assert r.exit_code == 0, r.output
    assert _agent_cwd(r.stdout) == (project / "workdir").resolve(), r.stdout
    assert not (project / "sub" / "workdir").exists()


def test_relative_workspace_root_is_the_same_from_the_project_root(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(tmp_path / "empty_home"))
    project = tmp_path / "project"
    (project / "workdir").mkdir(parents=True)
    (project / "my_agent.py").write_text(_CWD_AGENT, encoding="utf-8")
    (project / "langstage.toml").write_text(
        '[agent]\nspec = "my_agent.py:graph"\n[workspace]\nroot = "workdir"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    r = CliRunner().invoke(main, ["--no-interactive", "hi"])
    assert r.exit_code == 0, r.output
    assert _agent_cwd(r.stdout) == (project / "workdir").resolve(), r.stdout


def test_global_config_relative_spec_resolves_against_the_config_home(tmp_path, monkeypatch):
    # gh #133, resolved by design: one rule for every TOML file. A relative spec in the
    # GLOBAL config means "relative to ~/.langstage/"; the README says to use `~/...` or
    # an absolute path there. Pin the rule so it can't drift silently.
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text('[agent]\nspec = "g_agent133.py:graph"\n', encoding="utf-8")
    (home / "g_agent133.py").write_text(_CWD_AGENT, encoding="utf-8")
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(home))
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)

    r = CliRunner().invoke(main, ["--show-config"])
    assert r.exit_code == 0, r.output
    # --show-config shows the RESOLVED path, so the base directory is visible.
    assert str(home / "g_agent133.py") in r.output, r.output

    r = CliRunner().invoke(main, ["--no-interactive", "hi"])
    assert r.exit_code == 0, r.output
    assert "agent cwd = " in r.stdout, r.output
