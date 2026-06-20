"""
CLI for running arbitrary LangGraph agents from the terminal.
Styled after Claude Code / nanocode.
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click

from langgraph_stream_parser import (
    prepare_agent_input,
    stream_graph_updates,
    astream_graph_updates,
    load_agent_spec,
)
from langstage_cli import config as config_module

# Platform-specific imports for keyboard input
IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    import msvcrt
else:
    import termios
    import tty

# Try to import readline for tab completion (not available on all platforms)
try:
    import readline

    HAS_READLINE = True
except ImportError:
    HAS_READLINE = False


# ANSI color codes (matching nanocode style)
RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
ITALIC, UNDERLINE = "\033[3m", "\033[4m"
BLUE, CYAN, GREEN, YELLOW, RED = "\033[34m", "\033[36m", "\033[32m", "\033[33m", "\033[31m"
MAGENTA, WHITE, GRAY = "\033[35m", "\033[37m", "\033[90m"

# Bright variants for gradient effects
BRIGHT_CYAN, BRIGHT_BLUE = "\033[96m", "\033[94m"
BRIGHT_GREEN, BRIGHT_YELLOW = "\033[92m", "\033[93m"

# Spinner frames for thinking animation
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


# Version info — read from package metadata so it never drifts from pyproject.
try:
    __version__ = _pkg_version("langstage-cli")
except PackageNotFoundError:  # pragma: no cover - editable/source checkout
    __version__ = "0.0.0+local"


# Slash command registry
class SlashCommand:
    """Represents a slash command with its handler and metadata."""

    def __init__(
        self,
        name: str,
        handler: callable,
        description: str,
        aliases: Optional[List[str]] = None,
        usage: Optional[str] = None,
    ):
        self.name = name
        self.handler = handler
        self.description = description
        self.aliases = aliases or []
        self.usage = usage or f"/{name}"

    def execute(self, args: str, context: Dict[str, Any]) -> Optional[str]:
        """Execute the command with given arguments and context."""
        return self.handler(args, context)


class CommandRegistry:
    """Registry for slash commands."""

    def __init__(self):
        self._commands: Dict[str, SlashCommand] = {}
        self._alias_map: Dict[str, str] = {}

    def register(self, command: SlashCommand):
        """Register a slash command."""
        self._commands[command.name] = command
        for alias in command.aliases:
            self._alias_map[alias] = command.name

    def get(self, name: str) -> Optional[SlashCommand]:
        """Get a command by name or alias."""
        # Check if it's an alias
        if name in self._alias_map:
            name = self._alias_map[name]
        return self._commands.get(name)

    def all_commands(self) -> List[SlashCommand]:
        """Get all registered commands."""
        return list(self._commands.values())

    def parse_input(self, user_input: str) -> Tuple[Optional[str], str]:
        """Parse user input to extract command name and arguments.

        Returns:
            Tuple of (command_name, arguments) or (None, original_input) if not a command
        """
        if not user_input.startswith("/"):
            return None, user_input

        # Split into command and args
        parts = user_input[1:].split(maxsplit=1)
        cmd_name = parts[0].lower() if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        return cmd_name, args


# Global command registry
command_registry = CommandRegistry()


def rl_wrap(code: str) -> str:
    """Wrap ANSI escape code for readline to ignore in length calculations.

    On terminals, ANSI codes are invisible but counted in string length.
    This causes issues with line wrapping when using input().
    Wrapping with \\001 and \\002 tells readline to ignore these characters.
    """
    if HAS_READLINE:
        return f"\001{code}\002"
    return code


def make_prompt(text: str = "❯", color: str = BRIGHT_BLUE) -> str:
    """Create a prompt string with proper readline escaping for ANSI codes.

    This prevents line wrapping issues on Windows and other terminals.
    """
    return f"{rl_wrap(BOLD)}{rl_wrap(color)}{text}{rl_wrap(RESET)} "


def register_command(
    name: str,
    description: str,
    aliases: Optional[List[str]] = None,
    usage: Optional[str] = None,
):
    """Decorator to register a slash command handler."""

    def decorator(func):
        command = SlashCommand(
            name=name,
            handler=func,
            description=description,
            aliases=aliases or [],
            usage=usage,
        )
        command_registry.register(command)
        return func

    return decorator


class Spinner:
    """A simple terminal spinner for showing activity with elapsed time."""

    def __init__(self, message: str = "Thinking"):
        self.message = message
        self.running = False
        self.thread = None
        self.frame_idx = 0
        self.start_time = None

    def _spin(self):
        """Run the spinner animation with elapsed time display."""
        while self.running:
            frame = SPINNER_FRAMES[self.frame_idx % len(SPINNER_FRAMES)]
            elapsed = time.time() - self.start_time
            elapsed_str = f"{int(elapsed)}s"
            # Never let a stdout hiccup in this daemon thread crash the run.
            try:
                print(
                    f"\r{CYAN}{frame}{RESET} {DIM}{self.message}... {elapsed_str}{RESET}",
                    end="",
                    flush=True,
                )
            except (UnicodeEncodeError, ValueError):
                pass
            self.frame_idx += 1
            time.sleep(0.08)

    def start(self):
        """Start the spinner."""
        self.running = True
        self.start_time = time.time()
        self.thread = threading.Thread(target=self._spin, daemon=True)
        self.thread.start()

    def stop(self):
        """Stop the spinner and clear the line."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=0.2)
        # Clear the spinner line
        print("\r\033[2K", end="", flush=True)


def get_terminal_width() -> int:
    """Get terminal width, capped at 100 for readability."""
    try:
        return min(os.get_terminal_size().columns, 100)
    except OSError:
        return 80


def separator(style: str = "light") -> str:
    """Return a styled separator line.

    Args:
        style: 'light' for thin line, 'heavy' for thick line, 'dots' for dotted
    """
    width = get_terminal_width()
    if style == "heavy":
        return f"{DIM}{'━' * width}{RESET}"
    elif style == "dots":
        return f"{DIM}{'·' * width}{RESET}"
    else:
        return f"{DIM}{'─' * width}{RESET}"


