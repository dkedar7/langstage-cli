"""Regressions for gh #47 (error exit code) and gh #43 (multi-node run-on).

#47 — a runtime error inside the agent used to exit 0, so a single-shot / piped
caller couldn't tell a failed run from a success. run_single_turn_agui now reports
whether the turn errored so main() can exit non-zero.

#43 — non-verbose streaming printed one `⏺` marker per turn and appended every
chunk, so two nodes' messages ran together on one line. It now starts a fresh
marker on a node change (the node is carried on each chunk since langstage-core 1.0.4).
"""

import io
from contextlib import redirect_stdout

import pytest

pytest.importorskip("ag_ui_langgraph")
pytest.importorskip("fastapi")

from langstage_cli.agui_stream import build_session_agent  # noqa: E402
from langstage_cli.cli import print_chunk, run_single_turn_agui  # noqa: E402


async def test_turn_reports_error_frame_for_nonzero_exit(monkeypatch):
    # gh #47: an error FRAME (e.g. a RunErrorEvent — a recursion-limit failure) is
    # displayed but the run still "completes", so before the fix the CLI exited 0
    # and a single-shot caller couldn't tell a failed run from a success. (A raised
    # exception is separately caught by main() -> exit 1; this is the framed case.)
    async def fake_stream(agent, message, thread_id, resume=None):
        yield {"status": "streaming", "chunk": "partial output", "node": "agent"}
        yield {"status": "error", "error": "Recursion limit of 25 reached"}
        yield {"status": "complete"}

    import langstage_cli.agui_stream as agui_mod

    monkeypatch.setattr(agui_mod, "agui_stream_updates", fake_stream)
    _elapsed, had_error = await run_single_turn_agui(object(), "hi", "t-err", interactive=False)
    assert had_error is True


async def test_successful_turn_reports_no_error():
    from langstage_core import load_agent_spec

    agent = build_session_agent(load_agent_spec("langstage_core.demo.stub:graph"))
    _elapsed, had_error = await run_single_turn_agui(agent, "hi", "t-ok", interactive=False)
    assert had_error is False


def test_print_chunk_breaks_on_node_change_nonverbose():
    # gh #43: two nodes' output must not render as one run-on line.
    print_chunk._streaming_text = False
    print_chunk._streaming_node = None
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_chunk({"status": "streaming", "chunk": "first out", "node": "first"})
        print_chunk({"status": "streaming", "chunk": "second out", "node": "second"})
    out = buf.getvalue()
    assert "first out" in out and "second out" in out
    assert "first outsecond out" not in out  # the two nodes are separated, not a run-on
    assert out.count("⏺") == 2  # a fresh marker per node


def test_print_chunk_same_node_stays_on_one_marker():
    # A token-streamed reply from ONE node keeps a single marker (gh #34 not undone).
    print_chunk._streaming_text = False
    print_chunk._streaming_node = None
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_chunk({"status": "streaming", "chunk": "hel", "node": "agent"})
        print_chunk({"status": "streaming", "chunk": "lo", "node": "agent"})
    out = buf.getvalue()
    assert out.count("⏺") == 1
