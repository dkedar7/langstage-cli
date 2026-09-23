# Changelog

## 0.6.31 - 2026-09-23

### Security
- **The HITL approval prompt now shows EVERY argument of the action being approved, not
  just the first value (gh #146).** `format_interrupt_request` previewed a tool-review
  `ActionRequest` with `get_tool_arg_preview` — the compact streaming preview, which returns
  only the first arg *value* — so approving `transfer_funds(to, amount, confirm)` showed
  `└─ alice` and hid `amount=1000000` / `confirm=True` (and every arg name). The approval
  path now renders all args as `name=value` (each value capped at 80 chars, never dropped;
  one field per line when the row would be long). Non-printable characters in a value
  (newlines, ANSI escapes) are shown escaped, so a tool arg can't break out of its line and
  forge prompt text above the Approve/Reject menu. The compact one-value preview on the
  streaming tool-call render is unchanged.
- **An `action` / `tool` label on a generic `interrupt({...})` no longer hides the rest of
  the payload (gh #159).** The label routed the dict to the ActionRequest branch, which read
  only `args` — so `interrupt({"action": "delete_file", "path": "/etc/passwd"})` asked the
  user to approve a bare `delete_file`, while the same dict *without* the label rendered in
  full. Every sibling field is now shown alongside the label (and alongside `args` when
  both are present). The same rule now applies to a `description` / `question` /
  `message` / `prompt` headline (its sibling fields are listed under it), and a label-less
  dict too long for the one-line JSON repr lists every field instead of being cut off
  mid-payload. Resume semantics (#99) are unchanged.
- **Agent-supplied labels in the HITL prompt are escaped too.** The tool name, the
  `question` / `description` / `message` / `prompt` headline, a bare-string
  `interrupt("...")`, and every field name now go through the same escaping as argument
  values, so a label carrying a newline + a fake `Approve? [y/N]` line or an ANSI escape
  (e.g. `\x1b[2K` to erase a line) can no longer forge prompt text. A multi-line question
  now renders on one line with a visible `\n`.

### Fixed
- **`--no-persist` is honored with `--continue` / `--resume`: the session resumes
  read-only (gh #147).** `--continue` / `--resume` forced persistence on unconditionally,
  so an explicit `--no-persist` on the same command line was silently dropped — the
  "don't save this" turn was written to the durable store, indexed, and resumable later
  (and on a fresh workspace, the store was created). Now the explicit flag wins: the run
  reads the prior session from a throwaway snapshot of the store (so it sees the full
  checkpoint, including a pending interrupt), prints `resuming session <id> read-only —
  this run will not be saved`, and writes nothing back — no appended turns, no index
  update, no store created. With nothing to resume it's a plain ephemeral run. Only the
  explicit CLI flag has this effect: `LANGSTAGE_PERSIST=0` / `[session] persist = false`
  are lower-precedence than the `--continue` / `--resume` CLI flags, which still imply
  persistence over them. Documented in the README and `--help`; `/status` reports
  `Persist: off (--no-persist: read-only resume, nothing saved)`.

## 0.6.30 - 2026-08-08

### Fixed
- **A relative `agent.spec` in `langstage.toml` now resolves against the toml's own
  directory, so an `init`-scaffolded project runs from any subdirectory (gh #116).**
  Config discovery walks UP ancestor dirs to find `langstage.toml` (a project-root
  concept, so you can run the CLI from anywhere in your project), but a relative
  `agent.spec` in it — exactly what `init` scaffolds (`spec = "my_agent.py:graph"`) —
  was resolved against the process **cwd**. So the moment you ran from a subdirectory the
  spec was looked up under that subdir and crashed `Agent file not found`, even though
  `--show-config` (walking up correctly) reported the spec resolved. A relative spec that
  came from a discovered `langstage.toml` now rebases onto **that toml's directory** (the
  project root) before loading — the same way a path in `pyproject.toml` / `tsconfig.json`
  resolves relative to the config file — so the project runs identically from its root and
  any subdirectory. A spec from `-a` / `LANGSTAGE_AGENT_SPEC` keeps its cwd-relative base
  ("the file is where the user typed the command", gh #30); only toml-sourced relative
  specs change base. The `path:attr` split stays Windows drive-letter safe (`C:\x.py:graph`).
- **A wrong-type agent object now shows `build_agent`'s clean `TypeError` on the default
  persist-on run path, not a cryptic `AttributeError: 'X' object has no attribute
  'checkpointer'` (gh #117).** When the resolved agent isn't a compiled graph (`graph =
  None`, a dict, an int — a placeholder, a construction that fell through, or `-a` pointed
  at the wrong attribute), the default path attaches the durable `AsyncSqliteSaver` (#102)
  by setting `graph.checkpointer` **before** `build_agent` validates the graph — so the
  attribute-set raised first and leaked an internal `AttributeError`. `--verify` and
  `--no-persist` already reached `build_agent`'s actionable `TypeError: build_agent expected
  a compiled LangGraph graph (CompiledStateGraph) ... but got NoneType.`; the default config
  (which every user hits) now does too. The durable saver assignment is guarded exactly like
  the non-durable `_ensure_checkpointer`, so a valid graph is unaffected and the wrong-type
  object falls through to the one clean validator message.

## 0.6.29 - 2026-08-06

### Fixed
- **`--show-config` no longer flags the documented `[session] persist` key as an "unknown
  TOML key" (gh #112).** `langstage-core` 1.0.32 added unknown-TOML-key detection
  (`HostConfig.unknown_toml_keys()`, surfaced in `--show-config`), which flags any TOML key
  that maps to no config field. `persist` is resolved out-of-band by `_resolve_persist`
  (flag > `LANGSTAGE_PERSIST` > `[session] persist` > default-on), so it isn't a `CodeConfig`
  dataclass field — and the detector wrongly listed the documented, honored `session.persist`
  under "unknown TOML keys (ignored - a typo or wrong table?)", in the very same output that
  attributed `persist = ... [toml (session.persist)]` to it. `CodeConfig._TOML` now registers
  `session.persist` in the known-key set so it stops being false-flagged. It's a map entry,
  not a field, so `persist` stays resolved out-of-band and rendered by its own block (no
  double-render). A genuine typo like `[session] perssist` (or any unknown table) is still
  flagged — the detector isn't disabled, only the false positive is removed.

## 0.6.28 - 2026-08-03

### Fixed
- **`/history` no longer errors in the default persist-on config (gh #106).** Since
  persistence went on by default (#102), the durable checkpointer is the async-only
  `AsyncSqliteSaver`, opened inside each turn's own event loop and closed when the turn
  ends. `/history` still read state via the **sync** `graph.get_state`, which against that
  async, already-closed saver scheduled `aget_tuple` as a never-awaited coroutine and
  surfaced `Could not retrieve history: Event loop is closed` plus a `RuntimeWarning` —
  every user hit it on a normal run (it only worked under `--no-persist`). `/history` now
  reads through the async API on a freshly-opened saver over the same SQLite file (mirroring
  the run loop), so it renders the conversation on a persist-on run; the sync path still
  serves the in-memory (`--no-persist`) case.
- **`--show-config` names the env var you actually set for `DEEPAGENT_SPEC` (gh #107).** A
  value supplied through the oldest legacy alias `DEEPAGENT_SPEC` was labelled
  `[env:DEEPAGENT_AGENT_SPEC]` — the canonical-legacy key the resolver copies it onto, a
  var absent from the environment — contradicting the command's own stderr deprecation note.
  The `[source]` column now reports `[env:DEEPAGENT_SPEC]`, matching the note and the
  source-accuracy fixes already done for TOML files (#101) and CLI flags (#20).
- **A load/top-level exception with an empty message is no longer a blank, typeless
  `Error:` (gh #109).** A bare `assert`, `raise NotImplementedError`, or `RuntimeError()`
  has an empty `str(e)`, so the top-level handlers printed just `Error:` — no class, no
  hint. They now name the exception class (`Error: NotImplementedError`) and point at `-v`
  for the traceback, matching the runtime turn-error path.

### Added
- **`--show-config` surfaces the session/persistence config (gh #108, finishes #102 item
  3).** Persistence — a headline feature, on by default with a full
  `--persist/--no-persist` > `LANGSTAGE_PERSIST` > `[session] persist` > default chain —
  was visible only in interactive `/status`, never in the scriptable `--show-config`.
  `--show-config` now shows the resolved `persist` value with its `[source]`, the
  `(env: LANGSTAGE_PERSIST, toml: session.persist)` hint, and the store path when on (in
  both on and off states, so the off-switch is verifiable). Interactive `/config` appends
  the identical block, so the two diagnostics can't disagree.

## 0.6.27 - 2026-07-31

### Added
- **First-class cross-invocation session persistence — `--continue` / `--resume` (gh #102).**
  Every `langstage-cli` run used to be amnesiac: memory lived only for one process (an
  in-memory checkpointer), so the second command in a workflow couldn't see the first.
  The CLI now attaches a durable, per-workspace checkpointer **itself** — no user-supplied
  saver, no graph edits — so a fresh run is persisted and a later run can resume it:
  `--continue`/`-c` resumes the most recent session for the workspace (and starts fresh,
  with a note, if there is none); `--resume <id>` resumes a specific thread (exact id or an
  unambiguous prefix; a bare `--resume`, like `--list-sessions`, lists them); a plain run
  starts a new-but-persisted session. `--continue` and `--resume` are mutually exclusive.
  State lives at `~/.langstage/sessions/<workspace-hash>.sqlite` (honoring
  `LANGSTAGE_CONFIG_HOME`; override the whole location with `LANGSTAGE_CLI_SESSIONS_DIR`),
  with a small JSON index (thread id, timestamps, first-message snippet) driving `--continue`
  and `--list-sessions`. Persistence is on by default (disable via `--no-persist`,
  `LANGSTAGE_PERSIST=0`, or `[session] persist = false`); a graph that brings its own
  checkpointer keeps it (yours always wins), and a pinned `[configurable] thread_id` now
  genuinely persists across runs. Because the AG-UI streaming path is async-only — langgraph's
  sync `SqliteSaver` raises `NotImplementedError` for the async checkpointer methods the graph
  invokes — the durable saver is the async-native `AsyncSqliteSaver` over the same SQLite file,
  opened and closed within each turn's own event loop (its aiosqlite connection can't cross
  loops); `/status` surfaces persistence state and the store path. Adds a
  `langgraph-checkpoint-sqlite` dependency.
- **`langstage-cli init` scaffolds a runnable starter agent + `langstage.toml` (gh #104).**
  Bridges the gap between `--demo` ("see the CLI work") and "run *my* graph": `init` writes a
  minimal, immediately-runnable `my_agent.py` (the README's keyless stdlib `StateGraph`
  example — needs only `langgraph`, a base dependency) plus a `langstage.toml` wired to it, so
  the very next `langstage-cli "..."` runs the user's own graph with no `-a` and no
  hand-editing. It refuses to overwrite an existing `my_agent.py` / `langstage.toml` unless
  `--force` is passed, and prints exactly what it wrote plus the next step.

### Fixed
- **`--show-config` now attributes each merged TOML value to the file it ACTUALLY came from
  (gh #101).** When a global `~/.langstage/config.toml` and a project `langstage.toml` were both
  present and merged, the resolver stamped **every** TOML-sourced value with the last file it read
  (the project file), so a value set only in the global config was mislabelled
  `[toml (langstage.toml)]` — sending a user debugging "where did `workspace_root` come from?" to
  the wrong file. Merge/precedence were always correct; only the displayed source filename lied.
  The CLI now post-processes the resolved provenance, walking the same files in merge order
  (project wins on conflicts) and relabelling each value with the highest-precedence file that
  actually defines its key. A cli-local fix — it corrects the source map the shared resolver
  produced without touching core.
- **A free-text `interrupt()` resume no longer leaks an `ag_ui_langgraph` WARNING to the console
  (gh #103).** On the interactive HITL path, answering a plain-string `interrupt(...)` with free
  text made the bundled `ag_ui_langgraph` dependency log `failed to parse resume_input as JSON,
  treating as string (...)` at WARNING on **every** response — an error-looking line printed right
  above the (correct) answer, in default interactive mode, on the CLI's headline HITL feature. The
  resumed value was always right; it was pure confusing noise. The CLI now installs a surgical
  `logging.Filter` that drops **only** that one benign record on the emitting `ag_ui_langgraph`
  logger during the interactive resume, so no other warning is ever hidden. cli-local by design —
  the CLI owns its console UX, so no global logging filter lands in core.

## 0.6.26 - 2026-07-26

### Fixed
- **A generic `interrupt(value)` now resumes with the RAW value the user provides, so
  `name = interrupt("What is your name?")` gets the answer back instead of an approval
  envelope it never asked for (gh #99).** #82 / #95 fixed DISPLAYING a generic
  `interrupt(...)`; this closes the matching RESUME gap. The CLI built the resume payload
  unconditionally as `{"decisions": [...]}` — the deepagents tool-REVIEW protocol — and forwarded
  it verbatim as `command.resume`, which becomes the return value of `interrupt()`. So the
  canonical LangGraph "collect input from a human" pattern received
  `{"decisions": [{"type": "approve"}]}` and produced the visibly wrong
  `Hello, {'decisions': [{'type': 'approve'}]}!`; even "Provide custom decision (JSON)" wrapped the
  typed value inside the envelope, leaving no workaround. The resume now branches on the same signal
  #82/#95 use to render: a deepagents tool-review interrupt (an `action_request` dict keyed `action`,
  or the legacy `tool`) keeps the `{"decisions": [...]}` envelope unchanged, while a generic / scalar
  `interrupt(value)` — a bare string (the `"What is your name?"` form) or a plain dict without an
  action key — resumes with the raw value the user enters, UNWRAPPED (JSON is parsed when it parses, so
  a number / list / dict survives; otherwise the text is used verbatim as a string). Under
  `--no-interactive`, where nobody can supply the requested value, a generic interrupt resumes with an
  empty value rather than injecting a fake approval — keeping the "run to completion" contract without
  handing the agent a decision it never mentioned.

## 0.6.25 - 2026-07-25

### Fixed
- **Bare `/config` now renders the LIVE configuration, so after a runtime change it agrees with
  `/status`, the single-key `/config <key>` read, and its own `✓ Set` line instead of
  contradicting all three (gh #97).** The full-table `/config` view printed
  `_resolved_config_report` — a string snapshotted at startup and never updated — while every
  other reader and setter uses the live `context`: `/config verbose on` sets `context["verbose"]`,
  `/config verbose` and `/status` read it back, and `/reset` mutates
  `context["config"]["configurable"]["thread_id"]`. So after `/verbose on` the table still showed
  `verbose = False [default]` even as `/status` said `on` and `/config verbose` said `True`, and
  after `/reset` it kept printing the pre-reset `thread_id`. Worse, the frozen string carried a
  wrong **source** label — `[default]` for a value the user had actually overridden — so a user
  who set a value and re-ran `/config` to confirm saw it reported as unset and could reasonably
  conclude the set had silently failed. Bare `/config` now RE-RENDERS the one `describe()`
  diagnostic (the same renderer `--show-config` uses) from the `CodeConfig` resolved at startup,
  overlaid with live state: `verbose` from `context["verbose"]` (relabelled `[override]`, never
  `[default]`, when it differs from the resolved value) and the live `[configurable]` values (so a
  `/reset` thread_id shows through). Crucially this RE-RENDERS the startup-resolved cfg — whose
  per-field provenance is frozen — rather than RE-RESOLVING, so it still can't pick up the
  `LANGSTAGE_WORKSPACE_ROOT` that `apply_workspace()` self-publishes: the #64 fix holds and the
  no-mutation startup table stays byte-for-byte identical to `--show-config`. Same
  "two provenance views disagree" family as #64 / #66 / #79, now closed for the full `/config`
  table. `--show-config` (the non-interactive flag) is unaffected.

## 0.6.24 - 2026-07-25

### Fixed
- **A plain-string `interrupt("Approve deleting X?")` — the canonical LangGraph HITL form — now
  shows the human what they're approving, instead of "(no action details provided)" (gh #95).**
  The approval menu dropped the payload of a bare-string/scalar interrupt (dict payloads rendered
  fine), so a confirmation like `interrupt("Approve deleting ALL files in /home?")` asked the user
  to Approve/Reject blind — defeating the point of HITL. The renderer already handled a scalar
  (`format_interrupt_request` returns `str(action)`); the payload was dropped upstream in
  `langstage-core`'s `on_interrupt` handler, which `json.loads()`'d the string, failed on a
  non-JSON question, and fell back to an empty request. Fixed in **langstage-core 1.0.28** on the
  chunk wire the CLI consumes; this raises the floor to `>= 1.0.28` so a fresh install gets it.

## 0.6.23 - 2026-07-23

### Fixed
- **`-q/--quiet` and the non-TTY auto-quiet now apply when the message arrives on piped
  stdin (gh #93).** `-q` is documented to suppress the header, spinner, tool chatter,
  timing, and color and emit only the agent's reply, and #53 wired the same auto-quiet
  for a non-TTY stdout so a piped run is clean without any flag. That contract held for a
  single-shot run — a `MESSAGE` arg or `-f/--file` — but not for the most idiomatic pipe
  form, `echo "hi" | langstage-cli --demo -q`: a stdin message is read by the interactive
  loop, which honored neither `-q` nor the `stdout.isatty()==False` gating, so it still
  emitted the `····` separator rules, the `❯` prompt row (with `\x01\x02` bracketed-paste
  bytes), the `Nms` timing line, and a trailing `Goodbye!` — ~380 bytes of chrome around a
  25-byte reply. Two root causes: (1) the auto-quiet formula keyed only off a `MESSAGE`/
  `-f`/`--verify` single-shot, so a stdin-fed run never triggered it; and (2) the
  interactive loop applied no quiet gating at all. Both are fixed. The auto-quiet gate is
  now "stdout is not a TTY **and** the input is non-interactive" — a single-shot arg/file/
  verify **or** piped (non-TTY) stdin — and the conversation loop suppresses its
  separators, prompt, timing line and `Goodbye!` under `_QUIET`, so a piped message
  produces byte-identical output to the `-q "msg"` arg path (only the reply). The
  interactive-loop path is kept (not rerouted to single-shot) deliberately: piped stdin
  can still carry multiple lines and slash commands — `printf 'hi\nbye\n' | langstage-cli`
  runs two clean turns, and `echo /config | langstage-cli` still drives the command — each
  turn now just renders on the scriptable path. A live terminal session (stdin **is** a
  tty, no `-q`) is unchanged: it keeps its header, separators, prompt, timing and farewell.

## 0.6.22 - 2026-07-23

### Fixed
- **Tool calls and tool results now render for a non-token-streaming agent (gh #91).** An agent
  whose messages are produced without token streaming — a custom node calling `model.invoke()`, a
  non-streaming provider, a rule-based node (the "Creating Your Own Agent" shape) — delivers its
  turn via the AG-UI snapshot path, and `langstage-core`'s snapshot handler yielded text only: the
  `● tool_name` / `↳ result` the CLI renderer already knows how to draw never arrived, and a
  tool-call-only turn rendered nothing while `--verify`/the exit code reported success. The root
  cause and fix are in `langstage-core`; this release raises the floor to **>= 1.0.24**, which
  emits `tool_calls`/`tool_result` on the snapshot wire, so `print_chunk` renders them. (Tool
  frames remain intentionally suppressed in the quiet/scriptable single-shot path, where stdout
  carries only the reply text — that "tool chatter" contract from 0.6.20 is unchanged.)
- **README: `/status` no longer documents a "sync/async mode" field (gh #90).** 0.6.21 removed that
  line from the runtime (#88) and updated the CLI Options table and the `[ui] async_mode` example,
  but missed the `/status` entry in the Commands list — so the docs still advertised a field the
  command hasn't shown since 0.6.21. Corrected to `agent, thread, verbose, cwd`.

## 0.6.21 - 2026-07-19

### Changed
- **`--async-mode`/`--sync-mode` and `--agui` are deprecated — they were no-ops advertised as
  features (gh #88).** CHANGELOG 0.6.0 (ADR 0003) said the `--agui`/`--async`/`--stream-mode` flags
  were accepted-and-ignored *"for one release"*. `--stream-mode` was duly retired in #62;
  `--async-mode`/`--sync-mode` and `--agui` were still shipping **20 patch releases later**, still
  inert, and still presented as working controls. Root cause is that ADR 0003 collapsed every turn
  onto the single async AG-UI path (`run_single_turn_agui` → `agui_stream.agui_stream_updates` →
  `core.agui.iter_chunk_frames`), leaving no second behavior for either flag to select: `use_async`
  was resolved, stored in the session context, and read in exactly three places — `/status`,
  `/config`, and `--show-config` — **all display-only**, with no `if use_async:` branch anywhere in
  `run()`, so `--sync-mode` and `--async-mode` produced byte-identical output; `use_agui` was passed
  into `run()` and never read at all. `--agui`'s help text was additionally describing a mechanism
  that no longer exists ("instead of the built-in event parser" — since langstage-core 1.0 the AG-UI
  adapter *is* the only path, and the `[agui]` extra has been a redundant alias since 0.6.1). Rather
  than wire a dead knob up to something invented, both are now retired with the same posture
  `--stream-mode` got: hidden from `--help`, dropped from the README (the CLI Options row and the
  `langstage.toml` `[ui] async_mode` example), omitted from `--show-config`, removed from `/status`
  (the "Mode: async/sync" line, which reported a distinction the runtime does not have) and from the
  `/config` key map, no longer resolved from `[ui] async_mode`, and accepted-and-ignored on the CLI
  (so an existing `--async-mode` / `--agui` invocation doesn't hard-error) with a one-line
  deprecation notice. An existing `langstage.toml` that still sets `[ui] async_mode` keeps loading
  untouched — the key is simply ignored, never an error, since a config file that suddenly failed to
  resolve would be a worse regression than the dead knob it retires. No streaming behavior changes.
  New tests assert the flags are gone from `--help`/`--show-config`/`/status`/`/config`, that each is
  still accepted with a notice, and that a TOML carrying the retired key still loads.

## 0.6.20 - 2026-07-18

### Fixed
- **The interactive conversation loop erased every agent reply on a real terminal (gh #84).** The
  headline documented workflow — `langstage-cli --demo` / `-a my_agent.py:graph` — showed the user
  their own prompt and the timing line with the agent's answer *missing*. `run_single_turn_agui()`
  stops the spinner twice per turn: once on the first chunk (correct — that clears the "Thinking…"
  animation) and again in a `finally`, which exists so a turn that streams no chunks at all still
  clears the line instead of leaving a dangling "Thinking…". But `Spinner.stop()` ends with
  `print("\r\033[2K")` — CR plus "erase entire line" — and the reply is streamed with `end=""` and
  never terminated by a newline, so by the time the `finally` ran the cursor was parked on the
  *reply's* last line and the second clear wiped it; `print_timing()` then wrote over where the reply
  had been. A one-line reply — the common case — vanished entirely, and a multi-line reply lost its
  last line, confirming a single current-line `CSI 2K`. This never reproduced in single-shot, piped,
  or `--quiet` runs because those have no spinner (`spinner = None if _QUIET else …`), which is
  exactly why the existing suite and the README one-liner looked healthy while the primary
  interactive feature was unusable. `Spinner.stop()` is now idempotent — it emits its line-clear at
  most once per run, reset on `start()` — so the useful first clear and the no-chunk safety net both
  survive while the redundant second call becomes a no-op. Regression tests replay the emitted byte
  stream through a minimal VT model (CR / `CSI 2K` / newline) and assert the reply is still on the
  rendered screen, since `pty.fork()` is Unix-only and a stopped-twice assertion would only couple to
  the implementation.
- **Human-in-the-loop approval crashed with a cryptic `Error: (25, 'Inappropriate ioctl for device')`
  on a non-TTY, after leaking the approval menu to stdout (gh #86).** In default interactive mode, an
  agent raising a LangGraph `interrupt(...)` under a piped / CI / cron run — the scriptable context
  the README actively promotes (`answer=$(langstage-cli -a my_agent.py:graph "do X")`) — hit
  `get_key()`, whose Unix branch calls `termios.tcgetattr(fd)` with no `sys.stdin.isatty()` guard;
  `tcgetattr` raises on a non-terminal, surfacing as a raw errno with no hint that `--no-interactive`
  exists. Compounding it, `select_option()` printed the prompt, the `\033[?25l` cursor-hide escape,
  and all four menu options to **stdout** *before* ever reading a key — corrupting the very stream a
  piped single-shot run is contractually supposed to fill with only the agent's reply. The CLI
  already detected a non-TTY for the quiet/scriptable decision; that detection simply was not applied
  to the approval path. `handle_interrupt_input()` now checks stdin **before anything is printed** and
  exits `1` with an actionable message on stderr (`Error: approval required but stdin is not a
  terminal. / Re-run with --no-interactive to auto-approve pending actions.`), leaving stdout
  untouched. It deliberately does *not* silently auto-approve: `interrupt()` is a request for human
  review, so approving an action nobody saw is a worse failure than stopping — `--no-interactive`
  remains the documented, explicit opt-in and its behavior is unchanged. Both call sites now share one
  `_is_a_tty()` helper that never raises on a closed or replaced stream.

## 0.6.19 - 2026-07-16

### Fixed
- **The HITL approval prompt rendered nothing useful for a *generic* LangGraph `interrupt(...)`
  (gh #82).** #69 wired the deepagents `ActionRequest` shape (tool name under `action`), but the
  renderer still assumed *every* interrupt was that shape, so an arbitrary `interrupt(...)` value —
  the canonical HITL primitive from the LangGraph docs — was dropped: a plain dict such as
  `interrupt({"question": "..."})` rendered `1. unknown` (silently discarding the `question` and
  every other non-`action`/`tool`/`args` key), and a bare-string interrupt reaching the renderer as
  a string `action_request` raised `'str' object has no attribute 'get'`. Either way the operator was
  asked to approve or reject an action they could not see — and because approval can gate destructive
  work, approving blind is a real hazard, not a cosmetic gap. `print_chunk()`'s interrupt branch now
  renders **any** payload actionably via a new `format_interrupt_request()` helper: a bare string /
  scalar shows the value itself; a dict without a recognized tool key surfaces its first
  human-readable field (`description`/`question`/`message`/`prompt`) or, failing that, a compact JSON
  repr of the whole payload instead of `unknown`; and the structured `action`/`tool` + args path is
  unchanged. When `action_requests` is empty (a bare-string interrupt's payload is JSON-decoded to
  `{}` and dropped upstream by `langstage-core`'s AG-UI adapter), the CLI now renders a surviving raw
  interrupt `value` if present, else notes `(no action details provided)` so the banner is never
  silently contentless. The invariant: the approval prompt must never ask the user to approve an
  action whose description it silently threw away.

## 0.6.18 - 2026-07-15

### Documentation
- **README `## Commands` section was stale — it listed only 6 of the 9 interactive slash
  commands.** Missing entirely were `/status`, `/version`, and `/verbose`; `/reset` omitted its
  `/restart` alias; and `/config` was described as show-only when it also sets a runtime key
  (`/config [key] [value]`). The section now lists every registered command with its aliases and
  accurate description, matching what `/help` prints in the loop. Docs-only; no code change.

## 0.6.17 - 2026-07-15

### Fixed
- **Bare `/verbose` never toggled despite being advertised as "Toggle verbose output mode"
  (gh #79).** The interactive `/verbose` command is registered — and shown in `/help` — as
  "Toggle verbose output mode", but the shipped bare form (`/verbose` with no argument) only
  *printed* the current state and told the user to run `/verbose on|off`; it flipped nothing.
  A one-word command named "Toggle" that no-ops is the same "advertised != honoured" class as
  #62/#66/#36, on the interactive command surface. `cmd_verbose` now flips the value on a bare
  call and reports the new state (`✓ Verbose mode enabled`/`disabled`), matching the `/help`
  description and the one-word command name. The explicit `/verbose on|off` (and the `true/1`,
  `false/0` synonyms) form is unchanged — it still *sets* the value rather than toggling — and
  an unrecognised argument now prints `Usage: /verbose [on|off]` instead of silently emitting a
  misleading confirmation.

## 0.6.16 - 2026-07-14

### Fixed
- **Quiet/scriptable mode leaked the `⚠ Action Required` HITL banner onto stdout (gh #77).**
  The interrupt branch of `print_chunk()` never got the `_QUIET` gate its sibling
  tool-call/tool-result branches have, so when an agent raised an `interrupt()` on the
  documented `--no-interactive` auto-approve + piped/quiet path, the `⚠ Action Required`
  banner, a leading blank line, and the pending-action list were printed to **stdout** — ahead
  of the real reply — corrupting the machine-readable output that a scripting consumer captures
  (and the run still exited `0`, so nothing signalled the corruption). Unlike #76 (stderr only),
  this landed on the very stream a script reads. The interrupt branch now returns early in quiet
  mode, so stdout carries only the agent's reply; the `Auto-approving …` diagnostic still goes to
  stderr, so a log/human still sees what was approved.
- **Quiet/scriptable error output still leaked the `⏺` glyph on every error path except
  `BrokenPipeError` (gh #76).** Errors already route to stderr with color stripped, but the
  `⏺` in `_status(f"{RED}⏺ Error: {e}{RESET}")` is a literal glyph, not an ANSI code, so
  `_disable_ansi()` (which blanks `RED`/`RESET`) left it in place — the #74 fix only routed
  around it for `BrokenPipeError`, so every other path (bad/missing agent spec, load failure,
  a node exception, the generic top-level handlers) still printed `⏺ Error: …`, and on a
  Windows cp1252 console the glyph became a `?`/replacement char via the `errors="replace"`
  reconfigure. `_status()` now drops a leading `⏺ ` marker in quiet mode, in one place, so
  every error/diagnostic path matches the bare `Error: …` that `print_chunk` already emits on
  its own quiet error branch — completing the #74 suppression instead of special-casing one
  exception type.

### Fixed
- **Setting the documented `DEEPAGENT_SPEC` alias emitted a deprecation notice naming
  `DEEPAGENT_AGENT_SPEC` — a variable the user never set (gh #73).** `CodeConfig.resolve()`
  reconciles the oldest alias by copying `DEEPAGENT_SPEC` onto `DEEPAGENT_AGENT_SPEC` before
  delegating to the shared resolver, so the resolver then emitted its **visible stderr
  `note:`** for the synthesized `DEEPAGENT_AGENT_SPEC` — pointing the user at a var absent
  from their environment and defeating the whole point of the nudge. The one correct signal
  (`DEEPAGENT_SPEC`) was only a `DeprecationWarning`, which the default filter swallows. The
  CLI now raises the deprecation through the shared resolver's own `_warn_legacy_env` helper
  for `DEEPAGENT_SPEC` (so the message, dedupe, `LANGSTAGE_SUPPRESS_LEGACY_NOTICE` opt-out and
  pytest suppression all match every other legacy-env notice) and marks the synthesized
  `DEEPAGENT_AGENT_SPEC` already-warned so the resolver stays silent about it. The notice now
  names the var the user actually set.
- **Scriptable/quiet output concatenated a turn's separate messages with no separator (gh
  #74).** The gh #43 node-change break — re-emitting the marker and inserting a newline when
  the streaming node changes mid-turn — landed in the verbose and non-verbose branches of
  `print_chunk()` but never covered the `_QUIET` / scriptable branch, so two `AIMessage`s from
  two nodes in one turn (a common ReAct / plan→execute shape) were emitted back-to-back
  (`…up.The capital…`) on the one output mode explicitly meant to be machine-parseable. The
  quiet branch now tracks `_streaming_node` too and emits a bare `\n` (no marker, no color)
  on a mid-turn node change; single-message token streaming still joins unbroken. Also, a
  `BrokenPipeError` from an early-closing consumer (`| head`, `| grep -m1`) on the scriptable
  path is now swallowed (the standard Python SIGPIPE recipe) and exits cleanly, instead of
  surfacing a `⏺`-decorated error line to stderr.

## 0.6.14 - 2026-07-10

### Fixed
- **The header box collapsed to a bare `╭╮`/`╰╯` under a pty that reports 0 columns (gh
  #71).** `get_terminal_width()` guarded `OSError` but not the case where
  `os.get_terminal_size()` succeeds and reports `columns == 0` — which a pty forked without
  an initialized window size (pexpect/expect automation, some CI pseudo-ttys, editor
  terminals, process supervisors) really does. `min(0, 100) == 0` made the box borders
  (`"─" * (width - 2)`) vanish while content rows overflowed. Width is now floored at 40
  (`min(max(cols, 40), 100)`), so the banner always renders.

## 0.6.13 - 2026-07-09

### Fixed
- **The HITL approval prompt always showed `1. unknown` instead of the tool name (gh #69).**
  The interrupt renderer read `action.get("tool")`, but a HumanInterrupt `action_request`
  keys the tool name under `action` (the deepagents/langchain convention), so the lookup
  always missed. That's safety-relevant — the operator couldn't see what they were about to
  approve. Now reads `action.get("action")`, falling back to `"tool"` (for agents on the
  older key) then `"unknown"`. Mirrors the langstage-vscode #44 fix.

## 0.6.12 - 2026-07-08

### Changed
- **`--show-config` and interactive `/config` now render the identical diagnostic, and a
  parity test locks it (config-diagnostic consolidation).** Both views were assembled from
  pieces (`describe()` + a separately-rendered `[configurable]` table) that could drift —
  the seam behind #64 and #66. Both now go through the single `describe(configurable=…)`
  renderer in **langstage-core 1.0.14** (now the minimum pin); the per-surface
  `_print_configurable` bolt-on is gone, and a test asserts the two views can't diverge.

### Fixed
- **`--show-config` now shows the `[configurable]` table, matching interactive `/config`
  (gh #66).** The `[configurable]` TOML table is honored at runtime (it reaches the graph's
  `config["configurable"]`, gh #57) and `/config` displays it, but `--show-config` omitted
  the section entirely — so a user parameterizing their agent via `[configurable]` (model
  name, temperature, feature flags) and running `--show-config` to confirm it took effect
  saw nothing. Both views now render the table through a shared helper, so they stay in sync.

## 0.6.10 - 2026-07-07

### Fixed
- **The interactive `/config` command now reports the true source of `workspace_root`,
  matching `--show-config` (gh #64).** `/config` re-resolved the config at display time —
  but by then startup had already called `apply_workspace()`, which self-publishes
  `LANGSTAGE_WORKSPACE_ROOT` into the process environment (ADR 0005). So `/config` saw the
  tool's own published var and reported `workspace_root`'s source as
  `[env:LANGSTAGE_WORKSPACE_ROOT]` (and swapped the value for an absolute path) even when the
  user never set it — the exact opposite of the provenance the command exists to show.
  `/config` now reuses the config diagnostic snapshotted at startup (before the self-publish),
  so it agrees with `--show-config`.

## 0.6.9 - 2026-07-06

### Changed
- **`--stream-mode` is deprecated — it was a no-op advertised as a feature (gh #62).**
  `--stream-mode {auto,updates,messages}`, `LANGSTAGE_STREAM_MODE`, and `[ui] stream_mode`
  claimed three streaming behaviors, but since the AG-UI streaming migration all three
  render identically — the setting had zero effect (the `_resolve_stream_mode` mapper was
  never called). Rather than keep advertising a dead knob, it is now: hidden from `--help`,
  omitted from `--show-config`, no longer resolved from the env var / TOML key, and
  accepted-and-ignored on the CLI (so an existing `--stream-mode X` invocation doesn't
  hard-error) with a one-line deprecation notice. No rendering behavior changes.

## 0.6.8 - 2026-07-05

### Fixed
- **The interactive prompt no longer leaks an ANSI color escape in `--quiet` / piped
  mode (gh #60).** `make_prompt`'s `color` argument defaulted to the `BRIGHT_BLUE`
  module global, bound once at function-definition time — so `_disable_ansi()`
  (which rebinds the color globals to empty strings for non-TTY / `--quiet` output)
  never reached it, and the prompt still emitted a stray `\033[94m`. The default is
  now resolved at call time, so a disabled-ANSI prompt is clean. (Thanks @JSap0914.)

## 0.6.7 - 2026-07-04

### Fixed
- **The `langstage.toml` `[configurable]` table now reaches the graph (gh #57).**
  `/config` and `--show-config` advertise that `[configurable]` seeds LangGraph's
  `RunnableConfig.configurable`, but the AG-UI streaming path forwarded only
  `thread_id` — every other key (model name, feature flags, API config, …) was
  silently dropped, so a node's `config["configurable"]` saw `None`. The session
  agent is now built with the resolved configurable (`build_session_agent(config=…)`
  → `build_agent(config=…)`), so those keys reach the graph on every turn.

## 0.6.6 - 2026-07-03

### Changed
- **Workspace root is now applied through the shared `core.apply_workspace()`
  (ADR 0005).** Replaces the local `os.chdir` block with the one source of truth:
  the resolved workspace is published (env + active) so the agent's tools can read
  `core.workspace_root()`, and cli still `chdir`s into it (it's single-process) when
  one was explicitly configured. Same observable behavior, minus the drift — plus a
  configured workspace that doesn't exist yet is now **created** (via
  `apply_workspace`'s `ensure()`) instead of silently ignored. Requires
  `langstage-core>=1.0.7`.

## 0.6.5 - 2026-07-03

### Added
- **`--verify`: preflight the configured agent with one real turn (ADR 0004).**
  `langstage-cli --verify` (with `-a`/`--demo`/`LANGSTAGE_AGENT_SPEC`) loads the
  agent, runs ONE real turn through the shared `langstage-core` primitive
  `core.verify()`, and exits **0** if it completed cleanly, **non-zero** otherwise —
  a real CI gate that catches a missing key / broken tool / bad graph *before* you
  rely on it, versus a static "it imports" check. The verdict is a single line
  (pass on stdout, failure on stderr), and a piped `--verify` auto-quiets (no
  spinner or "Loaded" line), so `langstage-cli --verify -a my_agent.py` scripts
  cleanly. First surface adoption of the consolidated preflight; "healthy" now
  means the same thing here as in the other surfaces. Requires `langstage-core>=1.0.6`.

## 0.6.4 - 2026-07-03

### Added
- **Scriptable single-shot output (gh #53).** A single-shot run (a `MESSAGE` arg
  or `-f/--file`) now emits **only the agent's reply** — no header box, welcome
  text, "Loaded" line, spinner, tool-call chatter, timing, or ANSI color — as soon
  as it's piped (stdout is not a TTY). A new `-q/--quiet` flag forces the same
  clean output in a terminal. Errors and diagnostics are routed to **stderr** so
  they never pollute the captured reply, and the existing non-zero exit on a failed
  turn (gh #47) makes `answer=$(langstage-cli --demo "hi")` safe to script. Color is
  additionally stripped whenever stdout is not a TTY, matching well-behaved CLIs.

## 0.6.3 - 2026-07-02

### Changed
- **Scrubbed the leftover `deepagent-code` branding and disambiguated from
  LangChain's `dcode` (gh #42).** The default agent display name (used when a graph
  exposes no name) is now `Agent` instead of the old product name `AgentCode`, the
  `examples/agent.py` header no longer says "deepagent-code", and the README makes
  clear `langstage-cli` is **not** LangChain's `deepagents-code` (`dcode`). The
  deprecated `deepagent-code` package + console script still work as a back-compat
  alias.

## 0.6.2 - 2026-07-02

### Fixed
- **A single-shot / `--no-interactive` run now exits non-zero when the agent errors
  (gh #47).** A framed error (e.g. a `RunErrorEvent` like a recursion-limit failure)
  was displayed but the turn still "completed", so the CLI exited 0 and a piped /
  scripted caller couldn't tell a failed run from a success. `run_single_turn_agui`
  now reports whether the turn errored and `main()` exits 1 on it. (A raised
  exception already exited 1 via `main`'s handler; this covers the framed case.)
- **Multi-node graphs no longer render as an unreadable run-on (gh #43).** Non-verbose
  streaming printed one `⏺` marker per turn and appended every chunk, so two nodes'
  messages ran together on one line. It now starts a fresh marker when the langgraph
  node changes. Requires `langstage-core >= 1.0.4`, which carries the real node name
  on each frame (it previously hardcoded `"agent"`).

## 0.6.1 - 2026-07-02

### Fixed
- **Bare `pip install langstage-cli` couldn't run a turn.** 0.6.0 made AG-UI the
  only streaming path but left the AG-UI runtime (`ag-ui-langgraph[fastapi]`) in
  the optional `[agui]` extra, so a default install hit an ImportError on the first
  message. The runtime is now a base dependency (via `langstage-core[agui]`); the
  `[agui]` extra is a redundant no-op alias.

## 0.6.0 - 2026-07-02

### Changed
- **AG-UI is now the CLI's only streaming path (ADR 0003).** Every turn streams
  through `langstage-core`'s in-process AG-UI adapter; removed the
  `stream_graph_updates` turn functions. The `--agui`/`--async`/`--stream-mode`
  flags are accepted-and-ignored for one release.
- **Repointed to `langstage-core` 1.0** (the rename of `langgraph-stream-parser`).

## 0.5.12 - 2026-06-27

### Fixed
- **Token streaming jammed the `⏺` marker before every token.** A token-streaming
  model emits one chunk per token, and `print_chunk` prefixed the cyan bullet on
  *every* streaming chunk — so a real LLM reply rendered as
  `⏺ alpha⏺  ⏺ beta⏺  ⏺ gamma` instead of `⏺ alpha beta gamma`. This hit the
  default `auto` mode (and `messages`); only `updates` (one whole-message chunk)
  escaped it. The marker is now printed once at the start of a streamed AI turn,
  reset on any non-text event (tool call, interrupt, complete) and at each turn
  start. (Found by the dogfood routine, gh #34.)

## 0.5.11 - 2026-06-26

### Fixed
- **`--no-interactive` silently abandoned interrupts instead of auto-approving.**
  The README documents `--no-interactive` as "auto-approve tool calls", but when
  an agent hit a LangGraph `interrupt()` the resume loop only resolved it in
  interactive mode — otherwise it `break`ed, dropping the interrupt and exiting 0
  with the agent's post-interrupt work never run (a silent failure for the
  automation/CI audience the flag exists for). It now auto-approves every pending
  action and resumes the graph, so the agent runs to completion. (Found by the
  dogfood routine, gh #32.)

## 0.5.10 - 2026-06-25

### Fixed
- **A relative `-a` / `LANGSTAGE_AGENT_SPEC` broke the moment `LANGSTAGE_WORKSPACE_ROOT`
  was set.** The CLI `os.chdir()`s into the workspace root *before* loading the
  agent, so a relative `my_agent.py:graph` was resolved against the workspace
  root instead of the invocation cwd — failing with `Agent file not found` for a
  file sitting right there in the current directory (both vars are documented
  side-by-side, so setting both is expected usage). The file part of a relative
  spec is now resolved to an absolute path *before* the chdir; module specs
  (`pkg.mod:attr`) and absolute paths are unaffected. (Found by the dogfood
  routine, gh #30.)

## 0.5.9 - 2026-06-22

### Fixed
- **`--stream-mode messages` rendered an empty turn for a finished `AIMessage`.**
  Single `messages` mode only carries LLM *token* streams, so an agent whose node
  returns a complete (non-token-streamed) `AIMessage` — the exact shape the
  README's "Creating Your Own Agent" example produces — rendered nothing (no
  content, no error, exit 0). The default stream mode is now **`auto`** (dual
  `updates`+`messages`): tokens stream live when the agent emits them, and the
  finished-message content renders otherwise. `updates` and `messages` remain as
  explicit single-mode choices (with `messages` documented as token-only). This
  aligns the CLI with the web/JupyterLab/VS Code surfaces, which already stream
  dual-mode. (Found by the dogfood routine.)

## 0.5.8 - 2026-06-22

### Fixed
- **`/help` leaked a literal `36m` and bled ANSI color.** The Commands block built
  each command's alias list with `f", {CYAN}/{RESET}, {CYAN}/".join([""] + aliases)[4:]`,
  whose `[4:]` slice cut into the leading `\x1b[36m` escape — printing stray `36m`
  text and leaving color unterminated. Each alias is now rendered as its own clean
  cyan `/token`. (gh #-dogfood)

## 0.5.7 - 2026-06-21

### Docs
- README no longer advertises a "default agent" command — there is no bundled
  default; `langstage-cli` with no spec errors. Quick Start / Usage now lead with
  `--demo` and `-a my_agent.py:graph`.
- "Creating Your Own Agent" gained a stdlib-only `StateGraph` example (the prior
  snippet needed `deepagents`, which isn't a base dep) and notes the deepagents
  install for the full-agent variant.
- CLI Options now list `--demo`, `--show-config`, `--version`; the interactive
  Commands list matches the in-app `/help` (`/config`, `/history`, `/reset`, Tab).

## 0.5.6 - 2026-06-21

### Fixed
- **`--stream-mode values` crashed with a fatal Python error** instead of working
  or erroring cleanly. `values` was advertised (README / `--help` / `--show-config`)
  but the render path only supports `updates`/`messages`; the resulting `ValueError`
  surfaced while the spinner **daemon thread** held stdout, producing
  `Fatal Python error: _enter_buffered_busy ... at interpreter shutdown` (exit 127).
  Now: `--stream-mode` is a `Choice {updates, messages}` (clean rejection of the
  flag), the resolved value (incl. `LANGSTAGE_STREAM_MODE`, which bypasses the flag)
  is validated up front with a readable error, and the stream loop always stops the
  spinner via `try/finally` so no streaming error can crash the interpreter. README /
  `--help` / docstring no longer list `values`.
- **Dict-form messages now render** — a `CompiledGraph` returning
  `{"role": "assistant", "content": ...}` produced no output. Fixed upstream in
  `langgraph-stream-parser` 0.6.6; the core pin is modernized from the stale
  `<0.5` to `>=0.6.6,<0.7` (base + `[agui]`) to deliver it.

## 0.5.5 - 2026-06-20

### Fixed
- **Headline `langstage-cli --demo "hello"` crashed on a default Windows console
  (cp1252) with `UnicodeEncodeError`** — the spinner's Braille frames and the
  `✓`/`⏺` status glyphs couldn't encode. `main()` now reconfigures stdio to UTF-8
  (`errors="replace"`) at entry, and the spinner thread can no longer crash the
  process on a print error.
- **`--help` showed a mojibake character** where an em-dash was in the `--demo`
  option text; replaced with plain ASCII so help renders on any console.
- **README `langstage.toml` example documented `[agent] workspace_root`**, which
  is silently ignored — the real key is `[workspace] root`. Corrected.

## 0.5.4 - 2026-06-19

### Fixed
- `langstage_cli.__version__` was a hard-coded `"0.4.0"` and had drifted (the
  package was at 0.5.3). It now derives from the installed distribution metadata
  (`importlib.metadata.version`), so it always matches `pyproject.toml` and can't
  drift again. (The `--version` flag already read metadata; this aligns the
  exported module attribute.)

## 0.5.3 - 2026-06-17

### Fixed
- `--help` and runtime hints now lead with the canonical `LANGSTAGE_*` env vars and
  `langstage.toml` (the legacy `DEEPAGENT_*` / `deepagents.toml` are noted only as
  deprecated aliases), matching the README and `--show-config` output. Previously the
  CLI's own help advertised the deprecated names as canonical and never mentioned
  `LANGSTAGE_*` (#22).

### Added
- `--version` flag (reads the version from package metadata) — was missing (#22).


## 0.5.2 - 2026-06-17

### Fixed
- `--show-config` now reflects CLI flags (#20). It resolved config WITHOUT the CLI
  overrides, so `-a/--agent`, `-v/--verbose`, `--stream-mode`, etc. showed as
  `[default]` and the reported `[source]` could contradict the real run (e.g. a
  CLI flag set alongside an env var was reported as won by the env, but the run
  used the flag). The same override dict now feeds both `--show-config` and the
  actual run, so the diagnostic matches reality — CLI-set values show as `[override]`.


## 0.5.1 - 2026-06-16

### Changed
- Moved `deepagents` from runtime dependencies to the `[dev]` extra. Only the
  bundled `examples/agent.py` (a dev sample, not shipped) imports it; the CLI never
  does. A plain `pip install langstage-cli` no longer pulls deepagents/langchain/langgraph.


All notable changes to this project will be documented in this file.

## [0.5.0] - 2026-06-14

Adopt AG-UI: widen the langgraph-stream-parser ceiling to <0.5 and add an [agui] extra so this surface's agent can be served over AG-UI via langstage-agui. Additive; no runtime changes.

## [0.4.0] - 2026-06-10

**deepagent-code is now `langstage-cli`** — the terminal stage of the LangStage family ("every stage for your LangGraph agent"). Repositioned as the generic terminal runner for *any* LangGraph `CompiledGraph` (LangChain's `dcode` owns the batteries-included coding-agent niche; this tool runs *your* agent).

### Changed
- Distribution `deepagent-code` → **`langstage-cli`**; module `deepagent_code` → **`langstage_cli`**. A deprecated alias package keeps `import deepagent_code` (and its submodules) working with a `DeprecationWarning`; the `deepagent-code` command remains as an alias of the new `langstage-cli` command.
- Canonical config vocabulary via langgraph-stream-parser 0.3: `LANGSTAGE_*` env vars, project `langstage.toml`, global `~/.langstage/config.toml`. The full legacy vocabulary (`DEEPAGENT_*`, `deepagents.toml`, `~/.deepagents/`) still resolves as a deprecated fallback — and exiting `~/.deepagents/` ends the config-schema collision with LangChain's dcode.
- Parser pinned `>=0.3,<0.4`.

## [0.3.0] - 2026-06-10

### Added
- **`--demo`** — run the CLI against the shared keyless echo stub (`langgraph_stream_parser.demo.stub:graph`): a real compiled LangGraph graph streaming through the normal parser path, with no API key and no network. Mutually exclusive with `-a/--agent`.
- **`--show-config`** — print the resolved configuration (defaults < `deepagents.toml` < `DEEPAGENT_*` env < CLI), each value with its source and the key that sets it, then exit.
- README: *One agent, every surface* family table; env-var docs now show canonical `DEEPAGENT_AGENT_SPEC` (legacy `DEEPAGENT_SPEC` remains a deprecated alias).

### Changed
- `langgraph-stream-parser` pinned `>=0.2.2,<0.3` (the release that ships the stub).

## [0.2.0] - 2026-06-02

Adopts the shared `langgraph-stream-parser` runtime and config layer.

### Changed
- Routes streaming through `langgraph-stream-parser` (`compat.stream_graph_updates` / `astream_graph_updates`) — the in-tree 654-line `utils.py` reimplementation of the stream parser is **deleted**. CLI rendering is unchanged.
- `load_graph` now delegates to `host.load_agent_spec`; the bespoke file/module loaders are gone.
- Config resolves via the new `CodeConfig(HostConfig)`: `defaults < deepagents.toml < DEEPAGENT_* env < CLI overrides`. The TOML loader is shared with the rest of the family (`host.load_toml_config`).
- **`DEEPAGENT_AGENT_SPEC` is canonical again** (reverting the 0.1.4 rename to `DEEPAGENT_SPEC`); `DEEPAGENT_SPEC` is now a deprecated alias that warns once.
- The workspace TOML key moves from `agent.workspace_root` to the shared `workspace.root`.
- `verbose` / `async_mode` flags now fall back to TOML/env when the flag isn't passed (previously the absent flag's `False` always won).

### Added
- `langgraph-stream-parser>=0.2,<0.3` dependency.
- `/config` now prints the resolved config with each value's source + env var / TOML key.

### Fixed
- `load_graph` no longer mistakes a Windows drive-letter colon (`C:\...\agent.py`) for a `:graph_name` suffix.

## [0.1.6] - 2026-04-18

### Added
- TOML configuration support:
  - Global config at `~/.deepagents/config.toml` (shared with upstream deepagents CLI)
  - Project config at `deepagents.toml` (walks up from cwd)
  - Project overrides global; CLI args and env vars override both
- `[ui]` table: `verbose`, `async_mode`, `stream_mode`
- `[agent]` table: `spec`, `graph_name`, `workspace_root`
- `[configurable]` table seeds LangGraph `RunnableConfig.configurable`
- `/config` slash command now shows resolved values and TOML source files

### Removed
- `--config` / `-c` CLI flag (use TOML files instead)
- `DEEPAGENT_CONFIG` environment variable (use TOML files instead)

### Breaking
- JSON config via `--config` / `DEEPAGENT_CONFIG` is removed. Migrate inline
  JSON into `~/.deepagents/config.toml` or a project `deepagents.toml`.

## [0.1.5] - 2026-02-02

### Added
- `-f/--file` option to read prompt from any file
- Single-shot mode: auto-exit after processing CLI message

### Changed
- MESSAGE is now positional argument (previously `-m/--message`)
- Agent spec moved to `-a/--agent` option (previously positional)

## [0.1.4] - 2026-01-17

### Added
- ASCII art rendering for agent names in header display
- Agent description display below agent name (if available)
- Bang command (`!`) prefix to execute bash commands directly, bypassing agent
- `cwd:` label before current working directory display

### Changed
- Renamed `DEEPAGENT_AGENT_SPEC` to `DEEPAGENT_SPEC` (backwards compatible)
- Improved M character in ASCII font for better distinction from N

### Fixed
- ASCII art alignment issues for characters with trailing spaces (T, P, etc.)

## [0.1.3] - 2026-01-15

### Fixed
- Line wrapping issues on Windows terminals when entering long user messages
- Proper readline escape sequences for ANSI codes in input prompts

## [0.1.2] - 2026-01-13

### Added
- Slash command system with registry for extensible commands
- Tab completion for slash commands using readline
- Command suggestions for unknown commands ("Did you mean: /help?")
- New commands: /status, /config, /version, /history, /reset, /verbose
- Cross-platform keyboard input support (Windows msvcrt, Unix termios)

### Changed
- Refactored command handling to use centralized registry
- Improved help display to dynamically list all registered commands
- Removed emoji tool icons for cleaner, more professional output

## [0.1.1] - 2026-01-12

### Added
- Elapsed time display in spinner while model is thinking
- Response time tracking and display after each response
- Verbose mode shows detailed timing information

### Changed
- Updated copyright and license information for deepagent-code
- Added cover image to README

### Fixed
- License format updated to SPDX expression for PyPI compatibility
- Removed deprecated license classifier

## [0.1.0] - 2026-01-12

### Added
- Initial release of deepagent-code
- Claude Code-style CLI for running LangGraph agents
- Interactive conversation loop with agent
- Support for loading agents from file paths or module paths
- Spinner animation while waiting for responses
- Tool call visualization with previews
- Interrupt handling with interactive approval
- Environment variable configuration support
- Support for async and sync streaming modes
- Default agent with bash tool integration
- uvx tool support for running without installation