def print_welcome():
    """Print a welcome message with tips."""
    tips = [
        f"Type {CYAN}/help{RESET} for commands",
        f"Use {CYAN}/c{RESET} to clear conversation",
        f"Press {CYAN}Ctrl+C{RESET} to exit",
        f"Press {CYAN}Tab{RESET} to autocomplete commands",
    ]
    tip = tips[int(time.time()) % len(tips)]  # Rotate tips
    print(f"\n{DIM}Tip: {tip}{RESET}\n")


def print_goodbye():
    """Print a goodbye message."""
    print(f"\n{DIM}Goodbye!{RESET}\n")


def get_agent_name(graph) -> str:
    """Extract agent name from graph object, defaulting to 'AgentCode'."""
    # Try common attribute names for agent/graph name
    for attr in ("name", "agent_name", "_name", "__name__"):
        if hasattr(graph, attr):
            name = getattr(graph, attr)
            if name and isinstance(name, str):
                return name
    # Check if it's a compiled graph with a name in builder
    if hasattr(graph, "builder") and hasattr(graph.builder, "name"):
        name = graph.builder.name
        if name and isinstance(name, str):
            return name
    return "AgentCode"


def get_agent_description(graph) -> Optional[str]:
    """Extract agent description from graph object, if available."""
    # Try common attribute names for agent description
    for attr in ("description", "agent_description", "_description", "__doc__"):
        if hasattr(graph, attr):
            desc = getattr(graph, attr)
            if desc and isinstance(desc, str) and desc.strip():
                return desc.strip()
    # Check if it's a compiled graph with a description in builder
    if hasattr(graph, "builder") and hasattr(graph.builder, "description"):
        desc = graph.builder.description
        if desc and isinstance(desc, str) and desc.strip():
            return desc.strip()
    return None


def text_to_ascii_art(text: str) -> List[str]:
    """Convert text to ASCII art using a clean block font.

    Returns a list of strings, one per line of the ASCII art.
    All characters are exactly 3 chars wide for consistent spacing.
    """
    # Clean 3-line block font - each char is exactly 3 wide
    FONT = {
        "A": ["▄▀▄", "█▀█", "▀ ▀"],
        "B": ["█▀▄", "█▀▄", "▀▀▀"],
        "C": ["▄▀▀", "█  ", "▀▀▀"],
        "D": ["█▀▄", "█ █", "▀▀▀"],
        "E": ["█▀▀", "█▀▀", "▀▀▀"],
        "F": ["█▀▀", "█▀▀", "▀  "],
        "G": ["▄▀▀", "█▀█", "▀▀▀"],
        "H": ["█ █", "█▀█", "▀ ▀"],
        "I": ["▀█▀", " █ ", "▀▀▀"],
        "J": ["▀▀█", "  █", "▀▀▀"],
        "K": ["█ █", "█▀▄", "▀ ▀"],
        "L": ["█  ", "█  ", "▀▀▀"],
        "M": ["█▄█", "█ █", "▀ ▀"],
        "N": ["█▀█", "█ █", "▀ ▀"],
        "O": ["▄▀▄", "█ █", "▀▀▀"],
        "P": ["█▀▄", "█▀▀", "▀  "],
        "Q": ["▄▀▄", "█ █", "▀▀█"],
        "R": ["█▀▄", "█▀▄", "▀ ▀"],
        "S": ["▄▀▀", "▀▀▄", "▀▀▀"],
        "T": ["▀█▀", " █ ", " ▀ "],
        "U": ["█ █", "█ █", "▀▀▀"],
        "V": ["█ █", "█ █", " ▀ "],
        "W": ["█ █", "█▀█", "▀ ▀"],
        "X": ["▀▄▀", " █ ", "▀ ▀"],
        "Y": ["█ █", " █ ", " ▀ "],
        "Z": ["▀▀█", " █ ", "█▀▀"],
        "0": ["▄▀▄", "█ █", "▀▀▀"],
        "1": ["▄█ ", " █ ", "▀▀▀"],
        "2": ["▀▀█", "▄▀▀", "▀▀▀"],
        "3": ["▀▀█", " ▀█", "▀▀▀"],
        "4": ["█ █", "▀▀█", "  ▀"],
        "5": ["█▀▀", "▀▀▄", "▀▀▀"],
        "6": ["▄▀▀", "█▀█", "▀▀▀"],
        "7": ["▀▀█", "  █", "  ▀"],
        "8": ["▄▀▄", "█▀█", "▀▀▀"],
        "9": ["▄▀█", "▀▀█", "▀▀▀"],
        " ": ["   ", "   ", "   "],
        "-": ["   ", "▀▀▀", "   "],
        "_": ["   ", "   ", "▀▀▀"],
        ".": ["   ", "   ", " ▀ "],
    }

    # Default char for unknown characters
    DEFAULT = ["   ", " █ ", "   "]

    lines = ["", "", ""]
    for char in text.upper():
        char_art = FONT.get(char, DEFAULT)
        for i in range(3):
            lines[i] += char_art[i] + " "

    # Remove only the final trailing space we added (not internal spaces from chars like T, P)
    return [line[:-1] if line.endswith(" ") else line for line in lines]


