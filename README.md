# deepagent-code

A Claude Code-style CLI for running LangGraph agents from the terminal.

![deepagent-code](examples/image.png)

## Installation

```bash
pip install deepagent-code
```

Or install directly from GitHub:
```bash
pip install git+https://github.com/dkedar7/deepagent-code.git
```

## Quick Start

Run with the default agent (requires `ANTHROPIC_API_KEY`):
```bash
export ANTHROPIC_API_KEY="your_api_key"
deepagent-code
```

Or specify your own agent:
```bash
deepagent-code -a path/to/your_agent.py:graph
```

This launches an interactive conversation loop with your agent.

## Usage

```bash
# Use the default agent
deepagent-code

# Send a message directly
deepagent-code "Hello, agent!"

# Specify a custom agent file
deepagent-code -a my_agent.py:graph

# Use a module path
deepagent-code -a mypackage.agents:chatbot

# Read message from a file
deepagent-code -f ./prompt.md

# Non-interactive mode (auto-approve tool calls)
deepagent-code --no-interactive

# Verbose output
deepagent-code -v
```

## Commands

In the interactive loop:
- `/q` or `/quit` - Exit
- `/c` - Clear conversation history
- `/h` or `/help` - Show help

## Environment Variables

```bash
# Agent location (path/to/file.py:variable_name or module:variable)
export DEEPAGENT_SPEC="my_agent.py:graph"
deepagent-code

# Working directory
export DEEPAGENT_WORKSPACE_ROOT="/path/to/workspace"

# Stream mode (updates or values)
export DEEPAGENT_STREAM_MODE="updates"
```

## Configuration Files

`deepagent-code` reads TOML config from two locations and merges them
(project overrides global):

- **Global**: `~/.deepagents/config.toml` (shared with the upstream
  `deepagents` CLI)
- **Project**: `deepagents.toml` in the current directory or any ancestor

Precedence: CLI args > env vars > project TOML > global TOML > defaults.

Example `deepagents.toml`:

```toml
[agent]
spec = "my_agent.py:graph"
workspace_root = "."

[ui]
verbose = true
async_mode = false
stream_mode = "updates"

[configurable]
# seeds LangGraph RunnableConfig.configurable
thread_id = "my-thread"
```

## CLI Options

```
Usage: deepagent-code [OPTIONS] [MESSAGE]

Arguments:
  MESSAGE  Optional input to send to the agent immediately

Options:
  -a, --agent TEXT                Agent spec (path/to/file.py:graph or module:graph)
  -g, --graph-name TEXT           Graph variable name (default: "graph")
  -f, --file PATH                 Read message from a file (any extension)
  --interactive/--no-interactive  Handle interrupts (default: interactive)
  --async-mode/--sync-mode        Async streaming (default: sync)
  --stream-mode TEXT              Stream mode (updates or values)
  -v, --verbose                   Verbose output
```

## Creating Your Own Agent

Your agent file should export a compiled LangGraph graph:

```python
# my_agent.py
from deepagents import create_deep_agent
from langgraph.checkpoint.memory import MemorySaver

agent = create_deep_agent(
    name="My Agent",
    model="anthropic:claude-sonnet-4-20250514",
    checkpointer=MemorySaver(),
)
```

Then run it:
```bash
deepagent-code -a my_agent.py:agent
```

## Programmatic Use

```python
from deepagent_code import stream_graph_updates, prepare_agent_input

input_data = prepare_agent_input(message="Hello!")

for chunk in stream_graph_updates(graph, input_data):
    if chunk.get("chunk"):
        print(chunk["chunk"], end="")
```

## License

MIT License - see LICENSE file for details.
