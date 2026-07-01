"""A keyless chat model that STREAMS a tool call then a final answer, so a real
react agent exercises the AG-UI ToolCall*/TextMessage* streamed events (not just
the final snapshot). Spike-only.
"""
from typing import Iterator, List

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool


@tool
def get_weather(city: str) -> str:
    """Get the current weather for a city."""
    return "Sunny, 72F"


class FakeStreamingToolModel(BaseChatModel):
    """First turn: stream a get_weather tool call. After a ToolMessage: stream text."""

    @property
    def _llm_type(self) -> str:
        return "fake-streaming-tool"

    def bind_tools(self, tools, **kwargs):
        return self  # hard-coded behavior; ignore the binding

    def _stream(self, messages: List[BaseMessage], stop=None, run_manager=None, **kwargs) -> Iterator[ChatGenerationChunk]:
        already_ran_tool = any(isinstance(m, ToolMessage) for m in messages)
        if not already_ran_tool:
            # Stream like a real model: the tool NAME first (no args), then the
            # args as separate deltas — so each arg segment becomes a
            # ToolCallArgsEvent the adapter can accumulate into valid JSON.
            yield ChatGenerationChunk(message=AIMessageChunk(
                content="",
                tool_call_chunks=[{"name": "get_weather", "args": "", "id": "call_1", "index": 0}],
            ))
            for seg in ('{"city": ', '"Portland"}'):
                yield ChatGenerationChunk(message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[{"name": None, "args": seg, "id": None, "index": 0}],
                ))
        else:
            for tok in ["It's ", "sunny ", "and ", "72F ", "in ", "Portland."]:
                yield ChatGenerationChunk(message=AIMessageChunk(content=tok))

    def _generate(self, messages: List[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        chunks = list(self._stream(messages, stop=stop, run_manager=run_manager, **kwargs))
        msg = chunks[0].message
        for c in chunks[1:]:
            msg = msg + c.message
        final = AIMessage(content=msg.content, tool_calls=getattr(msg, "tool_calls", []))
        return ChatResult(generations=[ChatGeneration(message=final)])


def build_tool_agent():
    from langgraph.prebuilt import create_react_agent
    return create_react_agent(FakeStreamingToolModel(), [get_weather])
