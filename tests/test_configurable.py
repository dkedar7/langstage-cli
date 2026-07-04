"""The langstage.toml [configurable] table reaches the graph (gh #57).

`/config` and `--show-config` advertise that `[configurable]` seeds LangGraph's
`RunnableConfig.configurable`, but the AG-UI streaming path used to forward only
`thread_id` — every other key was silently dropped. The session agent now carries
the resolved configurable, so a node's `config["configurable"]` sees it.
"""

from click.testing import CliRunner

from langstage_cli.cli import main

_CFG_AGENT = (
    "from langgraph.graph import StateGraph, START, END\n"
    "from langgraph.graph.message import MessagesState\n"
    "from langchain_core.messages import AIMessage\n"
    "def respond(state, config):\n"
    "    c = (config or {}).get('configurable', {})\n"
    "    return {'messages': [AIMessage(content='custom_key=' + str(c.get('custom_key')))]}\n"
    "g = StateGraph(MessagesState)\n"
    "g.add_node('respond', respond)\n"
    "g.add_edge(START, 'respond')\n"
    "g.add_edge('respond', END)\n"
    "graph = g.compile()\n"
)


def test_configurable_toml_reaches_the_graph(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "langstage.toml").write_text('[configurable]\ncustom_key = "from-toml"\n')
    agent = tmp_path / "cfg_agent.py"
    agent.write_text(_CFG_AGENT)

    r = CliRunner().invoke(main, ["-a", f"{agent}:graph", "hi"])
    assert r.exit_code == 0, r.output
    # The node saw the [configurable] value, not None.
    assert "custom_key=from-toml" in r.output, r.output