def print_header_box(agent_name: str, cwd: str, description: Optional[str] = None):
    """Print an elegant header with ASCII art agent name, optional description, and cwd."""
    term_width = get_terminal_width()

    # Box drawing characters
    TL, TR, BL, BR = "╭", "╮", "╰", "╯"  # corners
    H, V = "─", "│"  # horizontal and vertical

    # Calculate inner width (accounting for borders and padding)
    inner_width = term_width - 4  # 2 for borders, 2 for padding

    # Generate ASCII art for agent name
    ascii_lines = text_to_ascii_art(agent_name)
    ascii_width = max(len(line) for line in ascii_lines) if ascii_lines else 0

    # Use ASCII art if it fits in terminal width
    use_ascii = ascii_width <= inner_width

    # Build cwd line with label
    cwd_label = "cwd: "
    max_cwd_len = inner_width - len(cwd_label)
    cwd_display = cwd if len(cwd) <= max_cwd_len else "..." + cwd[-(max_cwd_len - 3) :]
    cwd_with_label = f"{cwd_label}{cwd_display}"
    cwd_line = cwd_with_label.center(inner_width)

    # Print the box with gradient-style coloring
    print()
    print(f"{BRIGHT_CYAN}{TL}{H * (term_width - 2)}{TR}{RESET}")

    if use_ascii:
        # Print ASCII art lines centered
        for line in ascii_lines:
            centered_line = line.center(inner_width)
            print(
                f"{BRIGHT_CYAN}{V}{RESET} {BOLD}{BRIGHT_CYAN}{centered_line}{RESET} {BRIGHT_CYAN}{V}{RESET}"
            )
    else:
        # Fall back to plain text if ASCII art doesn't fit
        title_line = agent_name.center(inner_width)
        print(
            f"{BRIGHT_CYAN}{V}{RESET} {BOLD}{BRIGHT_CYAN}{title_line}{RESET} {BRIGHT_CYAN}{V}{RESET}"
        )

    # Print description line if available
    if description:
        # Truncate description if too long
        desc_display = (
            description
            if len(description) <= inner_width
            else description[: inner_width - 3] + "..."
        )
        desc_line = desc_display.center(inner_width)
        print(f"{CYAN}{V}{RESET} {DIM}{ITALIC}{desc_line}{RESET} {CYAN}{V}{RESET}")

    print(f"{CYAN}{V}{RESET} {DIM}{cwd_line}{RESET} {CYAN}{V}{RESET}")
    print(f"{CYAN}{BL}{H * (term_width - 2)}{BR}{RESET}")


def render_markdown(text: str) -> str:
    """Render markdown formatting for terminal display.

    Supports: **bold**, *italic*, `code`, [links](url)
    """
    # Bold: **text**
    text = re.sub(r"\*\*(.+?)\*\*", f"{BOLD}\\1{RESET}", text)
    # Italic: *text* (but not inside **)
    text = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", f"{ITALIC}\\1{RESET}", text)
    # Inline code: `code`
    text = re.sub(r"`([^`]+?)`", f"{CYAN}\\1{RESET}", text)
    # Links: [text](url) - show text in underline
    text = re.sub(r"\[([^\]]+?)\]\([^)]+?\)", f"{UNDERLINE}\\1{RESET}", text)
    return text


def parse_agent_spec(agent_spec: str) -> Tuple[str, str]:
    """
    Parse agent spec format: path/to/file.py:variable_name.

    Args:
        agent_spec: Agent specification string

    Returns:
        Tuple of (file_path, variable_name)

    Raises:
        ValueError: If format is invalid
    """
    if ":" not in agent_spec:
        raise ValueError(
            f"Invalid agent spec format: '{agent_spec}'. "
            f"Expected format: 'path/to/file.py:variable_name'"
        )

    parts = agent_spec.rsplit(":", 1)
    file_path = parts[0]
    variable_name = parts[1]

    if not file_path.endswith(".py"):
        raise ValueError(f"Agent spec file must be a .py file: {file_path}")

    return file_path, variable_name


def load_graph(spec: str, default_graph_name: str = "graph"):
    """
    Load a graph from either a file path or module path.

    Delegates the actual import to the shared
    ``langgraph_stream_parser.host.load_agent_spec`` loader, while preserving
    this CLI's convenience of a bare path (no ``:name``), which defaults to
    ``default_graph_name``.

    Supports formats:
        - path/to/file.py (uses default_graph_name)
        - path/to/file.py:graph_name
        - package.module (uses default_graph_name)
        - package.module:graph_name

    Args:
        spec: File path or module path, optionally with :graph_name suffix
        default_graph_name: Graph name to use if not specified in spec

    Returns:
        Tuple of (graph, graph_name).
    """
    path_or_module = spec
    graph_name = default_graph_name
    if ":" in spec:
        head, _, tail = spec.rpartition(":")
        # Only treat the trailing ':token' as a graph name if it looks like one
        # — i.e. it has no path separators. This avoids mistaking a Windows
        # drive-letter colon (e.g. 'C:\path\agent.py') for a name suffix.
        if tail and "/" not in tail and "\\" not in tail:
            path_or_module = head
            graph_name = tail or default_graph_name

    graph = load_agent_spec(f"{path_or_module}:{graph_name}")
    return graph, graph_name


def get_tool_arg_preview(args: Dict[str, Any]) -> str:
    """Get a preview of the first argument value (nanocode style)."""
    if not args:
        return ""
    # Get first value
    first_val = str(list(args.values())[0])
    # Truncate if needed
    if len(first_val) > 50:
        return first_val[:50] + "..."
    return first_val


def format_result_preview(result: str) -> str:
    """Format a result preview with line count indicator."""
    if not result:
        return "(empty)"
    lines = result.split("\n")
    preview = lines[0][:60]
    if len(lines) > 1:
        preview += f" ... +{len(lines) - 1} lines"
    elif len(lines[0]) > 60:
        preview += "..."
    return preview


def format_duration(seconds: float) -> str:
    """Format duration in human-readable format."""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    elif seconds < 60:
        return f"{seconds:.1f}s"
    else:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m {secs:.1f}s"


def print_timing(duration: float, verbose: bool = False):
    """Print response timing information."""
    formatted = format_duration(duration)
    if verbose:
        print(f"\n{DIM}Response time: {formatted}{RESET}")
    else:
        print(f"\n{DIM}{formatted}{RESET}")


