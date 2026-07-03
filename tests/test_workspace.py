"""cli applies the resolved workspace via core.apply_workspace (ADR 0005).

The acceptance behavior all the workspace bugs were about: with a workspace
configured, a turn whose agent writes a *relative* file must land that file in the
workspace, not the launch cwd.
"""

import os
from pathlib import Path

from click.testing import CliRunner

from langstage_cli.cli import main

_WRITER_AGENT = (
    "from pathlib import Path\n"
    "from langgraph.graph import StateGraph, START, END, MessagesState\n"
    "from langchain_core.messages import AIMessage\n"
    "def node(s):\n"
    "    Path('marker.txt').write_text('hi')\n"
    "    return {'messages': [AIMessage(content='wrote marker')]}\n"
    "b = StateGraph(MessagesState)\n"
    "b.add_node('n', node)\n"
    "b.add_edge(START, 'n')\n"
    "b.add_edge('n', END)\n"
    "graph = b.compile()\n"
)


def test_configured_workspace_is_where_the_agent_writes(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    agent = tmp_path / "writer.py"
    agent.write_text(_WRITER_AGENT)
    # Workspace comes from the env (cli has no --workspace flag); track both names
    # so monkeypatch restores them (apply_workspace also sets the legacy one).
    monkeypatch.setenv("LANGSTAGE_WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv("DEEPAGENT_WORKSPACE_ROOT", "")

    origin = Path.cwd()
    try:
        r = CliRunner().invoke(main, ["-a", f"{agent}:graph", "go"])
    finally:
        os.chdir(origin)  # cli chdir'd into the workspace; restore for other tests

    assert r.exit_code == 0, r.output
    # The relative write landed in the configured workspace, not the launch cwd.
    assert (ws / "marker.txt").read_text() == "hi"
    assert not (origin / "marker.txt").exists()
