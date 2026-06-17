# Changelog

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