def print_chunk(chunk: Dict[str, Any], verbose: bool = False):
    """
    Pretty print a chunk from the stream using Claude Code styling.

    Args:
        chunk: The chunk dictionary
        verbose: Whether to show verbose output
    """
    status = chunk.get("status")

    if status == "streaming":
        # Handle text chunks - cyan bullet with text
        if "chunk" in chunk:
            text = chunk["chunk"]
            node = chunk.get("node", "unknown")
            if verbose:
                print(f"{DIM}[{node}]{RESET} {text}", end="")
            else:
                # Print text output with cyan bullet
                print(f"{CYAN}⏺{RESET} {render_markdown(text)}", end="")

        # Handle tool calls - green tool name
        elif "tool_calls" in chunk:
            for tool_call in chunk["tool_calls"]:
                tool_name = tool_call["name"]
                args = tool_call.get("args", {})
                arg_preview = get_tool_arg_preview(args)

                print(f"\n{GREEN}● {tool_name}{RESET}")
                if arg_preview:
                    print(f"  {DIM}└─ {arg_preview}{RESET}")

        # Handle tool results - indented with result preview
        elif "tool_result" in chunk:
            result = chunk.get("tool_result", "")
            preview = format_result_preview(str(result))
            print(f"  {DIM}   ↳ {preview}{RESET}")

    elif status == "interrupt":
        interrupt_data = chunk.get("interrupt", {})
        action_requests = interrupt_data.get("action_requests", [])

        print(f"\n{YELLOW}⚠ Action Required{RESET}")
        if action_requests:
            for i, action in enumerate(action_requests):
                tool = action.get("tool", "unknown")
                args_preview = get_tool_arg_preview(action.get("args", {}))
                print(f"  {DIM}{i + 1}. {tool}{RESET}")
                if args_preview:
                    print(f"     {DIM}└─ {args_preview}{RESET}")

    elif status == "complete":
        pass  # No output on complete (nanocode style)

    elif status == "error":
        error_msg = chunk.get("error", "Unknown error")
        print(f"\n{RED}✗ Error: {error_msg}{RESET}")


def get_key() -> str:
    """Read a single keypress from stdin (cross-platform)."""
    if IS_WINDOWS:
        # Windows implementation using msvcrt
        ch = msvcrt.getch()
        if ch in (b"\x00", b"\xe0"):  # Special keys (arrows, function keys)
            ch2 = msvcrt.getch()
            if ch2 == b"H":
                return "up"
            elif ch2 == b"P":
                return "down"
            return ch2.decode("utf-8", errors="ignore")
        elif ch == b"\r":
            return "enter"
        elif ch == b"\x03":  # Ctrl+C
            return "ctrl-c"
        return ch.decode("utf-8", errors="ignore")
    else:
        # Unix implementation using termios/tty
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            # Handle escape sequences (arrow keys)
            if ch == "\x1b":
                ch2 = sys.stdin.read(1)
                if ch2 == "[":
                    ch3 = sys.stdin.read(1)
                    if ch3 == "A":
                        return "up"
                    elif ch3 == "B":
                        return "down"
            elif ch == "\r" or ch == "\n":
                return "enter"
            elif ch == "\x03":  # Ctrl+C
                return "ctrl-c"
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def select_option(options: List[str], prompt: str = "Select an option:") -> int:
    """
    Interactive option selector using arrow keys.

    Args:
        options: List of option strings to display
        prompt: Prompt to show above options

    Returns:
        Index of selected option (0-based)
    """
    selected = 0
    num_options = len(options)

    # Hide cursor
    print("\033[?25l", end="")

    try:
        print(f"\n{BOLD}{prompt}{RESET}")

        # Print initial options
        for i, opt in enumerate(options):
            if i == selected:
                print(f"  {CYAN}❯ {opt}{RESET}")
            else:
                print(f"    {DIM}{opt}{RESET}")

        while True:
            key = get_key()

            if key == "up" and selected > 0:
                selected -= 1
            elif key == "down" and selected < num_options - 1:
                selected += 1
            elif key == "enter":
                break
            elif key == "ctrl-c":
                print("\033[?25h", end="")  # Show cursor
                sys.exit(0)

            # Move cursor up to redraw options
            print(f"\033[{num_options}A", end="")

            # Redraw options
            for i, opt in enumerate(options):
                # Clear line and print option
                print("\033[2K", end="")  # Clear line
                if i == selected:
                    print(f"  {CYAN}❯ {opt}{RESET}")
                else:
                    print(f"    {DIM}{opt}{RESET}")

        return selected
    finally:
        # Show cursor
        print("\033[?25h", end="")


def handle_interrupt_input(num_actions: int = 1) -> List[Dict[str, Any]]:
    """
    Handle user input for interrupt decisions using arrow key navigation.

    Args:
        num_actions: Number of pending tool calls that need decisions

    Returns:
        List of decision objects (one for each pending action)
    """
    options = [
        "Approve all actions",
        "Reject all actions",
        "Provide custom decision (JSON)",
        "Exit",
    ]

    choice = select_option(options, "How would you like to proceed?")

    if choice == 0:
        # Return approve decision for each pending action
        return [{"type": "approve"} for _ in range(num_actions)]
    elif choice == 1:
        # Return reject decision for each pending action
        return [{"type": "reject"} for _ in range(num_actions)]
    elif choice == 2:
        print("Enter your decision as JSON (will be applied to all actions):")
        json_str = input(make_prompt("❯", BLUE)).strip()
        try:
            decision = json.loads(json_str)
            return [decision for _ in range(num_actions)]
        except json.JSONDecodeError as e:
            print(f"{RED}⏺ Invalid JSON: {e}{RESET}")
            return [{"type": "reject"} for _ in range(num_actions)]
    else:
        sys.exit(0)


async def run_single_turn_async(
    graph,
    message: str,
    config: Optional[Dict[str, Any]] = None,
    interactive: bool = True,
    verbose: bool = False,
    stream_mode: str = "updates",
) -> float:
    """Run a single turn of an async LangGraph graph. Returns total duration in seconds."""
    input_data = prepare_agent_input(message=message)
    start_time = time.time()

    while True:
        has_interrupt = False
        num_pending_actions = 0
        first_chunk = True
        spinner = Spinner("Thinking")
        spinner.start()

        async for chunk in astream_graph_updates(
            graph, input_data, config=config, stream_mode=stream_mode
        ):
            # Stop spinner on first chunk
            if first_chunk:
                spinner.stop()
                first_chunk = False

            print_chunk(chunk, verbose=verbose)

            if chunk.get("status") == "interrupt":
                has_interrupt = True
                # Count pending action requests
                interrupt_data = chunk.get("interrupt", {})
                action_requests = interrupt_data.get("action_requests", [])
                num_pending_actions = len(action_requests) if action_requests else 1

        # Ensure spinner is stopped even if no chunks received
        if first_chunk:
            spinner.stop()

        if has_interrupt and interactive:
            decisions = handle_interrupt_input(num_pending_actions)
            input_data = prepare_agent_input(decisions=decisions)
        else:
            break

    return time.time() - start_time


