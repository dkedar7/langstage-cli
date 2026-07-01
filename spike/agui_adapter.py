"""SPIKE (ADR 0002, cli-first): drive a graph through the in-process AG-UI
stream and yield chunks in the cli's existing ``print_chunk`` contract, so the
renderer is untouched and only the event *source* changes.

Not shipped. This is the cli-first migration probe: prove the terminal output is
reproducible from AG-UI events before wiring a real flag into cli.py.
"""
import importlib.util
import json
import sys
import types
import uuid


def _simulate_agui_2067():
    """Make ``import ag_ui_langgraph`` work without a real web stack.

    ag-ui-langgraph's __init__ eagerly imports its FastAPI endpoint, so importing
    the (FastAPI-free) LangGraphAgent currently drags in fastapi — the bug filed
    upstream as ag-ui-protocol/ag-ui#2067. We never serve, so a tiny stub stands
    in for the one-line upstream lazy-import and proves the terminal path needs
    NO real web stack. (If #2067 merges, delete this shim.)
    """
    if importlib.util.find_spec("fastapi") is not None:
        return  # real fastapi present — fallback path, nothing to stub
    fastapi = types.ModuleType("fastapi")
    fastapi.__stub__ = True  # so the parity runner can tell this from real fastapi
    fastapi.FastAPI = fastapi.HTTPException = fastapi.Request = object
    responses = types.ModuleType("fastapi.responses")
    responses.StreamingResponse = object
    fastapi.responses = responses
    sys.modules.setdefault("fastapi", fastapi)
    sys.modules.setdefault("fastapi.responses", responses)


async def agui_stream_updates(graph, message: str, *, node: str = "agent"):
    """Yield ``print_chunk``-compatible chunk dicts from LangGraphAgent.run().

    Covers the text + tool-call + complete/error surface the demo stub exercises.
    Interrupt/resume is deliberately out of scope here (ADR 0002 gate 2).
    """
    _simulate_agui_2067()
    from ag_ui.core.types import RunAgentInput, UserMessage
    from ag_ui_langgraph import LangGraphAgent

    # The AG-UI adapter calls graph.aget_state() (state + interrupt/resume), which
    # needs a checkpointer — many user graphs (and the demo stub) compile without
    # one. The core's agui.build_agent attaches an InMemorySaver when absent; mirror
    # that here so the in-process path works on any graph. (Spike finding.)
    if getattr(graph, "checkpointer", None) is None:
        from langgraph.checkpoint.memory import InMemorySaver
        graph.checkpointer = InMemorySaver()

    agent = LangGraphAgent(name="cli-spike", graph=graph)
    run_input = RunAgentInput(
        thread_id="spike", run_id=str(uuid.uuid4()), state={},
        messages=[UserMessage(id=str(uuid.uuid4()), role="user", content=message)],
        tools=[], context=[], forwarded_props={},
    )

    streamed_text = False
    tool_buf: dict = {}
    async for ev in agent.run(run_input):
        t = type(ev).__name__
        if t == "TextMessageContentEvent":
            streamed_text = True
            yield {"status": "streaming", "chunk": ev.delta, "node": node}
        elif t == "ToolCallStartEvent":
            tool_buf[ev.tool_call_id] = {"name": ev.tool_call_name, "args": ""}
        elif t == "ToolCallArgsEvent":
            tool_buf.setdefault(ev.tool_call_id, {"name": "tool", "args": ""})["args"] += ev.delta
        elif t == "ToolCallEndEvent":
            tc = tool_buf.pop(ev.tool_call_id, {"name": "tool", "args": ""})
            try:
                args = json.loads(tc["args"]) if tc["args"] else {}
            except json.JSONDecodeError:
                args = {"_raw": tc["args"]}
            yield {"status": "streaming", "tool_calls": [{"name": tc["name"], "args": args}]}
        elif t == "ToolCallResultEvent":
            # AG-UI's dedicated tool-result event (appears with streaming agents).
            yield {"status": "streaming", "tool_result": getattr(ev, "content", "")}
        elif t == "MessagesSnapshotEvent" and not streamed_text:
            # one-shot node (e.g. the keyless echo stub): text arrives as a final
            # snapshot rather than token deltas — recover it so parity holds.
            for m in ev.messages:
                if getattr(m, "role", None) == "assistant" and getattr(m, "content", None):
                    yield {"status": "streaming", "chunk": m.content, "node": node}
        elif t == "RunErrorEvent":
            yield {"status": "error", "error": getattr(ev, "message", "unknown")}
    yield {"status": "complete"}
