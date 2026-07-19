"""The cli's in-process AG-UI streaming path.

Drives the agent through the official ``ag-ui-langgraph`` adapter in-process (no
web server) and maps AG-UI events onto the cli's ``print_chunk`` chunk contract —
so the renderer is unchanged. Text, tool calls, and tool *results* all render, and
interrupts are fully supported: they DISPLAY as a ``CustomEvent(on_interrupt)`` and
RESUME via ``forwarded_props.command.resume`` (ADR 0002 gate 2, resolved).

ADR 0002 started this as an experimental opt-in behind ``--agui``, alongside a
bespoke event-parser path. ADR 0003 finished the migration: since langstage-core
1.0 this is the ONLY streaming path, there is no parser to fall back to, and the
``agui`` extra is a redundant alias (AG-UI ships as a base dependency, CHANGELOG
0.6.1). The ``--agui`` flag it was named for is deprecated and inert (gh #88).
"""

from typing import Any, AsyncIterator, Dict

_IMPORT_HINT = 'the AG-UI streaming path needs: pip install "langstage-core[agui]"'


def ensure_agui_available() -> None:
    """Raise a clean, actionable error if the AG-UI adapter isn't installed."""
    try:
        import ag_ui_langgraph  # noqa: F401
        from langstage_core.agui import build_agent  # noqa: F401
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(_IMPORT_HINT) from e


def build_session_agent(graph: Any, *, name: str = "langstage-cli", config: Any = None) -> Any:
    """Wrap a compiled graph once per session (checkpointer attached by the core
    bridge), so multi-turn memory persists across turns.

    ``config`` (a ``RunnableConfig``, e.g. ``{"configurable": {...}}`` seeded from
    ``langstage.toml``'s ``[configurable]`` table) is forwarded to the graph on
    every turn, so keys beyond ``thread_id`` actually reach the agent. (gh #57)
    """
    ensure_agui_available()
    from langstage_core.agui import build_agent

    return build_agent(graph, name=name, config=config)


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