def run_single_turn_sync(
    graph,
    message: str,
    config: Optional[Dict[str, Any]] = None,
    interactive: bool = True,
    verbose: bool = False,
    stream_mode: str = "updates",
) -> float:
    """Run a single turn of a sync LangGraph graph. Returns total duration in seconds."""
    input_data = prepare_agent_input(message=message)
    start_time = time.time()

    while True:
        has_interrupt = False
        num_pending_actions = 0
        first_chunk = True
        spinner = Spinner("Thinking")
        spinner.start()

        for chunk in stream_graph_updates(
            graph, input_data, config=config, stream_mode=stream_mode
        ):
            # Stop spinner on first chunk
            if first_chunk:
                spinner.stop()
                first_chunk = False

            print_chunk(chunk, verbose=verbose)

            if chunk.get("status") == "interrupt":
                has_interrupt = True
                # Count pending action requests
                interrupt_data = chunk.get("interrupt", {})
                action_requests = interrupt_data.get("action_requests", [])
                num_pending_actions = len(action_requests) if action_requests else 1

        # Ensure spinner is stopped even if no chunks received
        if first_chunk:
            spinner.stop()

        if has_interrupt and interactive:
            decisions = handle_interrupt_input(num_pending_actions)
            input_data = prepare_agent_input(decisions=decisions)
        else:
            break

    return time.time() - start_time


def print_help():
    """Print formatted help information."""
    print(f"\n{BOLD}{BRIGHT_CYAN}Commands{RESET}")
    print(f"{DIM}{'─' * 40}{RESET}")

    # Get all registered commands and display them
    commands = command_registry.all_commands()
    for cmd in sorted(commands, key=lambda c: c.name):
        aliases_str = ""
        if cmd.aliases:
            aliases_str = f", {CYAN}/{RESET}, {CYAN}/".join([""] + cmd.aliases)[4:]
        print(f"  {CYAN}/{cmd.name}{RESET}{aliases_str}")
        print(f"    {DIM}{cmd.description}{RESET}")

    print()
    print(f"{BOLD}{BRIGHT_CYAN}Shortcuts{RESET}")
    print(f"{DIM}{'─' * 40}{RESET}")
    print(f"  {CYAN}Tab{RESET}               Autocomplete commands")
    print(f"  {CYAN}Ctrl+C{RESET}            Exit at any time")
    print(f"  {CYAN}↑/↓{RESET}               Navigate options")
    print()


# --- Built-in Slash Commands ---


@register_command(
    name="help",
    description="Show this help message",
    aliases=["h", "?"],
)
def cmd_help(args: str, context: Dict[str, Any]) -> Optional[str]:
    """Display help information."""
    if args:
        # Show help for a specific command
        cmd = command_registry.get(args)
        if cmd:
            print(f"\n{BOLD}{BRIGHT_CYAN}/{cmd.name}{RESET}")
            print(f"  {cmd.description}")
            if cmd.aliases:
                print(f"  {DIM}Aliases: /{', /'.join(cmd.aliases)}{RESET}")
            if cmd.usage:
                print(f"  {DIM}Usage: {cmd.usage}{RESET}")
            print()
        else:
            print(f"{YELLOW}Unknown command: /{args}{RESET}")
    else:
        print_help()
    return None


@register_command(
    name="quit",
    description="Exit the CLI",
    aliases=["q", "exit"],
)
def cmd_quit(args: str, context: Dict[str, Any]) -> Optional[str]:
    """Exit the CLI."""
    return "exit"  # Special return value to signal exit


@register_command(
    name="clear",
    description="Clear conversation history",
    aliases=["c"],
)
def cmd_clear(args: str, context: Dict[str, Any]) -> Optional[str]:
    """Clear the conversation history."""
    context["config"]["configurable"]["thread_id"] = str(uuid.uuid4())
    print(f"\n{GREEN}✓ Conversation cleared{RESET}\n")
    return None


@register_command(
    name="version",
    description="Show version information",
    aliases=["v"],
)
def cmd_version(args: str, context: Dict[str, Any]) -> Optional[str]:
    """Display version information."""
    print(f"\n{BOLD}{BRIGHT_CYAN}langstage-cli{RESET} v{__version__}")
    agent_name = context.get("agent_name", "Unknown")
    print(f"{DIM}Agent: {agent_name}{RESET}\n")
    return None


@register_command(
    name="status",
    description="Show current session status",
    aliases=["s"],
)
def cmd_status(args: str, context: Dict[str, Any]) -> Optional[str]:
    """Display current session status."""
    config = context.get("config", {})
    thread_id = config.get("configurable", {}).get("thread_id", "N/A")
    agent_name = context.get("agent_name", "Unknown")
    verbose = context.get("verbose", False)
    use_async = context.get("use_async", False)
    stream_mode = context.get("stream_mode", "updates")

    print(f"\n{BOLD}{BRIGHT_CYAN}Session Status{RESET}")
    print(f"{DIM}{'─' * 30}{RESET}")
    print(f"  {DIM}Agent:{RESET}       {agent_name}")
    print(f"  {DIM}Thread ID:{RESET}   {thread_id[:8]}...")
    print(f"  {DIM}Mode:{RESET}        {'async' if use_async else 'sync'}")
    print(f"  {DIM}Stream:{RESET}      {stream_mode}")
    print(f"  {DIM}Verbose:{RESET}     {'on' if verbose else 'off'}")
    print(f"  {DIM}CWD:{RESET}         {os.getcwd()}")
    print()
    return None


