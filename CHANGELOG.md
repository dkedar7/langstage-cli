# Changelog

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
