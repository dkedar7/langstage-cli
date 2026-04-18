# Changelog

All notable changes to this project will be documented in this file.

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