@register_command(
    name="config",
    description="Show or set configuration",
    aliases=["cfg"],
    usage="/config [key] [value]",
)
def cmd_config(args: str, context: Dict[str, Any]) -> Optional[str]:
    """Show or modify configuration."""
    config = context.get("config", {})

    if not args:
        print(f"\n{BOLD}{BRIGHT_CYAN}Configuration{RESET}")
        print(f"{DIM}{'─' * 30}{RESET}")

        sources = config.get("_toml_sources", [])
        if sources:
            print(f"  {DIM}TOML sources:{RESET}")
            for src in sources:
                print(f"    {DIM}- {src}{RESET}")
        else:
            print(f"  {DIM}TOML sources:{RESET} {DIM}(none — using defaults){RESET}")

        # Full resolved view: each value, where it came from, and the env var
        # / TOML key that sets it.
        from langstage_cli.config import CodeConfig

        for line in CodeConfig.resolve().describe().splitlines():
            print(f"  {line}")

        configurable = config.get("configurable", {})
        if configurable:
            print(f"  {DIM}LangGraph configurable:{RESET}")
            for k, v in configurable.items():
                v_str = str(v)
                if len(v_str) > 50:
                    v_str = v_str[:50] + "..."
                print(f"    {CYAN}{k}:{RESET} {v_str}")
        print()
    else:
        parts = args.split(maxsplit=1)
        if len(parts) == 1:
            key = parts[0]
            configurable = config.get("configurable", {})
            if key in configurable:
                print(f"\n{CYAN}{key}:{RESET} {configurable[key]}\n")
            elif key in ("verbose", "async_mode", "stream_mode"):
                ctx_key = "use_async" if key == "async_mode" else key
                print(f"\n{CYAN}{key}:{RESET} {context.get(ctx_key)}\n")
            else:
                print(f"{YELLOW}Unknown config key: {key}{RESET}")
        else:
            key, value = parts
            if key == "verbose":
                context["verbose"] = value.lower() in ("true", "1", "on", "yes")
                print(f"{GREEN}✓ Set verbose = {context['verbose']}{RESET}")
            else:
                print(f"{YELLOW}Cannot modify {key} at runtime (edit langstage.toml){RESET}")
    return None


@register_command(
    name="history",
    description="Show recent messages (if available)",
    aliases=["hist"],
)
def cmd_history(args: str, context: Dict[str, Any]) -> Optional[str]:
    """Display conversation history if available."""
    graph = context.get("graph")
    config = context.get("config", {})

    if graph is None:
        print(f"{YELLOW}No graph available{RESET}")
        return None

    try:
        # Try to get state from the graph's checkpointer
        if hasattr(graph, "get_state"):
            state = graph.get_state(config)
            if state and hasattr(state, "values"):
                messages = state.values.get("messages", [])
                if messages:
                    print(f"\n{BOLD}{BRIGHT_CYAN}Conversation History{RESET}")
                    print(f"{DIM}{'─' * 40}{RESET}")

                    # Show last N messages
                    limit = 10
                    if args:
                        try:
                            limit = int(args)
                        except ValueError:
                            pass

                    for msg in messages[-limit:]:
                        role = getattr(msg, "type", "unknown")
                        content = getattr(msg, "content", str(msg))

                        if role == "human":
                            print(f"\n  {BRIGHT_BLUE}You:{RESET}")
                        elif role == "ai":
                            print(f"\n  {BRIGHT_CYAN}Agent:{RESET}")
                        else:
                            print(f"\n  {DIM}{role}:{RESET}")

                        # Truncate long content
                        if len(content) > 200:
                            content = content[:200] + "..."
                        print(f"  {DIM}{content}{RESET}")
                    print()
                else:
                    print(f"{DIM}No messages in history{RESET}")
            else:
                print(f"{DIM}No state available{RESET}")
        else:
            print(f"{DIM}Graph does not support state retrieval{RESET}")
    except Exception as e:
        print(f"{DIM}Could not retrieve history: {e}{RESET}")

    return None


@register_command(
    name="reset",
    description="Reset the session (clear history and restart)",
    aliases=["restart"],
)
def cmd_reset(args: str, context: Dict[str, Any]) -> Optional[str]:
    """Reset the session."""
    context["config"]["configurable"]["thread_id"] = str(uuid.uuid4())
    print(f"\n{GREEN}✓ Session reset{RESET}")
    print(f"{DIM}New thread ID: {context['config']['configurable']['thread_id'][:8]}...{RESET}\n")
    return None


@register_command(
    name="verbose",
    description="Toggle verbose output mode",
    usage="/verbose [on|off]",
)
def cmd_verbose(args: str, context: Dict[str, Any]) -> Optional[str]:
    """Toggle or show verbose output mode."""
    verbose = context.get("verbose", False)
    if args:
        if args.lower() in ("on", "true", "1"):
            context["verbose"] = True
            print(f"{GREEN}✓ Verbose mode enabled{RESET}")
        elif args.lower() in ("off", "false", "0"):
            context["verbose"] = False
            print(f"{GREEN}✓ Verbose mode disabled{RESET}")
    else:
        print(f"{DIM}Verbose mode: {'on' if verbose else 'off'}{RESET}")
        print(f"{DIM}Use /verbose on or /verbose off to change{RESET}")
    return None


def get_command_suggestions(partial: str) -> List[str]:
    """Get command suggestions based on partial input.

    Args:
        partial: Partial command name (without leading /)

    Returns:
        List of matching command names
    """
    partial_lower = partial.lower()
    suggestions = []

    for cmd in command_registry.all_commands():
        # Check main command name
        if cmd.name.startswith(partial_lower):
            suggestions.append(cmd.name)
        # Check aliases
        for alias in cmd.aliases:
            if alias.startswith(partial_lower) and cmd.name not in suggestions:
                suggestions.append(cmd.name)

    return sorted(suggestions)


def command_completer(text: str, state: int) -> Optional[str]:
    """Readline completer for slash commands.

    Args:
        text: Current text being completed
        state: State index for multiple completions

    Returns:
        Next completion or None
    """
    # Only complete if starting with /
    if not text.startswith("/"):
        return None

    partial = text[1:]  # Remove leading /
    suggestions = ["/" + s for s in get_command_suggestions(partial)]

    if state < len(suggestions):
        return suggestions[state]
    return None


def setup_readline_completion():
    """Set up readline for tab completion of slash commands."""
    if not HAS_READLINE:
        return

    readline.set_completer(command_completer)
    readline.set_completer_delims(" \t\n")

    # Use tab for completion
    if sys.platform == "darwin":
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")


