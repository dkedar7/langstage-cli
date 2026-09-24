"""Agent-spec loading through langstage-core 1.0.36 (gh #145, #141, #149, #136).

The CLI delegates spec import semantics to core's ``load_agent_spec`` and passes it the
right ``base_dir`` / ``stdout_to_stderr``. Each test is the issue's own repro, run
end-to-end through ``main()``.

Module names are unique per test: ``sys.modules`` / ``sys.path`` are process-wide, and a
name cached by one test must not make another pass by accident.
"""

import textwrap

from click.testing import CliRunner

from langstage_cli.cli import main

_REPLY_GRAPH = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langchain_core.messages import AIMessage

    def respond(state):
        return {{"messages": [AIMessage(content={reply})]}}

    g = StateGraph(MessagesState)
    g.add_node("respond", respond)
    g.add_edge(START, "respond")
    g.add_edge("respond", END)
    {name} = g.compile()
    """
)


def _graph_src(reply_expr: str, name: str = "graph", header: str = "") -> str:
    return header + _REPLY_GRAPH.format(reply=reply_expr, name=name)


# ---- gh #145: a file-path agent can import its sibling module --------------------------


def test_file_spec_agent_imports_its_sibling_module(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sib145_tools.py").write_text(
        "def make_reply(msg):\n    return f'[from tools.py] {msg}'\n", encoding="utf-8"
    )
    (tmp_path / "agent145.py").write_text(
        _graph_src(
            'make_reply(state["messages"][-1].content)',
            header="from sib145_tools import make_reply\n",
        ),
        encoding="utf-8",
    )
    r = CliRunner().invoke(main, ["-a", "agent145.py:graph", "--no-interactive", "hi"])
    assert r.exit_code == 0, r.output
    assert "[from tools.py] hi" in r.stdout, r.output
    assert "ModuleNotFoundError" not in r.output, r.output


def test_file_spec_sibling_import_works_from_another_directory(tmp_path, monkeypatch):
    # The agent's OWN directory goes on sys.path, not the cwd: run from elsewhere.
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "sib145b_tools.py").write_text("REPLY = 'sibling ok'\n", encoding="utf-8")
    (proj / "agent145b.py").write_text(
        _graph_src("REPLY", header="from sib145b_tools import REPLY\n"), encoding="utf-8"
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    r = CliRunner().invoke(main, ["-a", str(proj / "agent145b.py") + ":graph", "hi"])
    assert r.exit_code == 0, r.output
    assert "sibling ok" in r.stdout, r.output


# ---- gh #141: a project-local dotted module spec resolves ------------------------------


def _make_pkg(root, pkg: str, reply: str) -> None:
    (root / pkg).mkdir()
    (root / pkg / "__init__.py").write_text("", encoding="utf-8")
    (root / pkg / "agents.py").write_text(_graph_src(repr(reply), name="chatbot"), encoding="utf-8")


def test_dotted_spec_for_a_project_local_package(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _make_pkg(tmp_path, "mypkg141", "You said: hi")
    r = CliRunner().invoke(main, ["-a", "mypkg141.agents:chatbot", "hi"])
    assert r.exit_code == 0, r.output
    assert "You said: hi" in r.stdout, r.output
    assert "ModuleNotFoundError" not in r.output, r.output


def test_dotted_spec_via_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _make_pkg(tmp_path, "mypkg141env", "env ok")
    monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", "mypkg141env.agents:chatbot")
    r = CliRunner().invoke(main, ["hi"])
    assert r.exit_code == 0, r.output
    assert "env ok" in r.stdout, r.output


def test_dotted_toml_spec_resolves_against_the_toml_dir_from_a_subdirectory(tmp_path, monkeypatch):
    # The toml names a project package; running from a subdirectory must still find it
    # (base_dir = the toml's directory, not the cwd).
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(tmp_path / "empty_home"))
    _make_pkg(tmp_path, "mypkg141toml", "toml ok")
    (tmp_path / "langstage.toml").write_text(
        '[agent]\nspec = "mypkg141toml.agents:chatbot"\n', encoding="utf-8"
    )
    sub = tmp_path / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    r = CliRunner().invoke(main, ["hi"])
    assert r.exit_code == 0, r.output
    assert "toml ok" in r.stdout, r.output


def test_dotted_flag_spec_resolves_from_the_launch_dir_not_the_workspace(tmp_path, monkeypatch):
    # The CLI chdirs into an explicit workspace BEFORE loading; a flag spec still means
    # "where I typed the command" (gh #30), so the dotted fallback uses the launch cwd.
    monkeypatch.chdir(tmp_path)
    _make_pkg(tmp_path, "mypkg141ws", "launch dir ok")
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("LANGSTAGE_WORKSPACE_ROOT", str(ws))
    r = CliRunner().invoke(main, ["-a", "mypkg141ws.agents:chatbot", "hi"])
    assert r.exit_code == 0, r.output
    assert "launch dir ok" in r.stdout, r.output


# ---- gh #149: a str attribute is rejected, not re-read as a second spec -----------------


def test_str_attribute_is_a_clean_error_naming_the_users_spec(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "my_agent149.py").write_text('graph = "openai:gpt-4o-mini"\n', encoding="utf-8")
    r = CliRunner().invoke(main, ["-a", "my_agent149.py:graph", "hi"])
    assert r.exit_code == 1, r.output
    assert "TypeError" in r.output, r.output
    assert "resolved to a str" in r.output, r.output
    assert "my_agent149.py:graph" in r.output, r.output
    # The value was never imported as another spec: no phantom module blamed.
    assert "has no attribute 'gpt-4o-mini'" not in r.output, r.output
    assert "No module named" not in r.output, r.output


def test_colonless_str_attribute_is_not_parsed_as_a_spec(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "my_agent149b.py").write_text(
        'graph = "please use my real agent"\n', encoding="utf-8"
    )
    r = CliRunner().invoke(main, ["-a", "my_agent149b.py:graph", "hi"])
    assert r.exit_code == 1, r.output
    assert "resolved to a str" in r.output, r.output
    assert "Invalid agent spec" not in r.output, r.output


# ---- gh #136: an agent's import-time stdout never reaches the captured reply ------------

_NOISY = (
    'import sys\nprint("IMPORT-TIME-STDOUT-BANNER")\nprint("WARN: something", file=sys.stderr)\n'
)


def test_import_time_stdout_goes_to_stderr_on_the_piped_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "noisy136.py").write_text(
        _graph_src('"CLEAN-REPLY"', header=_NOISY), encoding="utf-8"
    )
    # CliRunner's stdout is not a TTY: the auto-quiet, "safe to capture" path.
    r = CliRunner().invoke(main, ["-a", "noisy136.py:graph", "hi"])
    assert r.exit_code == 0, r.output
    assert r.stdout == "CLEAN-REPLY\n", repr(r.stdout)
    # The banner isn't lost, it's a diagnostic now.
    assert "IMPORT-TIME-STDOUT-BANNER" in r.stderr, r.stderr


def test_import_time_stdout_goes_to_stderr_with_quiet_and_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "noisy136b.py").write_text(
        _graph_src('"CLEAN-REPLY"', header=_NOISY), encoding="utf-8"
    )
    (tmp_path / "p.txt").write_text("hi\n", encoding="utf-8")
    for args in (
        ["-q", "-a", "noisy136b.py:graph", "hi"],
        ["-a", "noisy136b.py:graph", "-f", "p.txt"],
    ):
        r = CliRunner().invoke(main, args)
        assert r.exit_code == 0, (args, r.output)
        assert r.stdout == "CLEAN-REPLY\n", (args, repr(r.stdout))
