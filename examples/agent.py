"""
Default agent configuration for deepagent-code.

This agent is used when no custom agent is specified. It provides basic
conversation capabilities with filesystem access and bash command execution.
"""

import os
import subprocess

from dotenv import load_dotenv

load_dotenv()

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langgraph.checkpoint.memory import MemorySaver


backend = FilesystemBackend(root_dir=os.getcwd(), virtual_mode=True)


def bash(command: str):
    """Execute a bash command and return the output.

    Args:
        command (str): The bash command to execute.
    """
    result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
    return (result.stdout + result.stderr).strip() or "(empty)"


# Create agent with configuration
agent = create_deep_agent(
    name="Default Agent",
    model="anthropic:claude-sonnet-4-20250514",
    backend=backend,
    checkpointer=MemorySaver(),
    tools=[bash],
    interrupt_on=dict(bash=True),
)
agent.description = "A helpful assistant that can read and write files, and execute bash commands."
