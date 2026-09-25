"""Advertised-not-honored fixes: each diagnostic now matches what the run does.

gh #120: a CompiledGraph whose state has no ``messages`` channel produced a blank turn
with exit 0 and no diagnostic, while ``--verify`` failed the same turn.

gh #124: ``--resume <prefix>`` said "no session matching" when the prefix matched two
or more sessions.

gh #128: an agent compiled with its own checkpointer never uses the CLI's store, yet
``--show-config`` / ``/config`` reported the store path as if it would be written, and
``--list-sessions`` listed the run as an ordinary resumable session.

gh #129: with a spec that names its graph inline (``app.py:prod``), ``-g`` /
``[agent] graph_name`` was dropped silently and ``--show-config`` reported it anyway.

gh #148: a misplaced spec key in ``langstage.toml`` failed with a bare "No agent
specified" that pointed at the env var instead of the ignored key.
"""

import re
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

pytest.importorskip("ag_ui_langgraph")
pytest.importorskip("langgraph.checkpoint.sqlite.aio")

from langstage_cli import cli, sessions  # noqa: E402
from langstage_cli.cli import main  # noqa: E402

_CUSTOM_STATE = textwrap.dedent(
    """
    from typing_extensions import TypedDict
    from langgraph.graph import StateGraph, START, END

    class S(TypedDict, total=False):
        query: str
        answer: str

    def work(state):
        return {"answer": "processed"}

    g = StateGraph(S)
    g.add_node("work", work)
    g.add_edge(START, "work")
    g.add_edge("work", END)
    graph = g.compile()
    """
)

_SILENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState

    def noop(state):
        return {}

    g = StateGraph(MessagesState)
    g.add_node("noop", noop)
    g.add_edge(START, "noop")
    g.add_edge("noop", END)
    graph = g.compile()
    """
)

_OWN_CHECKPOINTER = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langgraph.checkpoint.memory import MemorySaver
    from langchain_core.messages import AIMessage

    def respond(state):
        return {"messages": [AIMessage(content=f"sees {len(state['messages'])}")]}

    g = StateGraph(MessagesState)
    g.add_node("respond", respond)
    g.add_edge(START, "respond")
    g.add_edge("respond", END)
    graph = g.compile(checkpointer=MemorySaver())
    """
)

