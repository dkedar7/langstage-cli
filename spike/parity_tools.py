"""SPIKE phase A: tool-call + tool-result parity on a real (keyless) react agent.

Run: uv run python -m spike.parity_tools
"""
import asyncio
import io
import re
import sys

from langgraph_stream_parser import prepare_agent_input, stream_graph_updates

from langstage_cli.cli import print_chunk
from spike.agui_adapter import agui_stream_updates
from spike.fake_tools import build_tool_agent

ANSI = re.compile(r"\x1b\[[0-9;]*m")
MSG = "weather in Portland?"


def _utf8():
    for s in (sys.stdout, sys.stderr):
        r = getattr(s, "reconfigure", None)
        if r:
            try:
                r(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def _capture(fn):
    buf, old = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        fn()
    finally:
        sys.stdout = old
    return buf.getvalue()


def kinds(chunks):
    out = []
    for c in chunks:
        if "chunk" in c:
            out.append("text")
        elif "tool_calls" in c:
            out.append(f"tool_call:{c['tool_calls'][0]['name']}({c['tool_calls'][0].get('args')})")
        elif "tool_result" in c:
            out.append(f"tool_result:{c['tool_result']!r}")
        else:
            out.append(c.get("status", "?"))
    return out


def main():
    _utf8()

    cur = list(stream_graph_updates(build_tool_agent(), prepare_agent_input(message=MSG), stream_mode="updates"))

    async def collect():
        return [c async for c in agui_stream_updates(build_tool_agent(), MSG)]
    agui = asyncio.run(collect())

    cur_render = ANSI.sub("", _capture(lambda: [print_chunk(c) for c in cur])).strip()
    agui_render = ANSI.sub("", _capture(lambda: [print_chunk(c) for c in agui])).strip()

    print("CURRENT (StreamParser):")
    for k in kinds(cur):
        print("   ", k)
    print("\nAG-UI (in-process):")
    for k in kinds(agui):
        print("   ", k)
    print("\n--- rendered, current ---\n" + cur_render)
    print("\n--- rendered, AG-UI ---\n" + agui_render)

    cur_tools = [c["tool_calls"][0]["name"] for c in cur if "tool_calls" in c]
    agui_tools = [c["tool_calls"][0]["name"] for c in agui if "tool_calls" in c]
    cur_text = "".join(c["chunk"] for c in cur if "chunk" in c)
    agui_text = "".join(c["chunk"] for c in agui if "chunk" in c)
    agui_has_result = any("tool_result" in c for c in agui)

    print("\n=== verdict ===")
    print("tool call name parity:", cur_tools == agui_tools, cur_tools, agui_tools)
    print("final text parity:", cur_text == agui_text, repr(agui_text))
    print("AG-UI surfaced tool_result:", agui_has_result,
          "(current 'updates' mode does not emit one)")


if __name__ == "__main__":
    main()