def run_conversation_loop(
    graph,
    config: Dict[str, Any],
    agent_name: str = "AgentCode",
    agent_description: Optional[str] = None,
    use_async: bool = False,
    interactive: bool = True,
    verbose: bool = False,
    stream_mode: str = "updates",
    initial_message: Optional[str] = None,
    single_shot: bool = False,
):
    """
    Run a continuous conversation loop with the LangGraph agent.
    Styled after Claude Code / nanocode.

    If single_shot is True and initial_message is provided, exit after processing.
    """
    # Set up tab completion for slash commands
    setup_readline_completion()

    # Print box-drawn header with agent name and description
    print_header_box(agent_name, os.getcwd(), agent_description)

    # Print welcome message with tips
    print_welcome()

    # Create command context (mutable dict that commands can modify)
    command_context = {
        "graph": graph,
        "config": config,
        "agent_name": agent_name,
        "use_async": use_async,
        "interactive": interactive,
        "verbose": verbose,
        "stream_mode": stream_mode,
    }

    # Process initial message if provided
    if initial_message:
        print(f"\n{BOLD}{BRIGHT_BLUE}You{RESET}")
        print(f"{initial_message}")
        print()

        if use_async:
            duration = asyncio.run(
                run_single_turn_async(
                    graph, initial_message, config, interactive, verbose, stream_mode
                )
            )
        else:
            duration = run_single_turn_sync(
                graph, initial_message, config, interactive, verbose, stream_mode
            )
        print_timing(duration, verbose)
        print()

        # Exit after single-shot execution
        if single_shot:
            return

    # Main conversation loop
    while True:
        try:
            print(separator("dots"))
            user_input = input(make_prompt()).strip()

            if not user_input:
                continue

            # Check if it's a slash command
            cmd_name, cmd_args = command_registry.parse_input(user_input)

            if cmd_name is not None:
                # It's a slash command
                cmd = command_registry.get(cmd_name)
                if cmd:
                    result = cmd.execute(cmd_args, command_context)
                    # Update local vars from context (commands may modify these)
                    verbose = command_context.get("verbose", verbose)
                    if result == "exit":
                        break
                else:
                    # Show suggestions for unknown commands
                    suggestions = get_command_suggestions(cmd_name)
                    print(f"{YELLOW}Unknown command: /{cmd_name}{RESET}")
                    if suggestions:
                        suggestion_str = ", ".join([f"/{s}" for s in suggestions[:3]])
                        print(f"{DIM}Did you mean: {suggestion_str}?{RESET}")
                    else:
                        print(f"{DIM}Type /help to see available commands{RESET}")
                continue

            # Handle bang commands (!) - execute bash directly
            if user_input.startswith("!"):
                bash_cmd = user_input[1:].strip()
                if bash_cmd:
                    print()
                    try:
                        result = subprocess.run(
                            bash_cmd,
                            shell=True,
                            capture_output=True,
                            text=True,
                        )
                        if result.stdout:
                            print(result.stdout, end="")
                        if result.stderr:
                            print(f"{RED}{result.stderr}{RESET}", end="")
                        if result.returncode != 0:
                            print(f"{DIM}Exit code: {result.returncode}{RESET}")
                    except Exception as e:
                        print(f"{RED}Error executing command: {e}{RESET}")
                continue

            # Handle "exit" as a special case (without slash)
            if user_input.lower() == "exit":
                break

            print()  # Space before response

            # Run the agent
            if use_async:
                duration = asyncio.run(
                    run_single_turn_async(
                        graph, user_input, config, interactive, verbose, stream_mode
                    )
                )
            else:
                duration = run_single_turn_sync(
                    graph, user_input, config, interactive, verbose, stream_mode
                )
            print_timing(duration, verbose)
            print()

        except (EOFError, KeyboardInterrupt):
            break
        except Exception as err:
            print(f"\n{RED}✗ Error: {err}{RESET}\n")

    # Print goodbye message
    print_goodbye()


