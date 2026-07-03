# langstage-cli

**The terminal stage for your LangGraph agent.** A Claude Code-style CLI that runs *any* LangGraph `CompiledGraph` — yours, not a bundled one — with streaming, tool-call rendering, and human-in-the-loop approval.

> Renamed from **deepagent-code** (the old package name now just installs this one, and the `deepagent-code` command still works). Not to be confused with LangChain's **`deepagents-code`** (`dcode`) — that's a separate project; `langstage-cli` is the terminal stage of the [LangStage family](#every-stage-for-your-langgraph-agent).

![langstage-cli](examples/image.png)

## Every stage for your LangGraph agent

langstage-cli is the terminal stage of the **LangStage family**: write your agent once — any LangGraph `CompiledGraph` — and run it on every stage with the same spec string (`module:attr` or `path/to/file.py:attr`), the same `langstage.toml` config file, and the same `LANGSTAGE_*` environment variables.

| Stage | Package | Try it |
|---|---|---|
| Web app | [langstage](https://github.com/dkedar7/langstage) | `langstage run --agent my_agent.py:graph` |
| JupyterLab | [langstage-jupyter](https://github.com/dkedar7/langstage-jupyter) | `pip install langstage-jupyter`, then the chat sidebar in `jupyter lab` |
| Terminal | langstage-cli | **you are here** |
| VS Code | [langstage-vscode](https://github.com/dkedar7/langstage-vscode) | chat participant + stdio sidecar |
| Reference agent | [langstage-hermes](https://github.com/dkedar7/langstage-hermes) | `LANGSTAGE_AGENT_SPEC=langstage_hermes.agent:graph` on any stage |
| Shared core | [langstage-core](https://github.com/dkedar7/langstage-core) | typed events + config resolver behind every stage |

📖 **Full documentation:** <https://dkedar7.github.io/langstage-docs/>

### Serve over AG-UI

This surface's agent — any LangGraph `CompiledGraph` — can also be served over the [AG-UI protocol](https://github.com/dkedar7/langstage-core) as a standalone HTTP endpoint:

```bash
pip install "langstage-core[agui]"
langstage-agui --agent my_agent.py:graph
```

## Installation

```bash
pip install langstage-cli
```

Or install directly from GitHub:
```bash
pip install git+https://github.com/dkedar7/langstage-cli.git
```

## Quick Start

No agent or API key yet? See the CLI working in one command:
```bash
langstage-cli --demo "hello"
```

Point it at your own agent (any LangGraph `CompiledGraph`):
```bash
export ANTHROPIC_API_KEY="your_api_key"   # if your agent calls Anthropic
langstage-cli -a path/to/your_agent.py:graph
```

This launches an interactive conversation loop with your agent.

## Usage

```bash
# Keyless demo agent — no API key, no agent of your own
langstage-cli --demo "Hello"

# Send a message directly to your agent
langstage-cli -a my_agent.py:graph "Hello, agent!"

# Specify a custom agent file
langstage-cli -a my_agent.py:graph

# Use a module path
langstage-cli -a mypackage.agents:chatbot

# Read message from a file
langstage-cli -f ./prompt.md

# Non-interactive mode (auto-approve tool calls)
langstage-cli --no-interactive

# Verbose output
langstage-cli -v

# Keyless demo agent (no API key needed)
langstage-cli --demo

# Print the resolved configuration: each value, its source, and the
# env var / langstage.toml key that sets it
langstage-cli --show-config
```

## Commands

In the interactive loop:
- `/quit` (`/q`, `/exit`) - Exit
- `/clear` (`/c`) - Clear conversation history
- `/reset` - Reset the session
- `/config` (`/cfg`) - Show the resolved configuration
- `/history` (`/hist`) - Show conversation history
- `/help` (`/h`, `/?`) - Show help
- **Tab** autocompletes commands; **Ctrl+C** exits

## Environment Variables

```bash
# Agent location (path/to/file.py:variable_name or module:variable)
# (DEEPAGENT_AGENT_SPEC / DEEPAGENT_SPEC still accepted as deprecated aliases)
export LANGSTAGE_AGENT_SPEC="my_agent.py:graph"
langstage-cli

# Working directory
export LANGSTAGE_WORKSPACE_ROOT="/path/to/workspace"

# Stream mode (updates or messages)
export LANGSTAGE_STREAM_MODE="updates"
```

## Configuration Files

`langstage-cli` reads TOML config from two locations and merges them
(project overrides global):

- **Global**: `~/.langstage/config.toml`
- **Project**: `langstage.toml` in the current directory or any ancestor

Legacy locations (`~/.deepagents/config.toml`, `deepagents.toml`) are still
read as fallbacks; move your config when convenient — `~/.deepagents/` now
belongs to LangChain's `dcode`.

Precedence: CLI args > env vars > project TOML > global TOML > defaults.

Example `langstage.toml`:

```toml
[agent]
spec = "my_agent.py:graph"

[workspace]
root = "."

[ui]
verbose = true
async_mode = false
stream_mode = "auto"   # auto | updates | messages

[configurable]
# seeds LangGraph RunnableConfig.configurable
thread_id = "my-thread"
```

## CLI Options

```
Usage: langstage-cli [OPTIONS] [MESSAGE]

Arguments:
  MESSAGE  Optional input to send to the agent immediately

Options:
  -a, --agent TEXT                Agent spec (path/to/file.py:graph or module:graph)
  -g, --graph-name TEXT           Graph variable name (default: "graph")
  -f, --file PATH                 Read message from a file (any extension)
  --interactive/--no-interactive  Handle interrupts (default: interactive)
  --async-mode/--sync-mode        Async streaming (default: sync)
  --stream-mode [auto|updates|messages]
                                  Stream mode (default: auto)
  -v, --verbose                   Verbose output
  --demo                          Run with the built-in keyless demo agent
  --show-config                   Print the resolved configuration and exit
  --version                       Show the version and exit
```

## Creating Your Own Agent

Your agent file just needs to export a compiled LangGraph graph — `langstage-cli`
runs **any** `CompiledGraph`. A minimal stdlib example (no extra deps):

```python
# my_agent.py — needs only langgraph (a base dependency)
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import MessagesState
from langchain_core.messages import AIMessage

def respond(state):
    last = state["messages"][-1].content
    return {"messages": [AIMessage(content=f"You said: {last}")]}

g = StateGraph(MessagesState)
g.add_node("respond", respond)
g.add_edge(START, "respond")
g.add_edge("respond", END)
graph = g.compile()
```

Or a full deep agent (requires `pip install deepagents`):

```python
# my_agent.py
from deepagents import create_deep_agent
from langgraph.checkpoint.memory import MemorySaver

agent = create_deep_agent(
    name="My Agent",
    model="anthropic:claude-sonnet-4-6",
    checkpointer=MemorySaver(),
)
```

Then run it:
```bash
langstage-cli -a my_agent.py:graph    # or :agent for the deepagents example
```

## Programmatic Use

Since 1.0, streaming runs through the shared core's in-process AG-UI adapter
(`pip install "langstage-core[agui]"`):

```python
import asyncio
from langstage_core import load_agent_spec
from langstage_core.agui import build_agent, iter_chunk_frames

agent = build_agent(load_agent_spec("my_agent.py:graph"))

async def main():
    async for chunk in iter_chunk_frames(agent, "Hello!", thread_id="s1"):
        if chunk.get("chunk"):
            print(chunk["chunk"], end="")

asyncio.run(main())
```

## License

MIT License - see LICENSE file for details.
