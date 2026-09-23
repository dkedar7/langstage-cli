"""``--no-persist`` wins over ``--continue`` / ``--resume``: a read-only resume (gh #147).

``--continue`` / ``--resume`` used to force persistence on unconditionally, so an
explicit ``--no-persist`` on the same command line was silently dropped: the turn was
written to the durable store, indexed, and resumable later. Now the explicit flag wins —
the prior session's context is READ (from a throwaway snapshot of the store), but nothing
is written back: no new store, no index entry, no appended turn.
"""

import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

pytest.importorskip("ag_ui_langgraph")
pytest.importorskip("fastapi")
pytest.importorskip("langgraph.checkpoint.sqlite.aio")

from langstage_cli import sessions  # noqa: E402
from langstage_cli.cli import main  # noqa: E402

# Reports how many messages the turn can see, so a test can tell whether an earlier
# turn was (or was not) written to the durable store.
_COUNT_AGENT = textwrap.dedent(
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langchain_core.messages import AIMessage

    def respond(state):
        return {"messages": [AIMessage(content=f"history has {len(state['messages'])} msg(s)")]}

    g = StateGraph(MessagesState)
    g.add_node("respond", respond)
    g.add_edge(START, "respond")
    g.add_edge("respond", END)
    graph = g.compile()
    """
)


def _setup(fs: str, monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(tmp_path / "empty_home"))
    store = tmp_path / "store"
    monkeypatch.setenv("LANGSTAGE_CLI_SESSIONS_DIR", str(store))
    (Path(fs) / "count_agent.py").write_text(_COUNT_AGENT)
    (Path(fs) / "langstage.toml").write_text('[agent]\nspec = "count_agent.py:graph"\n')
    return store


def test_no_persist_continue_on_fresh_workspace_writes_nothing(monkeypatch, tmp_path):
    r = CliRunner()
    with r.isolated_filesystem() as fs:
        store = _setup(fs, monkeypatch, tmp_path)
        res = r.invoke(main, ["--no-persist", "-c", "--no-interactive", "one-off, do not save"])
        assert res.exit_code == 0, res.output
        assert "history has 1 msg(s)" in res.output
        # No store, no index entry: the opt-out was honored.
        assert not store.exists() or not any(store.iterdir()), list(store.iterdir())
        assert sessions.list_sessions(Path(fs).resolve()) == []


def test_no_persist_continue_reads_prior_session_but_does_not_append(monkeypatch, tmp_path):
    r = CliRunner()
    with r.isolated_filesystem() as fs:
        _setup(fs, monkeypatch, tmp_path)
        first = r.invoke(main, ["--no-interactive", "first"])
        assert "history has 1 msg(s)" in first.output, first.output
        before = sessions.list_sessions(Path(fs).resolve())

        # Read-only resume: sees the prior turn (1 human + 1 AI + this human = 3)...
        second = r.invoke(main, ["--no-persist", "-c", "--no-interactive", "second"])
        assert second.exit_code == 0, second.output
        assert "history has 3 msg(s)" in second.output, second.output
        assert "read-only" in second.output

        # ...but wrote nothing back: a later plain resume sees only "first" + itself.
        third = r.invoke(main, ["-c", "--no-interactive", "third"])
        assert "history has 3 msg(s)" in third.output, third.output
        # The index entry for the session wasn't bumped by the read-only turn either.
        after = sessions.list_sessions(Path(fs).resolve())
        assert len(after) == len(before) == 1


def test_no_persist_resume_by_id_is_read_only(monkeypatch, tmp_path):
    r = CliRunner()
    with r.isolated_filesystem() as fs:
        _setup(fs, monkeypatch, tmp_path)
        r.invoke(main, ["--no-interactive", "first"])
        ((tid, _),) = sessions.list_sessions(Path(fs).resolve())

        res = r.invoke(main, ["--no-persist", "--resume", tid[:8], "--no-interactive", "peek"])
        assert res.exit_code == 0, res.output
        assert "history has 3 msg(s)" in res.output, res.output

        later = r.invoke(main, ["--resume", tid, "--no-interactive", "later"])
        assert "history has 3 msg(s)" in later.output, later.output


def test_no_persist_resume_unknown_id_still_errors(monkeypatch, tmp_path):
    r = CliRunner()
    with r.isolated_filesystem() as fs:
        _setup(fs, monkeypatch, tmp_path)
        res = r.invoke(main, ["--no-persist", "--resume", "nope", "--no-interactive", "hi"])
        assert res.exit_code != 0
        assert "no session matching" in res.output


def test_env_persist_off_is_still_overridden_by_continue(monkeypatch, tmp_path):
    # Only the explicit CLI flag outranks --continue (both are CLI args); a lower-
    # precedence LANGSTAGE_PERSIST=0 / [session] persist=false keeps the documented
    # "--continue / --resume imply persistence" behavior.
    r = CliRunner()
    with r.isolated_filesystem() as fs:
        _setup(fs, monkeypatch, tmp_path)
        r.invoke(main, ["--no-interactive", "first"])
        monkeypatch.setenv("LANGSTAGE_PERSIST", "0")
        r.invoke(main, ["-c", "--no-interactive", "second"])
        monkeypatch.delenv("LANGSTAGE_PERSIST")
        third = r.invoke(main, ["-c", "--no-interactive", "third"])
        assert "history has 5 msg(s)" in third.output, third.output
