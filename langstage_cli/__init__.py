"""
langstage_cli - a Claude Code-style CLI for running LangGraph agents.

Streaming runs through the in-process AG-UI adapter (``langstage-core``'s
``agui``); the ``langstage-core`` 1.0 rename retired the old StreamParser
convenience API. ``prepare_agent_input`` is re-exported for callers that build
agent input directly.
"""

from importlib.metadata import PackageNotFoundError, version

from langstage_core import prepare_agent_input

try:
    __version__ = version("langstage-cli")
except PackageNotFoundError:  # pragma: no cover - editable/source checkout
    __version__ = "0.0.0+local"

__all__ = ["prepare_agent_input"]
