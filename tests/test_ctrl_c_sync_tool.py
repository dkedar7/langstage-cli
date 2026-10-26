"""Ctrl-C ends a turn while a sync tool is still running (gh #192).

LangGraph runs ``def`` nodes and tools on the event loop's default executor. A stock
thread pool is joined when ``asyncio.run`` closes and again at interpreter exit, so after
Ctrl-C the process waited for the tool, which could be forever. The turn loop's executor
now uses daemon threads that nothing joins.

Ctrl-C is simulated with ``_thread.interrupt_main(SIGINT)``, fired from inside the tool,
which runs Python's SIGINT handling exactly as a keypress does, on POSIX and Windows.
"""

import os
import subprocess
import sys
import textwrap
import time

import pytest

pytest.importorskip("ag_ui_langgraph")

_SLOW = textwrap.dedent(
    '''
    import _thread, signal, threading, time
    from pathlib import Path
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import MessagesState
    from langgraph.prebuilt import ToolNode

    @tool
    def run_job(seconds: int) -> str:
        """Run a slow job."""
        Path("started").write_text(repr(time.time()))
        threading.Timer(0.5, lambda: _thread.interrupt_main(signal.SIGINT)).start()
        time.sleep(seconds)
        return "finished"

    def plan(state):
        call = {"name": "run_job", "args": {"seconds": 60}, "id": "c1"}
        return {"messages": [AIMessage(content="", tool_calls=[call])]}

    g = StateGraph(MessagesState)
    g.add_node("plan", plan)
    g.add_node("tools", ToolNode([run_job]))
    g.add_edge(START, "plan")
    g.add_edge("plan", "tools")
    g.add_edge("tools", END)
    graph = g.compile()
    '''
)


@pytest.mark.parametrize("persist", [True, False], ids=["persist", "no-persist"])
def test_ctrl_c_during_a_sync_tool_exits_without_waiting_for_it(tmp_path, persist):
    (tmp_path / "slow_192.py").write_text(_SLOW, encoding="utf-8")
    args = [sys.executable, "-m", "langstage_cli.cli", "-a", "slow_192.py:graph", "go"]
    if not persist:
        args.insert(-1, "--no-persist")
    env = {
        **os.environ,
        "LANGSTAGE_CLI_SESSIONS_DIR": str(tmp_path / "sessions"),
        "LANGSTAGE_CONFIG_HOME": str(tmp_path / "home"),
    }
    p = subprocess.run(
        args, cwd=tmp_path, env=env, input="", capture_output=True, text=True, timeout=120
    )
    ended = time.time()
    started = float((tmp_path / "started").read_text())
    assert p.returncode == 1, (p.stdout, p.stderr)
    assert "Aborted!" in p.stderr
    # The tool sleeps 60s and the press comes 0.5s in; before the fix the exit waited ~60s.
    assert ended - started < 20, (ended - started, p.stderr)
