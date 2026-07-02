"""Experimental in-process AG-UI streaming path for the cli (``--agui``).

Instead of parsing ``graph.stream()`` via ``langgraph-stream-parser``'s event
layer, this drives the agent through the official ``ag-ui-langgraph`` adapter
in-process (no web server) and maps AG-UI events onto the cli's existing
``print_chunk`` chunk contract — so the renderer is unchanged.

This is the first step of ADR 0002 (retire the bespoke event layer, converge on
AG-UI). Text + tool calls/results are at parity with the default path (and the
AG-UI path additionally surfaces tool *results*). Interrupts are fully supported:
they DISPLAY as a ``CustomEvent(on_interrupt)`` and RESUME via
``forwarded_props.command.resume`` (ADR 0002 gate 2, resolved).

Requires the ``agui`` extra::

    pip install "langstage-cli[agui]"
"""

from typing import Any, AsyncIterator, Dict

_IMPORT_HINT = 'the --agui path needs the agui extra: pip install "langstage-cli[agui]"'


def ensure_agui_available() -> None:
    """Raise a clean, actionable error if the AG-UI adapter isn't installed."""
    try:
        import ag_ui_langgraph  # noqa: F401
        from langstage_core.agui import build_agent  # noqa: F401
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(_IMPORT_HINT) from e


def build_session_agent(graph: Any, *, name: str = "langstage-cli") -> Any:
    """Wrap a compiled graph once per session (checkpointer attached by the core
    bridge), so multi-turn memory persists across turns."""
    ensure_agui_available()
    from langstage_core.agui import build_agent

    return build_agent(graph, name=name)


async def agui_stream_updates(
    agent: Any, message: str, thread_id: str, resume: Any = None
) -> AsyncIterator[Dict[str, Any]]:
    """Drive ``agent.run()`` in-process and yield ``print_chunk``-compatible chunks.

    Maps: TextMessageContent -> text; ToolCall{Start,Args,End} -> tool_calls;
    ToolCallResult -> tool_result; CustomEvent(on_interrupt) -> interrupt;
    RunError -> error; and a one-shot MessagesSnapshot -> text when nothing streamed.
    Always terminates with a ``complete`` chunk.

    When ``resume`` is provided (an interrupt is being answered), it is delivered
    as ``forwarded_props.command.resume`` — the field the ag-ui-langgraph adapter
    turns into LangGraph's ``Command(resume=...)`` — so the graph continues past
    the interrupt instead of re-interrupting. Mirrors the default path's
    ``prepare_agent_input(decisions=...)`` -> ``Command(resume={"decisions": ...})``.

    The mapping itself lives in the core (``agui.iter_chunk_frames``, 0.6.17) —
    shared with langstage-jupyter — so a rendering fix lands once.
    """
    from langstage_core.agui import iter_chunk_frames

    async for frame in iter_chunk_frames(agent, message, thread_id, resume=resume):
        yield frame