_TWO_GRAPHS = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langchain_core.messages import AIMessage

    def _mk(label):
        def r(s):
            return {"messages": [AIMessage(content=f"[{label}]")]}
        g = StateGraph(MessagesState)
        g.add_node("r", r)
        g.add_edge(START, "r")
        g.add_edge("r", END)
        return g.compile()

    prod = _mk("PROD")
    staging = _mk("STAGING")
    """
)


def _ws(tmp_path, monkeypatch, files: dict) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    for name, text in files.items():
        (ws / name).write_text(text, encoding="utf-8")
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(tmp_path / "empty_home"))
    monkeypatch.chdir(ws)
    return ws.resolve()


# --- gh #120 ---


def test_non_messages_graph_renders_its_final_state(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch, {"custom_state_120.py": _CUSTOM_STATE})
    r = CliRunner().invoke(main, ["-a", "custom_state_120.py:graph", "hello"])
    assert r.exit_code == 0, r.output
    assert '"answer": "processed"' in r.stdout, r.output
    assert "no message" in r.stderr, r.stderr  # says why it shows state, not a reply


def test_turn_with_no_output_at_all_fails_clearly(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch, {"silent_120.py": _SILENT})
    r = CliRunner().invoke(main, ["-a", "silent_120.py:graph", "hello"])
    assert r.exit_code == 1, r.output  # agrees with --verify: a 0-content turn failed
    assert "produced no output" in r.stderr, r.stderr


def test_non_messages_graph_renders_state_without_persistence(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch, {"custom_state_120b.py": _CUSTOM_STATE})
    r = CliRunner().invoke(main, ["--no-persist", "-a", "custom_state_120b.py:graph", "hi"])
    assert r.exit_code == 0, r.output
    assert '"answer": "processed"' in r.stdout, r.output


# --- gh #124 ---


def test_ambiguous_resume_prefix_says_ambiguous_and_lists_matches(tmp_path, monkeypatch):
    ws = _ws(tmp_path, monkeypatch, {})
    sessions.touch_session(ws, "abc11111-0000", first_message="one")
    sessions.touch_session(ws, "abc22222-0000", first_message="two")
    r = CliRunner().invoke(main, ["--demo", "--resume", "abc", "go"])
    assert r.exit_code == 1, r.output
    assert "ambiguous" in r.output and "matches 2 sessions" in r.output, r.output
    assert "abc11111" in r.output and "abc22222" in r.output, r.output
    assert "no session matching" not in r.output, r.output


def test_unmatched_resume_prefix_still_says_no_match(tmp_path, monkeypatch):
    ws = _ws(tmp_path, monkeypatch, {})
    sessions.touch_session(ws, "abc11111-0000", first_message="one")
    r = CliRunner().invoke(main, ["--demo", "--resume", "zzz", "go"])
    assert r.exit_code == 1, r.output
    assert "no session matching 'zzz'" in r.output, r.output


def test_match_threads_returns_every_candidate(tmp_path):
    sessions.touch_session(tmp_path, "abc1", first_message="x")
    sessions.touch_session(tmp_path, "abc2", first_message="y")
    assert sorted(sessions.match_threads(tmp_path, "abc")) == ["abc1", "abc2"]
    assert sessions.match_threads(tmp_path, "abc1") == ["abc1"]
    assert sessions.resolve_thread(tmp_path, "abc") is None


# --- gh #128 ---


def test_own_checkpointer_is_reported_honestly(tmp_path, monkeypatch, repl_via_stdin):
    ws = _ws(tmp_path, monkeypatch, {"own_cp_128.py": _OWN_CHECKPOINTER})
    r = CliRunner().invoke(main, ["-a", "own_cp_128.py:graph"], input="/config\n/quit\n")
    assert r.exit_code == 0, r.output
    # /config: the CLI store is not used, and says why.
    assert re.search(r"sessions_store\s*=\s*not used", r.output), r.output
    assert "own checkpointer" in r.output, r.output
    # A run-time warning: MemorySaver keeps nothing across runs.
    r = CliRunner().invoke(main, ["-a", "own_cp_128.py:graph", "first"])
    assert r.exit_code == 0, r.output
    assert "in-memory checkpointer" in r.stderr, r.stderr
    assert not list(sessions.sessions_dir().glob("*.sqlite")), "no CLI store is written"
    # --list-sessions marks the session instead of listing it as ordinary.
    r = CliRunner().invoke(main, ["--list-sessions"])
    assert "first" in r.output and "agent's own checkpointer" in r.output, r.output
    # Resuming it says the CLI has no stored history for it.
    tid = sessions.most_recent_thread(ws)
    assert sessions.load_index(ws)[tid].get("checkpointer") == "agent"


def test_show_config_says_when_the_store_is_used(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch, {})
    r = CliRunner().invoke(main, ["-a", "whatever.py:graph", "--show-config"])
    assert r.exit_code == 0, r.output
    line = next(ln for ln in r.output.splitlines() if "sessions_store" in ln)
    assert "unused if the agent has its own checkpointer" in line, line


def test_cli_store_session_is_not_marked(tmp_path, monkeypatch):
    ws = _ws(tmp_path, monkeypatch, {"plain_128.py": _TWO_GRAPHS})
    r = CliRunner().invoke(main, ["-a", "plain_128.py:prod", "hi"])
    assert r.exit_code == 0, r.output
    assert "in-memory checkpointer" not in r.output
    tid = sessions.most_recent_thread(ws)
    assert "checkpointer" not in sessions.load_index(ws)[tid]
    r = CliRunner().invoke(main, ["--list-sessions"])
    assert "own checkpointer" not in r.output, r.output


# --- gh #129 ---


def test_show_config_reports_the_inline_graph_name(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch, {})
    r = CliRunner().invoke(main, ["-a", "app.py:prod", "-g", "staging", "--show-config"])
    assert r.exit_code == 0, r.output
    line = next(ln for ln in r.output.splitlines() if ln.strip().startswith("graph_name"))
    assert re.search(r"graph_name\s*=\s*prod\s", line), line
    assert "staging" in line and "ignored" in line, line


def test_show_config_graph_name_from_toml_with_inline_spec(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch, {"langstage.toml": '[agent]\ngraph_name = "staging"\n'})
    monkeypatch.setenv("LANGSTAGE_AGENT_SPEC", "app.py:prod")
    r = CliRunner().invoke(main, ["--show-config"])
    line = next(ln for ln in r.output.splitlines() if ln.strip().startswith("graph_name"))
    assert re.search(r"graph_name\s*=\s*prod\s", line), line


def test_show_config_graph_name_unchanged_without_inline_name(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch, {})
    r = CliRunner().invoke(main, ["-a", "app.py", "-g", "staging", "--show-config"])
    line = next(ln for ln in r.output.splitlines() if ln.strip().startswith("graph_name"))
    assert re.search(r"graph_name\s*=\s*staging\s*\[override\]", line), line
    assert "ignored" not in line


def test_dropped_graph_name_flag_is_noted_at_run_time(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch, {"two_129.py": _TWO_GRAPHS})
    r = CliRunner().invoke(main, ["-a", "two_129.py:prod", "-g", "staging", "hi"])
    assert r.exit_code == 0, r.output
    assert "[PROD]" in r.stdout
    assert "graph_name 'staging' ignored" in r.stderr, r.stderr


# --- gh #148 ---


@pytest.mark.parametrize(
    "toml,key",
    [
        ('spec = "my_agent.py:graph"\n', "spec"),
        ('[agents]\nspec = "my_agent.py:graph"\n', "agents.spec"),
        ('[agent]\nspce = "my_agent.py:graph"\n', "agent.spce"),
    ],
    ids=["top-level", "plural-table", "typo"],
)
def test_no_agent_error_names_the_ignored_toml_key(tmp_path, monkeypatch, toml, key):
    _ws(tmp_path, monkeypatch, {"langstage.toml": toml})
    monkeypatch.setattr(cli, "_default_agent_path", lambda: None)
    for args in (["hi"], ["--verify"]):
        r = CliRunner().invoke(main, args)
        assert r.exit_code == 1, r.output
        assert "No agent specified" in r.output, r.output
        assert "langstage.toml" in r.output and key in r.output, r.output
        assert "[agent]" in r.output, r.output  # points at the right table


def test_no_agent_error_without_toml_is_unchanged(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch, {})
    monkeypatch.setattr(cli, "_default_agent_path", lambda: None)
    r = CliRunner().invoke(main, ["hi"])
    assert r.exit_code == 1, r.output
    assert "No agent specified" in r.output
    assert "unknown" not in r.output
