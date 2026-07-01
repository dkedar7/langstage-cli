"""SPIKE parity runner: render the SAME agent through the current StreamParser
path and the in-process AG-UI path, using the cli's real ``print_chunk``, and
compare. Any difference is purely the event source.

Run: uv run python spike/parity_check.py
"""
import asyncio
import io
import re
import sys

from langgraph_stream_parser import load_agent_spec, prepare_agent_input, stream_graph_updates

from langstage_cli.cli import print_chunk
from spike.agui_adapter import agui_stream_updates

ANSI = re.compile(r"\x1b\[[0-9;]*m")
MESSAGE = "hello from the parity check"


def _capture(render_fn):
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        render_fn()
    finally:
        sys.stdout = old
    return buf.getvalue()


def current_path(graph):
    input_data = prepare_agent_input(message=MESSAGE)
    chunks = list(stream_graph_updates(graph, input_data, stream_mode="updates"))
    out = _capture(lambda: [print_chunk(c) for c in chunks])
    return chunks, out


def agui_path(graph):
    async def collect():
        return [c async for c in agui_stream_updates(graph, MESSAGE)]
    chunks = asyncio.run(collect())
    out = _capture(lambda: [print_chunk(c) for c in chunks])
    return chunks, out


def assistant_text(chunks):
    return "".join(c["chunk"] for c in chunks if isinstance(c, dict) and "chunk" in c)


def types_of(chunks):
    seen = []
    for c in chunks:
        if "chunk" in c:
            seen.append("text")
        elif "tool_calls" in c:
            seen.append("tool_call")
        elif "tool_result" in c:
            seen.append("tool_result")
        else:
            seen.append(c.get("status", "?"))
    return seen


def main():
    # Terminal glyphs (⏺, ●, ↳) are UTF-8; match the cli's own stdout reconfigure
    # so the Windows cp1252 console doesn't choke when we echo the transcript.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass

    graph = load_agent_spec("langgraph_stream_parser.demo.stub:graph")

    cur_chunks, cur_out = current_path(graph)
    agui_chunks, agui_out = agui_path(graph)

    cur_text = assistant_text(cur_chunks)
    agui_text = assistant_text(agui_chunks)

    print("=" * 66)
    print("CURRENT (StreamParser)  chunk kinds:", types_of(cur_chunks))
    print("  assistant text:", repr(cur_text))
    print("  rendered (ANSI-stripped):", repr(ANSI.sub("", cur_out).strip()))
    print("-" * 66)
    print("AG-UI (in-process)      chunk kinds:", types_of(agui_chunks))
    print("  assistant text:", repr(agui_text))
    print("  rendered (ANSI-stripped):", repr(ANSI.sub("", agui_out).strip()))
    print("=" * 66)

    web = any(m in sys.modules for m in ("uvicorn",))
    print("uvicorn imported:", web, "| real fastapi imported:", "fastapi" in sys.modules
          and not getattr(sys.modules["fastapi"], "__stub__", False))
    print()
    if cur_text == agui_text and cur_text:
        print(f"PARITY: assistant text matches across both paths -> {cur_text!r}")
    else:
        print(f"MISMATCH: current={cur_text!r} agui={agui_text!r}")


if __name__ == "__main__":
    main()