@click.command()
@click.version_option(__version__, "--version", prog_name="langstage-cli")
@click.argument("message", required=False)
@click.option(
    "--agent",
    "-a",
    "agent_spec",
    help="Agent spec: path/to/file.py, path/to/file.py:graph, or module.path:graph",
)
@click.option(
    "--graph-name",
    "-g",
    help="Name of the graph variable (default: 'graph', overridden if spec includes :name)",
)
@click.option(
    "--file",
    "-f",
    "prompt_file",
    type=click.Path(exists=True),
    help="Read input message from a file (any extension)",
)
@click.option(
    "--interactive/--no-interactive",
    default=True,
    help="Handle interrupts interactively (default: True)",
)
@click.option(
    "--async-mode/--sync-mode",
    "use_async",
    default=None,
    help="Use async streaming (default: sync)",
)
@click.option(
    "--stream-mode",
    help="Stream mode for LangGraph (default: 'updates')",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=None,
    help="Show verbose output including node names",
)
@click.option(
    "--demo",
    is_flag=True,
    default=False,
    help="Run with the built-in keyless demo agent (no API key needed)",
)
@click.option(
    "--show-config",
    "show_config",
    is_flag=True,
    default=False,
    help="Print the resolved configuration (defaults < langstage.toml < env < CLI) and exit",
)
def main(
    message: Optional[str],
    agent_spec: Optional[str],
    graph_name: Optional[str],
    prompt_file: Optional[str],
    interactive: bool,
    use_async: Optional[bool],
    stream_mode: Optional[str],
    verbose: Optional[bool],
    demo: bool,
    show_config: bool,
):
    """
    Run a LangGraph agent from the command line.

    MESSAGE is an optional input to send to the agent immediately.

    Agent spec (-a/--agent) can be:
    \b
    - path/to/file.py           (uses default graph name 'graph')
    - path/to/file.py:agent     (specifies graph variable name)
    - package.module            (Python module path)
    - package.module:agent      (module with graph variable name)

    Supports environment variables for configuration (legacy DEEPAGENT_*
    names still work as deprecated aliases):

    \b
    - LANGSTAGE_AGENT_SPEC: Agent location (same formats as above).
    - LANGSTAGE_WORKSPACE_ROOT: Working directory for the agent
    - LANGSTAGE_STREAM_MODE: Stream mode for LangGraph (updates or values)

    Reads ~/.langstage/config.toml (global) and langstage.toml (project,
    walks up from cwd). Precedence: CLI args > env vars > project TOML >
    global TOML > built-in defaults. (The legacy ~/.deepagents/config.toml and
    deepagents.toml are still read as deprecated fallbacks.)

    \b
    Examples:
        langstage-cli "Hello, agent!"
        langstage-cli -a my_agent.py "What can you do?"
        langstage-cli -a my_agent.py:graph
        langstage-cli -f ./prompt.md
        langstage-cli --demo "try it with no API key"
        langstage-cli --show-config
    """
    # Windows consoles default to cp1252, where the spinner (Braille frames) and
    # status glyphs (✓ ⏺ —) raise UnicodeEncodeError — the documented
    # `langstage-cli --demo "hello"` one-liner crashed before any output. Force
    # UTF-8 with errors="replace" so a glyph can never crash the process.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # non-reconfigurable stream
            pass

    if demo:
        if agent_spec:
            print(f"{RED}⏺ Error: --demo and -a/--agent are mutually exclusive{RESET}")
            sys.exit(1)
        # The keyless echo agent shipped with the shared core.
        agent_spec = "langgraph_stream_parser.demo.stub:graph"

    # CLI flags are the highest-precedence config layer. Build the override dict
    # ONCE and use it for both --show-config and the real run, so the diagnostic
    # reflects exactly what a run resolves (CLI-set values then show as
    # `[override]`, not `[default]`). Resolving --show-config without these was
    # the bug in #20.
    cli_overrides = {
        "agent_spec": agent_spec,
        "graph_name": graph_name,
        "stream_mode": stream_mode,
        # bool flags only override when actually passed; otherwise fall back to
        # TOML/env/default.
        "async_mode": True if use_async else None,
        "verbose": True if verbose else None,
    }

    if show_config:
        print(
            config_module.CodeConfig.resolve(
                toml_start=Path.cwd(), overrides=cli_overrides
            ).describe()
        )
        return

    try:
        # Handle -f/--file option: read message from file
        if prompt_file and message:
            print(f"{RED}⏺ Error: Cannot use both MESSAGE argument and -f/--file option{RESET}")
            sys.exit(1)

        if prompt_file:
            try:
                with open(prompt_file, "r", encoding="utf-8") as f:
                    message = f.read().strip()
                if not message:
                    print(f"{RED}⏺ Error: File '{prompt_file}' is empty{RESET}")
                    sys.exit(1)
            except Exception as e:
                print(f"{RED}⏺ Error reading file '{prompt_file}': {e}{RESET}")
                sys.exit(1)

        # Load TOML configuration (global + project, merged)
        try:
            toml_config, toml_sources = config_module.load_config()
        except config_module.ConfigError as e:
            print(f"{RED}⏺ {e}{RESET}")
            sys.exit(1)

        # Resolve all standard settings through the shared chain in one shot:
        # CLI overrides > DEEPAGENT_* env > deepagents.toml > defaults.
        # (DEEPAGENT_AGENT_SPEC is canonical; DEEPAGENT_SPEC is a deprecated alias.)
        cfg = config_module.CodeConfig.resolve(
            toml_start=Path.cwd(),
            overrides=cli_overrides,
        )
        final_spec = cfg.agent_spec
        final_graph_name_default = cfg.graph_name
        final_stream_mode = cfg.stream_mode
        use_async = cfg.async_mode
        verbose = cfg.verbose
        # Only change directory when a workspace root was actually configured.
        workspace_root = (
            cfg.workspace_root if cfg.sources.get("workspace_root") != "default" else None
        )

        # If no spec provided, try the default agent
        if not final_spec:
            default_agent_path = Path(__file__).parent.parent / "examples" / "agent.py"
            if default_agent_path.exists():
                final_spec = f"{default_agent_path}:agent"
            else:
                print(f"{RED}⏺ Error: No agent specified.{RESET}")
                print(f"\n{DIM}Usage:{RESET}")
                print("  langstage-cli path/to/agent.py:graph")
                print("  langstage-cli mypackage.module:agent")
                print(f"\n{DIM}Or set the LANGSTAGE_AGENT_SPEC environment variable{RESET}")
                sys.exit(1)

        # Change to workspace root if specified
        if workspace_root:
            workspace_path = Path(workspace_root).expanduser().resolve()
            if workspace_path.exists():
                os.chdir(workspace_path)

        # Load the graph with a spinner
        spinner = Spinner("Loading agent")
        spinner.start()
        graph, final_graph_name = load_graph(final_spec, final_graph_name_default)
        spinner.stop()
        print(f"{GREEN}✓{RESET} {DIM}Loaded {final_spec}{RESET}")

        # Seed LangGraph RunnableConfig from TOML [configurable] table if present
        config_dict: Dict[str, Any] = {"configurable": {}}
        toml_configurable = config_module.get(toml_config, "configurable")
        if isinstance(toml_configurable, dict):
            config_dict["configurable"].update(toml_configurable)
        if "thread_id" not in config_dict["configurable"]:
            config_dict["configurable"]["thread_id"] = str(uuid.uuid4())

        # Expose TOML sources to slash commands via the config dict
        config_dict["_toml_sources"] = [str(p) for p in toml_sources]

        # Extract agent name and description from graph object
        agent_name = get_agent_name(graph)
        agent_description = get_agent_description(graph)

        # Run the conversation loop
        # Single-shot mode: exit after processing if message was provided via CLI
        run_conversation_loop(
            graph=graph,
            config=config_dict,
            agent_name=agent_name,
            agent_description=agent_description,
            use_async=use_async,
            interactive=interactive,
            verbose=verbose,
            stream_mode=final_stream_mode,
            initial_message=message,
            single_shot=bool(message),
        )

    except FileNotFoundError as e:
        print(f"{RED}⏺ Error: {e}{RESET}")
        sys.exit(1)
    except AttributeError as e:
        print(f"{RED}⏺ Error: {e}{RESET}")
        sys.exit(1)
    except ModuleNotFoundError as e:
        print(f"{RED}⏺ Error: {e}{RESET}")
        print(f"\n{DIM}Make sure your agent's dependencies are installed.{RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"{RED}⏺ Error: {e}{RESET}")
        if verbose:
            import traceback

            print(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
