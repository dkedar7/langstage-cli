"""Experimental in-process AG-UI streaming path for the cli (``--agui``).

Instead of parsing ``graph.stream()`` via ``langgraph-stream-parser``'s event
layer, this drives the agent through the official ``ag-ui-langgraph`` adapter
in-process (no web server) and maps AG-UI events onto the cli's existing
``print_chunk`` chunk contract — so the renderer is unchanged.

This is the first step of ADR 0002 (retire the bespoke event layer, converge on
AG-UI). Text + tool calls/results are at parity with the default path (and the
AG-UI path additionally surfaces tool *results*). Interrupt **display** works;
interrupt **resume** is not yet supported here (ADR 0002 gate 2) — the caller
surfaces a notice and the user re-runs on the default path to approve actions.

Requires the ``agui`` extra::

    pip install "langstage-cli[agui]"
"""

import json
import uuid
from typing import Any, AsyncIterator, Dict

_IMPORT_HINT = 'the --agui path needs the agui extra: pip install "langstage-cli[agui]"'


def ensure_agui_available() -> None:
    """Raise a clean, actionable error if the AG-UI adapter isn't installed."""
    try:
        import ag_ui_langgraph  # noqa: F401
        from langgraph_stream_parser.agui import build_agent  # noqa: F401
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(_IMPORT_HINT) from e


def build_session_agent(graph: Any, *, name: str = "langstage-cli") -> Any:
    """Wrap a compiled graph once per session (checkpointer attached by the core
    bridge), so multi-turn memory persists across turns."""
    ensure_agui_available()
    from langgraph_stream_parser.agui import build_agent

    return build_agent(graph, name=name)


async def agui_stream_updates(
    agent: Any, message: str, thread_id: str
) -> AsyncIterator[Dict[str, Any]]:
    """Drive ``agent.run()`` in-process and yield ``print_chunk``-compatible chunks.

    Maps: TextMessageContent -> text; ToolCall{Start,Args,End} -> tool_calls;
    ToolCallResult -> tool_result; CustomEvent(on_interrupt) -> interrupt;
    RunError -> error; and a one-shot MessagesSnapshot -> text when nothing streamed.
    Always terminates with a ``complete`` chunk.
    """
    from ag_ui.core.types import RunAgentInput, UserMessage

    run_input = RunAgentInput(
        thread_id=thread_id,
        run_id=str(uuid.uuid4()),
        state={},
        messages=[UserMessage(id=str(uuid.uuid4()), role="user", content=message)],
        tools=[],
        context=[],
        forwarded_props={},
    )

    streamed_text = False
    tool_buf: Dict[str, Dict[str, str]] = {}

    async for ev in agent.run(run_input):
        t = type(ev).__name__
        if t == "TextMessageContentEvent":
            streamed_text = True
            yield {"status": "streaming", "chunk": ev.delta, "node": "agent"}
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
            yield {"status": "streaming", "tool_result": getattr(ev, "content", "")}
        elif t == "CustomEvent" and getattr(ev, "name", None) == "on_interrupt":
            payload = getattr(ev, "value", None)
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {"action_requests": []}
            yield {"status": "interrupt", "interrupt": payload or {"action_requests": []}}
        elif t == "MessagesSnapshotEvent" and not streamed_text:
            for m in ev.messages:
                if getattr(m, "role", None) == "assistant" and getattr(m, "content", None):
                    yield {"status": "streaming", "chunk": m.content, "node": "agent"}
        elif t == "RunErrorEvent":
            yield {"status": "error", "error": getattr(ev, "message", "unknown error")}

    yield {"status": "complete"}
