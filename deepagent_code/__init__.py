"""
deepagent_code - A Claude Code-style CLI for running LangGraph agents.

The streaming / interrupt utilities that used to live here now come from
``langgraph-stream-parser``. This package re-exports its dict-based convenience
API for backward compatibility; the lower-level helpers (interrupt parsing,
tool-call serialization, content extraction) are available from
``langgraph_stream_parser.extractors`` if needed directly.
"""

from langgraph_stream_parser import (
    prepare_agent_input,
    stream_graph_updates,
    resume_graph_from_interrupt,
    astream_graph_updates,
    aresume_graph_from_interrupt,
)

__version__ = "0.2.0"

__all__ = [
    "prepare_agent_input",
    "stream_graph_updates",
    "resume_graph_from_interrupt",
    "astream_graph_updates",
    "aresume_graph_from_interrupt",
]
