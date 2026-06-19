"""
langstage_cli - A Claude Code-style CLI for running LangGraph agents.

The streaming / interrupt utilities that used to live here now come from
``langgraph-stream-parser``. This package re-exports its dict-based convenience
API for backward compatibility; the lower-level helpers (interrupt parsing,
tool-call serialization, content extraction) are available from
``langgraph_stream_parser.extractors`` if needed directly.
"""

from importlib.metadata import PackageNotFoundError, version

from langgraph_stream_parser import (
    prepare_agent_input,
    stream_graph_updates,
    resume_graph_from_interrupt,
    astream_graph_updates,
    aresume_graph_from_interrupt,
)

# Single source of truth is the installed distribution metadata, so this can
# never drift from pyproject's version (it was stuck at a stale "0.4.0").
try:
    __version__ = version("langstage-cli")
except PackageNotFoundError:  # pragma: no cover - editable/source checkout
    __version__ = "0.0.0+local"

__all__ = [
    "prepare_agent_input",
    "stream_graph_updates",
    "resume_graph_from_interrupt",
    "astream_graph_updates",
    "aresume_graph_from_interrupt",
]
