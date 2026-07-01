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

## Phase A — tool calls + results (validated) ✓

Against a keyless react agent that streams a `get_weather` tool call
(`spike/fake_tools.py`, `spike/parity_tools.py`):

- Tool-call name + args parity: **True**. Final-text parity: **True**.
- **Correction to the earlier note:** AG-UI *does* have a dedicated tool-result
  event — `ToolCallResultEvent` (`content`, `tool_call_id`) — it only appears with
  a *streaming* agent (one-shot nodes hide it in the snapshot). So the AG-UI path is
  a **superset**: it renders `↳ Sunny, 72F`, which the cli's current `updates` mode
  does not emit. Mapping: `ToolCallStart/Args/End → tool_calls`,
  `ToolCallResultEvent → tool_result`, `TextMessageContent → text`.
- Real models stream args as `ToolCallArgsEvent` deltas (confirmed by splitting the
  fake's arg stream); the messages snapshot carries them as a backstop.

## Phase B — interrupt / resume (partly validated) ⚠

Against a keyless `interrupt()` graph (`spike/observe_interrupt.py`):

- **Interrupt DISPLAY maps cleanly ✓.** A LangGraph `interrupt()` surfaces as
  `CustomEvent(name="on_interrupt", value=<payload JSON>)`, whose payload is exactly
  the `action_requests` the cli's interrupt chunk wants.
- **Resume does NOT round-trip out of the box ✗.** AG-UI resume uses a typed
  `ResumeEntry(interrupt_id, status∈{resolved,cancelled}, payload)` — the id comes
  from LangGraph state (`state.tasks[0].interrupts[0].id`), and the status vocabulary
  differs from the cli's accept/reject/edit decisions. Resumed this way the graph
  **re-interrupted** (same `on_interrupt`) and state never advanced, whereas the
  current `Command`-resume path continues to `decision was:…`. Resume needs real
  design — likely aligning how interrupts are *raised* with the AG-UI/HITL pattern
  the adapter expects, not a raw `interrupt()`.

Concrete evidence for why ADR 0002 gates the HITL surfaces on gate (2): **display is
easy, resume is the hard part.**

## Still open

- **Resume round-trip** (the gate-2 blocker above) — needs AG-UI's expected
  interrupt-raising pattern.
- **Usage events** — not checked.

## Recommendation

- **cli text + tools:** ready. A hidden `--agui` flag in `cli.py` routing through
  `agui.build_agent` + an `agui_stream_updates` like this spike reaches parity (and
  gains tool-result rendering), once ag-ui#2067 lands (or we accept fastapi+starlette).
- **cli interrupts:** display now, but hold the resume path until gate (2)'s resume
  round-trip is solved — that same solution is what the web/vscode HITL surfaces need.
