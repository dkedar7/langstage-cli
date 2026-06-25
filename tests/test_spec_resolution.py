"""Relative agent-spec resolution vs the workspace-root chdir (gh #30).

The CLI chdirs into LANGSTAGE_WORKSPACE_ROOT before loading the agent, so a
relative `-a my_agent.py:graph` must be anchored to the invocation cwd up front
— otherwise it's looked up under the workspace root and fails "file not found"
for a file sitting in the user's current directory.
"""

from click.testing import CliRunner

from langstage_cli.cli import _absolutize_file_spec, main

_AGENT_SRC = (
    "from langgraph.graph import StateGraph, START, END, MessagesState\n"
    "from langchain_core.messages import AIMessage\n"
    "def respond(state):\n"
    "    return {'messages': [AIMessage(content='hi from my_agent')]}\n"
    "g = StateGraph(MessagesState); g.add_node('respond', respond)\n"
    "g.add_edge(START, 'respond'); g.add_edge('respond', END)\n"
    "graph = g.compile()\n"
)


def test_absolutize_resolves_relative_py(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = _absolutize_file_spec("my_agent.py:graph")
    assert out == f"{(tmp_path / 'my_agent.py').resolve()}:graph"


def test_absolutize_bare_py_keeps_no_suffix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = _absolutize_file_spec("my_agent.py")
    assert out == str((tmp_path / "my_agent.py").resolve())
    assert not out.endswith(":")


def test_absolutize_leaves_module_specs_untouched():
    assert _absolutize_file_spec("pkg.mod:graph") == "pkg.mod:graph"
    assert _absolutize_file_spec("langstage_hermes.agent:graph") == "langstage_hermes.agent:graph"


def test_absolutize_idempotent_on_absolute(tmp_path):
    abs_spec = f"{(tmp_path / 'a.py').resolve()}:graph"
    assert _absolutize_file_spec(abs_spec) == abs_spec


def test_relative_spec_with_workspace_root_resolves_against_cwd(tmp_path, monkeypatch):
    # The exact issue scenario: relative spec in cwd, workspace root elsewhere.
    proj = tmp_path / "proj"
    proj.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (proj / "my_agent.py").write_text(_AGENT_SRC)
    monkeypatch.chdir(proj)

    r = CliRunner().invoke(
        main,
        ["-a", "my_agent.py:graph", "--no-interactive", "hi"],
        env={"LANGSTAGE_WORKSPACE_ROOT": str(elsewhere)},
    )
    assert r.exit_code == 0, r.output
    assert "hi from my_agent" in r.output
    assert "not found" not in r.output.lower()
