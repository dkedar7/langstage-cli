# Spike: cli renderer on in-process AG-UI (ADR 0002, cli-first)

**Status:** spike / not for merge. Proves the cli-first migration is viable before
wiring a real flag into `cli.py`.

## Goal

Render the same agent through (a) today's `StreamParser` path and (b) the
in-process AG-UI path (`ag_ui_langgraph.LangGraphAgent.run()`), using the cli's
**unmodified** `print_chunk` renderer, and check parity. Any difference is purely
the event source.

## Result — PARITY ✓

Against the demo stub (`langgraph_stream_parser.demo.stub:graph`):

| | chunk kinds | assistant text | rendered (ANSI-stripped) |
|---|---|---|---|
| Current (StreamParser) | `text, complete` | `(demo agent) You said: …` | `⏺ (demo agent) You said: …` |
| AG-UI (in-process) | `text×9, complete` | `(demo agent) You said: …` | `⏺ (demo agent) You said: …` |

Rendered output is **identical**. The AG-UI path even surfaced finer-grained
token streaming (9 deltas vs 1 update); `print_chunk`'s one-marker-per-run logic
(#34/#40) collapses both to the same transcript. `uvicorn`, real `fastapi`, and
`starlette` were all confirmed **not imported**.

Run it: `uv run python -m spike.parity_check`

## Findings (what the real migration must handle)

1. **Checkpointer is required.** The AG-UI adapter calls `graph.aget_state()`
   (state + interrupt/resume), so a graph compiled without a checkpointer crashes
   with `No checkpointer set`. The core's `agui.build_agent` already attaches an
   `InMemorySaver` when absent — the migration should route through that, not call
   `LangGraphAgent` directly. (Reproduced here; the adapter mirrors the fallback.)
2. **The #2067 import bug is real in practice.** This venv has `ag_ui_langgraph`
   but not `fastapi`, so `import ag_ui_langgraph` fails today (eager endpoint
   import). The spike stubs `fastapi` to simulate the one-line upstream fix
   (ag-ui-protocol/ag-ui#2067) and proves the terminal path then needs **no** web
   stack. Blocked on #2067 merging, or we accept `fastapi`+`starlette` (no uvicorn).
3. **Text + one-shot snapshot: proven.** Token deltas (`TextMessageContentEvent`)
   and one-shot node output (`MessagesSnapshotEvent`) both map cleanly to the
   `chunk` contract.

## Not covered here (next validation steps)

- **Tool calls / tool results.** The adapter maps `ToolCall{Start,Args,End}` →
  `tool_calls`, but this wasn't run against a tool-using agent. Note: AG-UI's core
  event set has **no dedicated tool-result event** — results arrive via messages,
  so `tool_result` rendering must be recovered from the messages snapshot. Real gap
  to design.
- **Interrupt / resume (HITL).** Deliberately out of scope — ADR 0002 gate (2).
  The cli has an interrupt path; parity there is the next thing to prove.
- **Usage events.** Not checked.

## Recommendation

Green-light the real implementation: a hidden `--agui` / env flag in `cli.py`
routing through `agui.build_agent` + an `agui_stream_updates` shaped like this
spike, validated first on the demo stub (done) then a tool-using agent and the
interrupt path, before any second surface.
