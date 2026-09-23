"""
CLI for running arbitrary LangGraph agents from the terminal.
Styled after Claude Code / nanocode.
"""

import asyncio
import copy
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click

from langstage_core import apply_workspace, load_agent_spec
from langstage_cli import config as config_module
from langstage_cli import sessions

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

# Scriptable single-shot output (gh #53). When a single-shot run is piped (stdout
# is not a TTY) or --quiet is passed, we suppress every decoration — the header
# box, welcome text, "Loaded" line, spinner, tool-call chatter, and timing — and
# strip ANSI, so the pipe/file receives ONLY the agent's reply. Toggled once in
# main(); the render helpers below read it as a module global.
_QUIET = False


def _is_a_tty(stream) -> bool:
    """``stream.isatty()`` that never raises.

    A replaced or closed stream (pytest capture, a detached service, a stream
    swapped for a plain object) may lack ``isatty`` or raise ``ValueError`` on a
    closed file. Treat anything unknowable as "not a terminal" — the safe default
    for both the quiet/scriptable decision and the interactive-approval guard.
    """
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


def _disable_ansi() -> None:
    """Blank every ANSI constant so nothing colorized reaches a pipe or file.

    The render helpers reference these as module globals at call time, so
    reassigning them here strips color everywhere without threading a flag
    through every ``print``. ``render_markdown`` then also drops its ``**``/`` ` ``
    markers cleanly (empty wrappers), leaving plain text.
    """
    global RESET, BOLD, DIM, ITALIC, UNDERLINE, BLUE, CYAN, GREEN, YELLOW, RED
    global MAGENTA, WHITE, GRAY, BRIGHT_CYAN, BRIGHT_BLUE, BRIGHT_GREEN, BRIGHT_YELLOW
    RESET = BOLD = DIM = ITALIC = UNDERLINE = BLUE = CYAN = GREEN = YELLOW = RED = ""
    MAGENTA = WHITE = GRAY = BRIGHT_CYAN = BRIGHT_BLUE = BRIGHT_GREEN = BRIGHT_YELLOW = ""


def _status(msg: str) -> None:
    """Emit a status/diagnostic line off the reply stream: to stderr in quiet
    mode (so it never pollutes the piped answer), to stdout otherwise.

    In quiet/scriptable mode also drop a leading ``⏺ `` marker. The glyph is a
    literal in the caller's f-string (``f"{RED}⏺ Error: …{RESET}"``), not an ANSI
    code, so ``_disable_ansi()`` — which blanks the surrounding color — leaves it
    in place. Quiet mode is documented to suppress it, and the #74 fix only routed
    around it for ``BrokenPipeError``; stripping it here, in one place, completes
    that suppression so every error/diagnostic path matches the bare ``Error: …``
    that ``print_chunk`` already emits on its own quiet error branch. (gh #76)
    """
    if _QUIET:
        msg = msg.removeprefix("⏺ ")
    print(msg, file=sys.stderr if _QUIET else sys.stdout)


def _fmt_exc(e: BaseException) -> str:
    """Render an exception for a top-level ``Error:`` line, naming the class.

    Many "my agent doesn't load yet" exceptions have an EMPTY ``str(e)`` — a bare
    ``assert x`` (``AssertionError('')``), ``raise NotImplementedError``,
    ``raise RuntimeError()`` — so a handler that prints only ``{e}`` collapsed to a
    blank, typeless ``Error:`` with nothing to act on (gh #109). Fall back to the
    class name when the message is empty, and prefix it otherwise, so the load/top-
    level path names the type just like the runtime turn-error path (which core
    renders as ``{type(exc).__name__}: {exc}``).
    """
    msg = str(e)
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


# Keys the terminal CLI never honors, so `--show-config` / `/config` omit them and
# the diagnostic only advertises knobs that actually do something. The inherited
# HostConfig server keys — it starts no server (host/port/debug are inert) and the
# header box uses the loaded graph's name, not `title` (gh #36) — plus `stream_mode`,
# which has had no effect since the AG-UI streaming migration (gh #62), and
# `async_mode`, inert since ADR 0003 collapsed every turn onto the one async AG-UI
# path (gh #88).
_INERT_KEYS = ["host", "port", "debug", "title", "stream_mode", "async_mode"]

# Sentinel value of a bare ``--resume`` (no id): list this workspace's sessions instead
# of resuming one. Matches the click option's ``flag_value``. (gh #102)
_RESUME_LIST_SENTINEL = "__LIST__"

# On the interactive HITL path, answering a plain-string ``interrupt(...)`` with free
# text makes the bundled ``ag_ui_langgraph`` dep log a benign WARNING on EVERY response
# — "failed to parse resume_input as JSON, treating as string (...)" — right above the
# correct answer. The resumed value is right; the line just looks like an error. Drop
# ONLY that one record so no other ag_ui_langgraph warning is ever hidden. cli-local by
# design: the CLI owns its console UX, so this stays out of core. (gh #103)
_AGUI_RESUME_JSON_WARNING = "failed to parse resume_input as JSON"

# The warning is emitted by ``logging.getLogger("ag_ui_langgraph.agent")`` (the module
# ``__name__``). A logger-level filter is consulted ONLY for records ORIGINATING on that
# logger — during propagation ancestor-logger filters are skipped — so the filter must
# sit on the emitting child logger (verified empirically). The package logger is added
# too, defensively, in case a future version emits on it directly.
_AGUI_WARNING_LOGGERS = ("ag_ui_langgraph.agent", "ag_ui_langgraph")


class _DropResumeJSONWarning(logging.Filter):
    """A surgical filter that removes ONLY ag_ui_langgraph's resume-JSON warning (gh #103)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a malformed record must never break logging
            message = str(getattr(record, "msg", ""))
        return _AGUI_RESUME_JSON_WARNING not in message


@contextmanager
def _quiet_agui_resume_json_warning():
    """Drop ag_ui_langgraph's benign resume-JSON WARNING for the wrapped block (gh #103).

    Installs the precise :class:`_DropResumeJSONWarning` filter on the emitting logger
    for the duration and removes it after — so the noise is gone from the interactive
    resume path while every OTHER warning still reaches the console.
    """
    filt = _DropResumeJSONWarning()
    loggers = [logging.getLogger(name) for name in _AGUI_WARNING_LOGGERS]
    for lg in loggers:
        lg.addFilter(filt)
    try:
        yield
    finally:
        for lg in loggers:
            lg.removeFilter(filt)


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


def make_prompt(text: str = "❯", color: str | None = None) -> str:
    """Create a prompt string with proper readline escaping for ANSI codes.

    This prevents line wrapping issues on Windows and other terminals.
    """
    prompt_color = BRIGHT_BLUE if color is None else color
    return f"{rl_wrap(BOLD)}{rl_wrap(prompt_color)}{text}{rl_wrap(RESET)} "


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
        # Whether stop() has already emitted its line-clear. See stop(). (gh #84)
        self._stopped = False

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
        self._stopped = False
        self.start_time = time.time()
        self.thread = threading.Thread(target=self._spin, daemon=True)
        self.thread.start()

    def stop(self):
        """Stop the spinner and clear its line. Idempotent — a second stop() is a
        no-op (gh #84).

        The line-clear below is ``CR`` + ``CSI 2K`` ("erase entire line"), which is
        only ever correct while the cursor is still parked on the spinner's own
        animated line. ``run_single_turn_agui()`` stops the spinner twice per turn:
        once on the first chunk (correct — that clears "Thinking…") and again in its
        ``finally``, which exists so a turn that streams NO chunks still clears the
        line instead of leaving a dangling "Thinking…". But by the time the
        ``finally`` runs on a normal turn the reply has been printed with ``end=""``
        and no terminating newline, so the cursor sits on the *reply's* last line —
        and the second clear erased it. On a real terminal the user saw their prompt
        and the timing line with the agent's answer wiped out (a one-line reply
        vanished entirely; a multi-line reply lost its last line). Guarding on
        ``_stopped`` keeps the useful first clear and the no-chunk safety net while
        making the redundant second call harmless.
        """
        if self._stopped:
            return
        self._stopped = True
        self.running = False
        if self.thread:
            self.thread.join(timeout=0.2)
        # Clear the spinner line
        print("\r\033[2K", end="", flush=True)


def get_terminal_width() -> int:
    """Get terminal width, floored at a sane minimum and capped at 100 for readability.

    A pty forked without an initialized window size (pexpect/expect automation, some CI
    pseudo-ttys, editor terminals, process supervisors) reports ``columns == 0`` WITHOUT
    raising ``OSError`` — so the old `min(cols, 100)` returned 0, and the header box's
    borders (`"─" * (width - 2)`) collapsed to `╭╮`/`╰╯` while content rows overflowed
    (gh #71). Floor at 40 so the banner always renders."""
    try:
        cols = os.get_terminal_size().columns
    except OSError:
        cols = 80
    return min(max(cols, 40), 100)


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
    """Extract agent name from graph object, defaulting to 'Agent'."""
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
    return "Agent"


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


def load_graph(
    spec: str, default_graph_name: str = "graph", attach_default_checkpointer: bool = True
):
    """
    Load a graph from either a file path or module path.

    Delegates the actual import to the shared
    ``langstage_core.host.load_agent_spec`` loader, while preserving
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
    # Skip the in-memory default when a durable per-workspace saver will be attached
    # instead (session persistence, gh #102) — else build_agent's in-memory fallback
    # would win and cross-invocation memory would be lost.
    if attach_default_checkpointer:
        _ensure_checkpointer(graph)
    return graph, graph_name


def _ensure_checkpointer(graph: Any) -> None:
    """Attach an in-memory checkpointer if the graph has none.

    The interactive loop sends only the latest message each turn and relies on
    the graph's checkpointer (keyed by ``configurable.thread_id``) for multi-turn
    memory. Many bring-your-own graphs — including the README's own minimal
    example — compile without one, which left the "conversation loop" amnesiac
    and ``/history`` erroring ("No checkpointer set"). Auto-attach an in-memory
    default (same as the web stage) so memory and ``/history`` work out of the
    box; a user-supplied checkpointer is left untouched. Pass your own (durable)
    checkpointer for persistence across runs. (gh #38)
    """
    if getattr(graph, "checkpointer", None) is not None:
        return
    try:
        from langgraph.checkpoint.memory import InMemorySaver

        graph.checkpointer = InMemorySaver()
    except Exception:  # noqa: BLE001 - best effort; the loop still runs, just stateless
        pass


def _split_py_file_spec(spec: str) -> Optional[Tuple[str, str]]:
    """Split a ``path.py[:attr]`` agent spec into ``(file_path, suffix)`` when the
    path part is a ``.py`` FILE path; return ``None`` for module specs / non-specs.

    ``suffix`` is the reattachable ``":attr"`` (or ``""`` for a bare path). The
    ``path:attr`` split is the same one ``load_graph`` uses and stays Windows
    drive-letter safe: a trailing ``':token'`` is a graph-name suffix only when it
    has no path separator, so ``C:\\x.py:graph`` keeps its drive colon and splits at
    the final ``:graph``. Shared by ``_absolutize_file_spec`` (cwd base, gh #30) and
    ``_rebase_toml_file_spec`` (toml-dir base, gh #116) so the parse lives in one place.
    """
    if not spec:
        return None
    path_part, suffix = spec, ""
    if ":" in spec:
        head, _, tail = spec.rpartition(":")
        if tail and "/" not in tail and "\\" not in tail:
            path_part, suffix = head, f":{tail}"
    # Only a .py file path is a resolvable file; a module path (pkg.mod) is not.
    if not path_part.endswith(".py"):
        return None
    return path_part, suffix


def _absolutize_file_spec(spec: str) -> str:
    """Resolve a relative *file-path* agent spec to an absolute path against the
    current cwd.

    The CLI chdirs into ``LANGSTAGE_WORKSPACE_ROOT`` before loading the agent, so
    a relative ``-a my_agent.py:graph`` would otherwise be looked up under the
    workspace root instead of where the user actually invoked the command (and
    put the file). Resolving the file part up front keeps the spec anchored to
    the invocation cwd. Module specs (``pkg.mod:attr``) and already-absolute
    paths pass through unchanged. (gh #30)
    """
    parsed = _split_py_file_spec(spec)
    if parsed is None:
        return spec
    path_part, suffix = parsed
    return f"{Path(path_part).expanduser().resolve()}{suffix}"


def _rebase_toml_file_spec(spec: str, toml_dir: Path) -> str:
    """Resolve a relative *file-path* agent spec that came from a ``langstage.toml``
    against that toml's OWN directory — the discovered project root — not the cwd.

    Config discovery walks UP to find ``langstage.toml``, but a relative ``agent.spec``
    in it (what ``init`` scaffolds, e.g. ``"my_agent.py:graph"``) was resolved against
    the process cwd, so running from a subdir crashed ``Agent file not found`` even
    though ``--show-config`` reported the spec resolved. A toml-sourced relative spec's
    natural base is the config file's directory — like a path in ``pyproject.toml`` /
    ``tsconfig.json`` resolves relative to the config file — so the project runs
    identically from its root and any subdirectory. (gh #116)

    Only applied to *toml-sourced* specs; specs from ``-a`` / ``LANGSTAGE_AGENT_SPEC``
    keep #30's cwd-relative base ("where the user typed it"). Module specs
    (``pkg.mod:attr``) and already-absolute paths pass through unchanged.
    """
    parsed = _split_py_file_spec(spec)
    if parsed is None:
        return spec
    path_part, suffix = parsed
    file_path = Path(path_part).expanduser()
    if file_path.is_absolute():
        return spec
    return f"{(toml_dir / file_path).resolve()}{suffix}"


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


# Per-value cap for the HITL approval preview. Values are truncated, never dropped: the
# user must see EVERY field they approve (gh #146, #159), just not a 5 KB file body inline.
_APPROVAL_VALUE_MAX = 80
# Above this total width the fields go one per line instead of a single comma-joined row.
_APPROVAL_LINE_MAX = 100
# Human-readable fields a generic interrupt dict may use as its headline (gh #82).
_INTERRUPT_LABEL_KEYS = ("description", "question", "message", "prompt")


def _approval_text(value: Any, limit: Optional[int] = _APPROVAL_VALUE_MAX) -> str:
    """One field of the approval preview as a single, escaped, length-capped line.

    Non-printable characters (newlines, ANSI escapes) are shown escaped, so a tool arg
    can't break out of its line and forge prompt text above the Approve/Reject menu.
    """
    if isinstance(value, str):
        text = value
    elif isinstance(value, (dict, list, tuple)):
        try:
            text = json.dumps(value, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
    else:
        text = str(value)
    text = "".join(ch if ch.isprintable() else repr(ch)[1:-1] for ch in text)
    if limit is not None and len(text) > limit:
        return text[:limit] + "..."
    return text


def _approval_fields(action: Dict[str, Any], skip: Tuple[str, ...]) -> str:
    """Every field of an interrupt dict (minus the ``skip`` headline keys) as ``k=v``.

    An ``args`` dict is flattened into its own ``k=v`` pairs (the ActionRequest payload,
    gh #146); every other top-level key is shown too (gh #159). Pairs are kept as a list,
    not a dict, so an arg and a sibling sharing a name can't shadow each other.
    """
    pairs: List[Tuple[Any, Any]] = []
    for key, val in action.items():
        if key in skip:
            continue
        if key == "args" and isinstance(val, dict):
            pairs.extend(val.items())
        elif key == "args" and val in (None, "", [], ()):
            continue
        else:
            pairs.append((key, val))
    parts = [f"{_approval_text(k, None)}={_approval_text(v)}" for k, v in pairs]
    joined = ", ".join(parts)
    return joined if len(joined) <= _APPROVAL_LINE_MAX else "\n".join(parts)


def format_interrupt_request(action: Any) -> Tuple[str, str]:
    """Render one HITL interrupt ``action_request`` to a ``(label, preview)`` pair.

    A generic LangGraph ``interrupt(...)`` may carry ANY value, not just a
    deepagents ``ActionRequest``. The renderer must never ask the user to approve
    an action whose description it silently threw away (gh #82), so the preview
    lists EVERY field being approved as ``name=value`` (each value capped, never
    dropped; one per line when long):

    - a deepagents/langchain ``ActionRequest`` (tool name under ``action`` — the
      convention #69 fixed — or the legacy ``tool`` key) -> tool name + all of its
      ``args`` (not just the first value, gh #146) + any sibling fields (an
      ``action`` label on a generic dict must not hide the rest of it, gh #159);
    - any other dict -> its first human-readable field
      (``description``/``question``/``message``/``prompt``) + the remaining fields,
      else a compact JSON repr of the whole payload (or, if that would be cut off,
      every field in the preview) — instead of the old, content-dropping ``unknown``;
    - a bare string / scalar -> the value itself (a ``.get`` on it used to raise).

    The preview may span several lines (``\n``-joined); the caller indents them.
    """
    if isinstance(action, dict):
        for key in ("action", "tool"):
            tool = action.get(key)
            if tool:
                return str(tool), _approval_fields(action, skip=(key,))
        for key in _INTERRUPT_LABEL_KEYS:
            val = action.get(key)
            if isinstance(val, str) and val.strip():
                return val, _approval_fields(action, skip=(key,))
        # No recognized field — surface the payload compactly, never "unknown". If the
        # one-line repr would be cut off (hiding later keys), list every field instead
        # (an uncut dict repr always ends in "}", so a "..." tail means it was capped).
        compact = _compact_repr(action)
        if not compact.endswith("..."):
            return compact, ""
        return "Approval requested", _approval_fields(action, skip=())
    return str(action), ""


def _print_approval_preview(preview: str) -> None:
    """Print an approval preview under its label; a multi-line preview (one field per
    line, gh #146) keeps every line aligned under the ``└─``."""
    for n, line in enumerate(preview.split("\n") if preview else []):
        print(f"     {DIM}{'└─' if n == 0 else '  '} {line}{RESET}")


def _compact_repr(value: Any) -> str:
    """A one-line, length-capped repr for an unrecognized interrupt payload."""
    try:
        text = json.dumps(value, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= 120 else text[:120] + "..."


def _is_generic_interrupt(action_requests: List[Any]) -> bool:
    """Whether an interrupt is a generic/scalar ``interrupt(value)`` rather than a
    deepagents tool-REVIEW interrupt — the same distinction #82/#95 draw when
    RENDERING, applied to the RESUME (gh #99).

    A deepagents/langchain ``ActionRequest`` is a dict keyed ``action`` (the #69
    convention) or the legacy ``tool``; its contract is the tool-review protocol,
    so it resumes with a ``{"decisions": [...]}`` envelope. Anything else — a bare
    string / scalar (the canonical ``value = interrupt("What is your name?")``
    form, which core surfaces as a single string action request), or a plain dict
    without that key — is generic: its contract is "``interrupt`` returns exactly
    what I was resumed with", so it must resume with the RAW value the user gives,
    never a ``{"decisions": [...]}`` approval envelope it never asked for.
    """
    if not action_requests:
        return True
    return not all(
        isinstance(a, dict) and (a.get("action") or a.get("tool")) for a in action_requests
    )


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
            if _QUIET:
                # Scriptable path (gh #53): emit the raw reply text only — no cyan
                # bullet, no [node] label, and no markdown re-rendering, so a pipe
                # gets exactly what the model produced. But when the streaming node
                # changes mid-turn, insert a bare newline (no marker/color) so two
                # nodes' messages don't run together (…up.The capital…). The gh #43
                # node-change break landed in the verbose/non-verbose branches but not
                # here, so the scriptable path — the one output mode meant to be
                # machine-parseable — was the only one that dropped the boundary
                # (gh #74). Tokens of a single message share one node, so they still
                # join unbroken.
                if print_chunk._streaming_text and print_chunk._streaming_node != node:
                    print()  # node changed mid-turn — break so the messages don't concatenate
                print(text, end="", flush=True)
                print_chunk._streaming_node = node
                print_chunk._streaming_text = True
                return
            if verbose:
                # Print the [node] label ONCE per streamed run — when a text run
                # starts, or the node changes mid-stream — then append subsequent
                # tokens with no label. A token-streaming model emits one chunk per
                # token, so prefixing [node] on every chunk jammed it before every
                # token. The #34 fix covered only the non-verbose branch. (gh #40)
                if not print_chunk._streaming_text or print_chunk._streaming_node != node:
                    if print_chunk._streaming_text:
                        print()  # node changed mid-run — break before the new label
                    print(f"{DIM}[{node}]{RESET} ", end="")
                    print_chunk._streaming_node = node
                    print_chunk._streaming_text = True
                print(text, end="")
            else:
                # Print the cyan bullet ONCE at the start of a streamed AI turn AND
                # again when the node changes mid-turn, then append subsequent tokens
                # with no marker. A token-streaming model emits one chunk per token, so
                # a per-chunk marker jammed a `⏺` before every token (gh #34); but a
                # per-turn-only marker ran two nodes' messages together on one line with
                # no separator (gh #43). Break + re-mark on a node change.
                if not print_chunk._streaming_text or print_chunk._streaming_node != node:
                    if print_chunk._streaming_text:
                        print()  # node changed mid-turn — break before the new marker
                    print(f"{CYAN}⏺{RESET} {render_markdown(text)}", end="")
                    print_chunk._streaming_node = node
                    print_chunk._streaming_text = True
                else:
                    print(render_markdown(text), end="")

        # Handle tool calls - green tool name
        elif "tool_calls" in chunk:
            if _QUIET:
                return  # tool chatter is decoration; scriptable output omits it
            print_chunk._streaming_text = False  # a non-text event ends the text run
            for tool_call in chunk["tool_calls"]:
                tool_name = tool_call["name"]
                args = tool_call.get("args", {})
                arg_preview = get_tool_arg_preview(args)

                print(f"\n{GREEN}● {tool_name}{RESET}")
                if arg_preview:
                    print(f"  {DIM}└─ {arg_preview}{RESET}")

        # Handle tool results - indented with result preview
        elif "tool_result" in chunk:
            if _QUIET:
                return  # tool chatter is decoration; scriptable output omits it
            print_chunk._streaming_text = False
            result = chunk.get("tool_result", "")
            preview = format_result_preview(str(result))
            print(f"  {DIM}   ↳ {preview}{RESET}")

    elif status == "interrupt":
        print_chunk._streaming_text = False
        if _QUIET:
            # The `⚠ Action Required` banner is human-facing decoration — like the
            # tool-call / tool-result chatter above, the scriptable path omits it so
            # the machine-readable reply on stdout carries ONLY the agent's text and
            # is never corrupted by the banner (a leading blank line, the literal `⚠`
            # glyph, and the pending-action list). Under --no-interactive the
            # `Auto-approving …` diagnostic still goes to stderr, so a log/human still
            # sees what was approved. (gh #77)
            return
        interrupt_data = chunk.get("interrupt", {})
        action_requests = interrupt_data.get("action_requests", [])

        print(f"\n{YELLOW}⚠ Action Required{RESET}")
        if action_requests:
            for i, action in enumerate(action_requests):
                # #69 taught the deepagents/langchain `ActionRequest` shape (tool name
                # under `action`). But a generic `interrupt(...)` carries arbitrary
                # values — a plain dict rendered `1. unknown` (dropping e.g. `question`)
                # and a bare string raised `'str' has no attribute 'get'`. Render ANY
                # payload actionably (gh #82); the structured tool path is unchanged.
                label, args_preview = format_interrupt_request(action)
                print(f"  {DIM}{i + 1}. {label}{RESET}")
                _print_approval_preview(args_preview)
        else:
            # No structured action_requests. A bare-string/scalar interrupt loses its
            # payload upstream (core's AG-UI adapter JSON-decodes it to `{}`), but if a
            # raw value survives on the frame, surface it; otherwise say so, so the user
            # never approves against a blank, contentless banner. (gh #82)
            raw = interrupt_data.get("value")
            if raw is None:
                raw = interrupt_data.get("interrupt")
            if raw in (None, "", {}, []):
                print(f"  {DIM}(no action details provided){RESET}")
            else:
                label, args_preview = format_interrupt_request(raw)
                print(f"  {DIM}{label}{RESET}")
                _print_approval_preview(args_preview)

    elif status == "complete":
        print_chunk._streaming_text = False  # turn over; next turn starts a fresh marker

    elif status == "error":
        print_chunk._streaming_text = False
        error_msg = chunk.get("error", "Unknown error")
        if _QUIET:
            # Keep stdout clean for the pipe; errors go to stderr. (gh #53)
            print(f"Error: {error_msg}", file=sys.stderr)
        else:
            print(f"\n{RED}✗ Error: {error_msg}{RESET}")


# Whether the current AI turn has already emitted its leading cyan bullet. Tracked
# across per-chunk print_chunk() calls so a token-streamed reply gets one marker,
# not one per token (gh #34). Reset on any non-text event and at each turn start.
print_chunk._streaming_text = False
# The node whose tokens are currently streaming, so verbose mode prints the
# [node] label once per run instead of before every token (gh #40).
print_chunk._streaming_node = None


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


def handle_interrupt_input(num_actions: int = 1, is_generic: bool = False) -> Any:
    """
    Handle user input for an interrupt using arrow key navigation, returning the
    value to resume the graph with (the ``command.resume`` payload).

    Args:
        num_actions: Number of pending tool calls that need decisions
        is_generic: True for a generic/scalar ``interrupt(value)`` (gh #99), False
            for a deepagents tool-REVIEW interrupt.

    Returns:
        For a deepagents tool-review interrupt, a ``{"decisions": [...]}`` envelope
        (unchanged — the tool-review protocol). For a generic interrupt, the RAW
        value the user provides, UNWRAPPED, so ``value = interrupt("What is your
        name?")`` gets exactly that value back instead of a ``{"decisions": [...]}``
        envelope it never asked for (gh #99).
    """
    # The approval menu is arrow-key driven, so it needs a real terminal on stdin.
    # Without this guard a piped / CI / cron run (`… "do X" </dev/null`) printed the
    # cursor-hide escape and the whole menu to STDOUT — violating the contract that a
    # piped single-shot run carries only the agent's reply — and then crashed inside
    # get_key(): `termios.tcgetattr()` raises on a non-tty, surfacing as a cryptic
    # `Error: (25, 'Inappropriate ioctl for device')`. Check BEFORE anything is
    # printed, and fail loudly rather than auto-approving: an interrupt() is a request
    # for human review, so silently approving an action nobody saw is worse than
    # stopping. `--no-interactive` remains the documented way to opt into
    # auto-approval, and the message points at it. (gh #86)
    if not _is_a_tty(sys.stdin):
        print(
            "Error: approval required but stdin is not a terminal.\n"
            "Re-run with --no-interactive to auto-approve pending actions.",
            file=sys.stderr,
        )
        sys.exit(1)

    if is_generic:
        # A generic interrupt() asks for an arbitrary value, not tool approval, so
        # "Approve/Reject" have no meaning and wrapping the answer in
        # {"decisions": [...]} corrupts it (gh #99). Collect a raw value and resume
        # with it UNWRAPPED. JSON is attempted first so a structured value (number,
        # list, dict) survives; a non-JSON entry is used verbatim as a string — the
        # canonical `value = interrupt("What is your name?")` case, where the user
        # types `Alice` and the agent must receive `"Alice"`, not an envelope.
        options = ["Provide a response", "Exit"]
        choice = select_option(options, "How would you like to respond?")
        if choice != 0:
            sys.exit(0)
        raw = input(make_prompt("❯", BLUE))
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    options = [
        "Approve all actions",
        "Reject all actions",
        "Provide custom decision (JSON)",
        "Exit",
    ]

    choice = select_option(options, "How would you like to proceed?")

    if choice == 0:
        # Approve decision for each pending action
        return {"decisions": [{"type": "approve"} for _ in range(num_actions)]}
    elif choice == 1:
        # Reject decision for each pending action
        return {"decisions": [{"type": "reject"} for _ in range(num_actions)]}
    elif choice == 2:
        print("Enter your decision as JSON (will be applied to all actions):")
        json_str = input(make_prompt("❯", BLUE)).strip()
        try:
            decision = json.loads(json_str)
            return {"decisions": [decision for _ in range(num_actions)]}
        except json.JSONDecodeError as e:
            print(f"{RED}⏺ Invalid JSON: {e}{RESET}")
            return {"decisions": [{"type": "reject"} for _ in range(num_actions)]}
    else:
        sys.exit(0)


def print_help():
    """Print formatted help information."""
    print(f"\n{BOLD}{BRIGHT_CYAN}Commands{RESET}")
    print(f"{DIM}{'─' * 40}{RESET}")

    # Get all registered commands and display them
    commands = command_registry.all_commands()
    for cmd in sorted(commands, key=lambda c: c.name):
        aliases_str = ""
        if cmd.aliases:
            # Each alias as its own cyan "/x" token. The old
            # `…join([""] + aliases)[4:]` sliced into the leading ANSI escape,
            # leaking a literal "36m" and bleeding color (gh #-dogfood).
            aliases_str = "".join(f", {CYAN}/{alias}{RESET}" for alias in cmd.aliases)
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

    print(f"\n{BOLD}{BRIGHT_CYAN}Session Status{RESET}")
    print(f"{DIM}{'─' * 30}{RESET}")
    print(f"  {DIM}Agent:{RESET}       {agent_name}")
    print(f"  {DIM}Thread ID:{RESET}   {thread_id[:8]}...")
    # No "Mode: async/sync" line: since ADR 0003 there is exactly one (async) path,
    # so the value was reporting a distinction the runtime does not have (gh #88).
    print(f"  {DIM}Verbose:{RESET}     {'on' if verbose else 'off'}")
    print(f"  {DIM}CWD:{RESET}         {os.getcwd()}")
    # Session persistence, when active (gh #102): so it's discoverable and honored-as-
    # advertised (persistence on/off, durable store path).
    session_info = config.get("_session_info")
    if isinstance(session_info, dict) and session_info.get("persist"):
        durable = session_info.get("durable")
        print(f"  {DIM}Persist:{RESET}     on ({'durable' if durable else 'graph checkpointer'})")
        if session_info.get("store"):
            print(f"  {DIM}Store:{RESET}       {session_info['store']}")
    elif isinstance(session_info, dict) and session_info.get("read_only"):
        print(f"  {DIM}Persist:{RESET}     off (--no-persist: read-only resume, nothing saved)")
    print()
    return None


def _live_resolved_report(config: Dict[str, Any], context: Dict[str, Any]) -> str:
    """Render the full resolved-config diagnostic from LIVE runtime state (gh #97).

    Bare ``/config`` used to print ``_resolved_config_report`` — a string frozen at
    startup — so after a runtime mutation (``/verbose``, ``/config verbose on``,
    ``/reset``) it contradicted ``/status``, the single-key ``/config <key>`` read, and
    even its own ``✓ Set`` line, and mislabelled an overridden value ``[default]``.

    Instead we re-render the ONE ``describe()`` diagnostic from the ``CodeConfig``
    resolved at startup, overlaid with live state: ``verbose`` from
    ``context["verbose"]`` and the live ``[configurable]`` values (so a ``/reset``
    thread_id shows through). The stored cfg keeps its startup-frozen
    ``_sources``/``_toml_paths``, so this RE-RENDERS rather than RE-RESOLVES — it can't
    pick up the ``LANGSTAGE_WORKSPACE_ROOT`` that ``apply_workspace`` self-publishes, so
    the #64 fix still holds and static keys keep their true provenance.

    A field changed at runtime is relabelled ``[override]`` (never ``[default]``),
    reusing ``describe()``'s own source vocabulary so the live view agrees with
    ``/status`` and ``/config <key>``. Falls back to the frozen snapshot string only
    when the resolved cfg is unavailable (e.g. a hand-built test context).
    """
    # The session-persistence block (gh #108), computed at startup, appended to every
    # rendering so /config surfaces persistence exactly as --show-config does.
    persist_block = config.get("_persist_diagnostic")

    resolved_cfg = config.get("_resolved_config")
    if resolved_cfg is None:
        # No live cfg to render from: honour the startup snapshot as-is, and only
        # re-resolve if even that is absent (never re-resolve otherwise — gh #64).
        report = config.get("_resolved_config_report")
        if report is None:
            from langstage_cli.config import CodeConfig

            report = CodeConfig.resolve().describe(
                omit_keys=_INERT_KEYS, configurable=config.get("configurable") or None
            )
        if persist_block:
            report += "\n" + persist_block
        return report

    # Overlay the one runtime-mutable field (verbose) onto a copy, keeping every static
    # key's frozen provenance. Relabel it [override] only when it actually differs from
    # the startup-resolved value, so an unchanged value keeps its true source.
    live_cfg = copy.copy(resolved_cfg)
    live_cfg._sources = dict(resolved_cfg.sources)
    live_verbose = context.get("verbose", resolved_cfg.verbose)
    if live_verbose != resolved_cfg.verbose:
        live_cfg.verbose = live_verbose
        live_cfg._sources["verbose"] = "override"

    # Overlay live [configurable] values onto the startup key set, so /reset's new
    # thread_id shows through while the table's shape stays byte-identical to startup
    # (a key absent at startup — e.g. an auto thread_id when the TOML set none — stays
    # hidden, keeping /config's table matching --show-config's).
    snap_conf = config.get("_snap_configurable")
    live_conf = config.get("configurable") or {}
    overlaid = None
    if isinstance(snap_conf, dict) and snap_conf:
        overlaid = {k: live_conf.get(k, v) for k, v in snap_conf.items()}

    report = live_cfg.describe(omit_keys=_INERT_KEYS, configurable=overlaid)
    if persist_block:
        report += "\n" + persist_block
    return report


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

        # Full resolved view — the COMPLETE describe() diagnostic (fields + source +
        # env/TOML keys + the [configurable] table), RE-RENDERED from live state so it
        # reflects runtime mutations (/verbose, /config verbose on, /reset) and agrees
        # with /status and /config <key> instead of contradicting them (gh #97). This
        # re-renders the startup-resolved cfg (frozen provenance) rather than
        # re-resolving, so workspace_root's source stays truthful — the #64 fix holds —
        # and the startup table still matches --show-config byte-for-byte (gh #64, #66).
        report = _live_resolved_report(config, context)
        for line in report.splitlines():
            print(f"  {line}")
        print()
    else:
        parts = args.split(maxsplit=1)
        if len(parts) == 1:
            key = parts[0]
            configurable = config.get("configurable", {})
            if key in configurable:
                print(f"\n{CYAN}{key}:{RESET} {configurable[key]}\n")
            elif key in ("verbose", "stream_mode"):
                # async_mode is gone from this map: it was a dead knob whose only
                # remaining job was to report itself back to the user (gh #88).
                print(f"\n{CYAN}{key}:{RESET} {context.get(key)}\n")
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


async def _aget_state_via_durable_saver(graph: Any, store_path: str, config: Dict[str, Any]) -> Any:
    """Read graph state through the ASYNC checkpointer API on a freshly-opened saver.

    In the shipped persist-on default (gh #102) the durable checkpointer is an
    ``AsyncSqliteSaver`` opened INSIDE each turn's own event loop and closed when the turn
    ends (its aiosqlite connection can't cross loops) — so by the time ``/history`` runs
    there is no live saver. A SYNC ``graph.get_state()`` against that async-only, already-
    closed saver schedules ``aget_tuple()`` as a never-awaited coroutine and surfaces
    ``Event loop is closed`` plus a ``RuntimeWarning`` (gh #106). Mirror
    ``_run_turn_persistent``: open a fresh saver over the SAME SQLite file in THIS loop and
    read through the async API, so ``/history`` renders on a normal persist-on run.
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with AsyncSqliteSaver.from_conn_string(store_path) as saver:
        graph.checkpointer = saver
        return await graph.aget_state(config)


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

    # The durable per-workspace store (gh #102), when persistence is on: read history
    # through the async saver on its own loop (gh #106) instead of the sync get_state,
    # which can't drive the closed AsyncSqliteSaver.
    session_info = config.get("_session_info") if isinstance(config, dict) else None
    # (A read-only resume, gh #147, has a store too — its throwaway snapshot.)
    store_path = session_info.get("store") if isinstance(session_info, dict) else None

    try:
        # Prefer the async read on the durable store; otherwise the sync path still works
        # (an in-memory checkpointer, e.g. --no-persist, drives get_state fine).
        state = None
        if store_path:
            state = asyncio.run(_aget_state_via_durable_saver(graph, store_path, config))
        elif hasattr(graph, "get_state"):
            state = graph.get_state(config)
        else:
            print(f"{DIM}Graph does not support state retrieval{RESET}")
            return None

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
    """Toggle or set verbose output mode.

    Bare ``/verbose`` flips the current value — honouring the advertised "Toggle
    verbose output mode" contract (gh #79) — while ``/verbose on|off`` sets it
    explicitly. Either way the new state is reported.
    """
    verbose = context.get("verbose", False)
    if args:
        if args.lower() in ("on", "true", "1"):
            verbose = True
        elif args.lower() in ("off", "false", "0"):
            verbose = False
        else:
            print(f"{YELLOW}Usage: /verbose [on|off]{RESET}")
            return None
    else:
        # Bare /verbose toggles, matching the "Toggle" description (gh #79).
        verbose = not verbose
    context["verbose"] = verbose
    print(f"{GREEN}✓ Verbose mode {'enabled' if verbose else 'disabled'}{RESET}")
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


async def run_single_turn_agui(
    agent,
    message: str,
    thread_id: str,
    interactive: bool = True,
    verbose: bool = False,
) -> tuple[float, bool]:
    """Stream a turn through the in-process AG-UI adapter. Returns
    ``(elapsed_seconds, had_error)`` — ``had_error`` is True if any frame reported
    ``status == "error"``, so a single-shot caller can exit non-zero (gh #47).
    adapter, rendering with the same ``print_chunk``. Text + tool calls/results
    reach parity with the default path (and tool *results* are also shown).

    Interrupts are fully supported (ADR 0002 gate 2, resolved): an interrupt is
    displayed, the decision is collected, and the turn resumes via the AG-UI
    adapter's ``forwarded_props.command.resume`` — mirroring the default path's
    interrupt loop, including the ``--no-interactive`` auto-approve behavior.
    """
    from langstage_cli.agui_stream import agui_stream_updates

    print_chunk._streaming_text = False  # fresh marker state per turn (gh #34)
    start_time = time.time()
    had_error = False
    resume = None  # first pass sends the message; later passes carry the decision

    while True:
        has_interrupt = False
        num_pending_actions = 0
        is_generic_interrupt = False
        first_chunk = True
        # No spinner in quiet mode — its \r animation is terminal-only chrome that
        # would corrupt a piped reply. (gh #53)
        spinner = None if _QUIET else Spinner("Thinking")
        if spinner:
            spinner.start()
        try:
            # Silence ag_ui_langgraph's benign "failed to parse resume_input as JSON"
            # WARNING that fires on a free-text interrupt() resume, so it never appears
            # above the (correct) answer on the interactive HITL path. The filter is
            # surgical — only that one record — so wrapping the whole turn is harmless
            # (the warning can only fire on a resume pass anyway). (gh #103)
            with _quiet_agui_resume_json_warning():
                async for chunk in agui_stream_updates(agent, message, thread_id, resume=resume):
                    if first_chunk:
                        if spinner:
                            spinner.stop()
                        first_chunk = False
                    print_chunk(chunk, verbose=verbose)
                    if chunk.get("status") == "error":
                        had_error = True
                    if chunk.get("status") == "interrupt":
                        has_interrupt = True
                        interrupt_data = chunk.get("interrupt", {})
                        action_requests = interrupt_data.get("action_requests", [])
                        num_pending_actions = len(action_requests) if action_requests else 1
                        is_generic_interrupt = _is_generic_interrupt(action_requests)
        finally:
            if spinner:
                spinner.stop()

        if has_interrupt and interactive:
            resume = handle_interrupt_input(num_pending_actions, is_generic=is_generic_interrupt)
        elif has_interrupt and is_generic_interrupt:
            # --no-interactive on a generic interrupt() (gh #99): there is no action
            # to "approve", and resuming with a {"decisions": [...]} envelope would
            # hand the agent an approval it never asked for (`Hello, {'decisions':
            # [...]}!`). Nobody can supply the requested value non-interactively, so
            # resume with an empty value — a value the agent could plausibly have
            # received — keeping the "run to completion" contract without lying.
            _status(
                f"{DIM}Auto-resuming generic interrupt with an empty value "
                f"(--no-interactive){RESET}"
            )
            resume = ""
        elif has_interrupt:
            # --no-interactive: auto-approve and resume so the agent runs to
            # completion (same behavior as the default path, gh #32).
            _status(
                f"{DIM}Auto-approving {num_pending_actions} pending action(s) "
                f"(--no-interactive){RESET}"
            )
            decisions = [{"type": "approve"} for _ in range(num_pending_actions)]
            resume = {"decisions": decisions}
        else:
            break

    return time.time() - start_time, had_error


async def _run_turn_persistent(
    graph: Any,
    sqlite_path: Path,
    message: str,
    thread_id: str,
    interactive: bool,
    verbose: bool,
    *,
    name: str,
    session_config: Any,
) -> tuple[float, bool]:
    """Run ONE turn with a durable per-workspace SQLite checkpointer (gh #102).

    The AG-UI streaming path is async-only, and langgraph's SYNC ``SqliteSaver`` raises
    ``NotImplementedError`` for the async checkpointer methods the graph invokes — so the
    durable saver here is the async-native ``AsyncSqliteSaver`` over the same SQLite file
    (see the PR notes). Its aiosqlite connection is bound to the event loop it is opened
    in and cannot cross loops, and the CLI runs each turn in its own ``asyncio.run``; so
    the saver is opened and closed WITHIN this turn's loop, and the AG-UI agent is rebuilt
    here so it wraps the graph with this turn's live checkpointer. Persistence is durable
    regardless — the SQLite FILE is the store, keyed by ``thread_id`` — so reopening per
    turn reads back every prior turn's state. The ``async with`` guarantees a clean
    open/close even on Ctrl-C, so state is never corrupted.
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from langstage_cli.agui_stream import build_session_agent

    async with AsyncSqliteSaver.from_conn_string(str(sqlite_path)) as saver:
        # Attach the durable saver, but GUARD the assignment the way _ensure_checkpointer
        # does. A wrong-type agent object (graph = None / a dict / an int — not a compiled
        # graph) rejects the attribute set, and on this default persist-on path that used
        # to leak a cryptic `AttributeError: 'X' object has no attribute 'checkpointer'`
        # BEFORE any validation ran. Swallowing it lets the object fall through to
        # build_session_agent -> build_agent, whose validator raises the clean, actionable
        # `TypeError: build_agent expected a compiled LangGraph graph ...` — the same
        # message --verify / --no-persist already show. A valid graph accepts the
        # assignment, so durable persistence is unchanged. (gh #117)
        try:
            graph.checkpointer = saver
        except Exception:  # noqa: BLE001 - defer to build_agent's clean type validation below
            pass
        agent = build_session_agent(graph, name=name, config=session_config)
        return await run_single_turn_agui(agent, message, thread_id, interactive, verbose)


def run_conversation_loop(
    graph,
    config: Dict[str, Any],
    agent_name: str = "Agent",
    agent_description: Optional[str] = None,
    interactive: bool = True,
    verbose: bool = False,
    stream_mode: str = "updates",
    initial_message: Optional[str] = None,
    single_shot: bool = False,
    persist_sqlite_path: Optional[Path] = None,
    on_turn: Optional[Any] = None,
):
    """
    Run a continuous conversation loop with the LangGraph agent.
    Styled after Claude Code / nanocode.

    If single_shot is True and initial_message is provided, exit after processing.
    """
    # Set up tab completion for slash commands
    setup_readline_completion()

    # Header + welcome are interactive chrome; a scriptable single-shot run omits
    # them so the pipe gets only the reply. (gh #53)
    if not _QUIET:
        # Print box-drawn header with agent name and description
        print_header_box(agent_name, os.getcwd(), agent_description)

        # Print welcome message with tips
        print_welcome()

    # Create command context (mutable dict that commands can modify)
    command_context = {
        "graph": graph,
        "config": config,
        "agent_name": agent_name,
        "interactive": interactive,
        "verbose": verbose,
        "stream_mode": stream_mode,
    }

    # Build the in-process AG-UI agent ONCE per session (checkpointer attached by
    # the core bridge) so multi-turn memory persists. Since langstage-core 1.0 the
    # AG-UI adapter is the only streaming path.
    thread_id = ""
    configurable: Dict[str, Any] = {}
    if isinstance(config, dict):
        configurable = dict(config.get("configurable", {}))
        thread_id = configurable.get("thread_id", "") or ""
    from langstage_cli.agui_stream import build_session_agent

    # Forward the resolved `[configurable]` table (minus thread_id, which the
    # adapter sets per-run) to the graph, so keys beyond thread_id — the documented
    # way to parameterize an agent — actually reach it instead of being silently
    # dropped while /config advertises them. (gh #57)
    configurable.pop("thread_id", None)
    session_config = {"configurable": configurable} if configurable else None

    # When persisting (gh #102) the durable AsyncSqliteSaver must be opened inside each
    # turn's own event loop (its aiosqlite connection can't cross loops), so the agent is
    # (re)built per turn there — skip the once-per-session build below. Otherwise build it
    # once, as before, so the in-memory checkpointer's within-session memory persists.
    agui_agent = None
    if persist_sqlite_path is None:
        try:
            agui_agent = build_session_agent(graph, name=agent_name, config=session_config)
        except RuntimeError as e:
            _status(f"{RED}⏺ {e}{RESET}")
            return

    def run_one(msg: str) -> tuple:
        """Run one turn, persisting to the durable store when configured."""
        if on_turn is not None:
            on_turn(msg)  # bump the session index (updated timestamp / first-message snippet)
        if persist_sqlite_path is not None:
            return asyncio.run(
                _run_turn_persistent(
                    graph,
                    persist_sqlite_path,
                    msg,
                    thread_id,
                    interactive,
                    verbose,
                    name=agent_name,
                    session_config=session_config,
                )
            )
        return asyncio.run(run_single_turn_agui(agui_agent, msg, thread_id, interactive, verbose))

    # Process initial message if provided
    if initial_message:
        if not _QUIET:
            print(f"\n{BOLD}{BRIGHT_BLUE}You{RESET}")
            print(f"{initial_message}")
            print()

        duration, had_error = run_one(initial_message)
        if _QUIET:
            # Only the reply reached stdout (streamed with end=""); cap it with a
            # single newline so the piped output ends cleanly. No timing line. (gh #53)
            print()
        else:
            print_timing(duration, verbose)
            print()

        # Exit after single-shot execution. Propagate the turn's error status so
        # main() can exit non-zero — a single-shot/piped caller must be able to tell
        # a failed run from a success (gh #47).
        if single_shot:
            return had_error

    # Main conversation loop.
    #
    # This loop also consumes a message on piped (non-TTY) stdin — `echo "hi" |
    # langstage-cli` reads the line via input() and runs one turn, then EOF ends the
    # loop. That path is scriptable, not an interactive session, so when _QUIET is
    # set (via -q or the non-TTY auto-quiet above) every decorative element is
    # suppressed: the `····` separators, the `❯` prompt (input() with no prompt, so
    # no glyph and no bracketed-paste bytes), the `Nms` timing line, and the
    # `Goodbye!`. What reaches stdout is then exactly the reply the MESSAGE-arg path
    # emits. A real TTY session has _QUIET == False, so all of it renders unchanged.
    # (gh #93)
    while True:
        try:
            if not _QUIET:
                print(separator("dots"))
            user_input = input("" if _QUIET else make_prompt()).strip()

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

            if not _QUIET:
                print()  # Space before response

            # Run the agent (AG-UI is the only streaming path since langstage-core 1.0)
            duration, _ = run_one(user_input)
            if _QUIET:
                # Scriptable path (gh #93): cap the streamed reply with a single
                # newline and emit no `Nms` timing line — exactly what the quiet
                # MESSAGE-arg single-shot path does, so a piped one-liner produces
                # byte-identical output whether the message is an arg or on stdin.
                print()
            else:
                print_timing(duration, verbose)
                print()

        except (EOFError, KeyboardInterrupt):
            break
        except Exception as err:
            print(f"\n{RED}✗ Error: {err}{RESET}\n")

    # Print goodbye message (interactive chrome — omitted on the scriptable/quiet
    # path so a piped consumer never sees a trailing `Goodbye!`, gh #93).
    if not _QUIET:
        print_goodbye()


# The README's stdlib StateGraph example — needs only langgraph (a base dependency),
# so `langstage-cli init` scaffolds a graph that runs keyless with zero extra installs.
# This is the BYO-graph analogue of --demo: a real, user-OWNED graph the adopter edits,
# not a bundled runner. (gh #104)
_INIT_AGENT_PY = """\
# my_agent.py — a minimal LangGraph agent, runnable keyless (needs only langgraph,
# a base dependency of langstage-cli). Edit `respond` to build your own agent, or
# swap the node for a model call (see the langstage-cli README).
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
"""

_INIT_TOML = """\
# Written by `langstage-cli init`. Points the CLI at your agent so a bare
# `langstage-cli "Hello!"` runs it — no -a needed.
[agent]
spec = "my_agent.py:graph"
"""

_INIT_AGENT_FILENAME = "my_agent.py"
_INIT_TOML_FILENAME = "langstage.toml"


def scaffold_init(target_dir: Path, force: bool = False) -> int:
    """Scaffold a runnable starter agent + ``langstage.toml`` into ``target_dir`` (gh #104).

    Writes ``my_agent.py`` (the README's keyless stdlib example) and a ``langstage.toml``
    wired to it, so the very next ``langstage-cli "Hello!"`` runs the user's OWN graph
    with no ``-a`` and no hand-editing — the missing rung between ``--demo`` ("see it
    work") and "run my own graph". Refuses to clobber an existing file unless ``force``
    (never silently overwrites a user's agent), printing exactly what it did. Returns a
    process exit code: 0 on success, 1 if it refused because a file already exists.
    """
    agent_path = target_dir / _INIT_AGENT_FILENAME
    toml_path = target_dir / _INIT_TOML_FILENAME

    if not force:
        existing = [p.name for p in (agent_path, toml_path) if p.exists()]
        if existing:
            _status(f"{RED}⏺ Error: refusing to overwrite existing {', '.join(existing)}.{RESET}")
            _status(f"{DIM}Re-run with --force to overwrite.{RESET}")
            return 1

    target_dir.mkdir(parents=True, exist_ok=True)
    agent_path.write_text(_INIT_AGENT_PY, encoding="utf-8")
    toml_path.write_text(_INIT_TOML, encoding="utf-8")

    print(f"{GREEN}✓{RESET} Wrote {_INIT_AGENT_FILENAME} and {_INIT_TOML_FILENAME}")
    print(f'{DIM}Next:{RESET}  langstage-cli "Hello!"        # runs your new agent')
    print(f"{DIM}      langstage-cli --verify       # preflight it (one real turn){RESET}")
    return 0


def _resolve_resume_thread(
    workspace: Path, continue_session: bool, resume_id: Optional[str]
) -> Optional[str]:
    """The thread ``--continue`` / ``--resume <id>`` targets, or ``None`` for a fresh one.

    ``--continue`` with nothing to continue notes it and starts fresh; an unknown
    ``--resume`` id is an error (exit 1).
    """
    if continue_session:
        thread = sessions.most_recent_thread(workspace)
        if thread is None:
            _status(f"{DIM}⏺ No prior session for this workspace — starting a fresh one.{RESET}")
        return thread
    if resume_id is not None:
        thread = sessions.resolve_thread(workspace, resume_id)
        if thread is None:
            _status(f"{RED}⏺ Error: no session matching '{resume_id}' for this workspace.{RESET}")
            _status(f"{DIM}Run --list-sessions to see available sessions.{RESET}")
            sys.exit(1)
        return thread
    return None


def _snapshot_session_store(workspace: Path) -> Optional[Path]:
    """Copy the workspace's durable store to a throwaway temp file for a read-only
    resume (``--no-persist`` + ``--continue`` / ``--resume``, gh #147).

    The turn runs against the COPY, so it sees the full prior checkpoint (including a
    pending interrupt) while the real store is never written. The copy is removed at
    exit. Returns ``None`` if there is no store to read (nothing to resume from).
    """
    import atexit
    import shutil
    import sqlite3
    import tempfile

    src = sessions.sessions_dir() / f"{sessions.workspace_key(workspace)}.sqlite"
    if not src.is_file():
        return None
    tmp_dir = Path(tempfile.mkdtemp(prefix="langstage-readonly-"))
    atexit.register(shutil.rmtree, tmp_dir, ignore_errors=True)
    dst = tmp_dir / "session.sqlite"
    # sqlite's online backup: a consistent copy even with a WAL sidecar present, and it
    # only READS the source.
    src_conn = sqlite3.connect(f"{src.resolve().as_uri()}?mode=ro", uri=True)
    try:
        dst_conn = sqlite3.connect(dst)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    return dst


def _resolve_persist(flag: Optional[bool], toml_config: dict) -> bool:
    """Resolve whether to persist this session: ``--persist/--no-persist`` flag >
    ``LANGSTAGE_PERSIST`` env > ``[session] persist`` TOML > default ON (gh #102).

    Default-on is what makes ``--continue`` useful: a fresh run leaves a session behind
    to resume. ``--continue`` / ``--resume`` force it on over the env/TOML layers, except
    against an explicit ``--no-persist``, which makes the resume read-only (gh #147) —
    both handled by the caller.
    """
    if flag is not None:
        return flag
    env = os.getenv("LANGSTAGE_PERSIST")
    if env is not None and env != "":
        return env.strip().lower() in ("1", "true", "yes", "on")
    toml_val = config_module.get(toml_config, "session.persist")
    if isinstance(toml_val, bool):
        return toml_val
    return True


def _persist_source_label(flag: Optional[bool], toml_config: dict) -> str:
    """The ``[source]`` for the resolved persist value — mirrors ``_resolve_persist``'s
    chain (``--persist/--no-persist`` > ``LANGSTAGE_PERSIST`` > ``[session] persist`` >
    default) so ``--show-config`` attributes it just like every other key (gh #108)."""
    if flag is not None:
        return "override"
    env = os.getenv("LANGSTAGE_PERSIST")
    if env is not None and env != "":
        return "env:LANGSTAGE_PERSIST"
    if isinstance(config_module.get(toml_config, "session.persist"), bool):
        return "toml (session.persist)"
    return "default"


def _sessions_store_source_label() -> str:
    """The ``[source]`` for the sessions store directory (gh #108)."""
    if os.getenv(sessions._SESSIONS_DIR_ENV):
        return f"env:{sessions._SESSIONS_DIR_ENV}"
    if os.getenv("LANGSTAGE_CONFIG_HOME"):
        return "env:LANGSTAGE_CONFIG_HOME"
    if os.getenv("DEEPAGENTS_CONFIG_HOME"):
        return "env:DEEPAGENTS_CONFIG_HOME"
    return "default"


def _persist_diagnostic_block(flag: Optional[bool], toml_config: dict, workspace: Path) -> str:
    """Render the session-persistence block appended to the config diagnostic (gh #108).

    Session persistence is a headline feature (``--continue``/``--resume``, on by
    default) with a full resolution chain, yet only interactive ``/status`` surfaced it —
    the scriptable ``--show-config`` never did, so #102's item 3 (surface it in BOTH) was
    half-done. ``persist`` isn't a ``CodeConfig`` field (it's resolved out-of-band by
    ``_resolve_persist``), so it can't flow through core's ``describe()``; this renders it
    in the SAME style right after it, and both ``--show-config`` and interactive
    ``/config`` append this one block so the two can't disagree. The store path is
    computed WITHOUT creating the directory (a read-only diagnostic must have no side
    effects).
    """
    persist_val = _resolve_persist(flag, toml_config)
    source = _persist_source_label(flag, toml_config)
    lines = ["", "  Session persistence:"]
    lines.append(
        f"  {'persist':<16} = {str(persist_val):<26} [{source}]"
        "   (env: LANGSTAGE_PERSIST, toml: session.persist)"
    )
    if persist_val:
        # sessions_dir() reads env only (no mkdir); build the path by hand so the
        # diagnostic never touches the filesystem the way db_path() would.
        store = sessions.sessions_dir() / f"{sessions.workspace_key(workspace)}.sqlite"
        lines.append(
            f"  {'sessions_store':<16} = {str(store):<26} [{_sessions_store_source_label()}]"
        )
    return "\n".join(lines)


def _format_ts(ts: Any) -> str:
    """Format an epoch timestamp for the session list; ``?`` if unparseable."""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return "?"


def _print_sessions(workspace: Path) -> None:
    """Print this workspace's persisted sessions, most-recent first (gh #102)."""
    rows = sessions.list_sessions(workspace)
    if not rows:
        print(f"{DIM}No sessions yet for this workspace.{RESET}")
        print(f'{DIM}Run a turn (e.g. langstage-cli "hi") to start one.{RESET}')
        return
    print(f"\n{BOLD}{BRIGHT_CYAN}Sessions{RESET} {DIM}({workspace}){RESET}")
    print(f"{DIM}{'─' * 40}{RESET}")
    for tid, entry in rows:
        when = _format_ts(entry.get("updated"))
        first = entry.get("first_message") or f"{DIM}(no message yet){RESET}"
        print(f"  {CYAN}{tid[:8]}{RESET}  {DIM}{when}{RESET}  {first}")
    print(f"\n{DIM}Resume with:  langstage-cli --resume <id>  (or -c for the most recent){RESET}\n")


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
    # DEPRECATED (gh #88): a no-op since ADR 0003 collapsed every turn onto the single
    # async AG-UI path — `--sync-mode` and `--async-mode` produce byte-identical output.
    # Kept hidden + accepted so existing invocations don't hard-error; a one-line notice
    # fires when either spelling is passed. Same posture as --stream-mode (gh #62).
    hidden=True,
    help="(deprecated: no effect — every turn streams through the one async path).",
)
@click.option(
    "--stream-mode",
    type=click.Choice(["auto", "updates", "messages"]),
    # DEPRECATED (gh #62): a no-op since the AG-UI streaming migration — all three
    # modes render identically. Kept hidden + accepted so existing `--stream-mode X`
    # invocations don't hard-error; a one-line notice fires when it is passed.
    hidden=True,
    help="(deprecated: no effect since the AG-UI streaming migration).",
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
    "--agui",
    is_flag=True,
    default=False,
    # DEPRECATED (gh #88): never read. It once opted into the experimental in-process
    # AG-UI adapter "instead of the built-in event parser"; since langstage-core 1.0
    # that adapter is the ONLY streaming path, so there is nothing left to opt into
    # (and the [agui] extra is a redundant alias — CHANGELOG 0.6.1). Hidden + accepted
    # so existing invocations don't hard-error, with a one-line notice.
    hidden=True,
    help="(deprecated: no effect — the AG-UI adapter is the only streaming path).",
)
@click.option(
    "--show-config",
    "show_config",
    is_flag=True,
    default=False,
    help="Print the resolved configuration (defaults < langstage.toml < env < CLI) and exit",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    default=False,
    help="Scriptable single-shot output: suppress the header, spinner, tool "
    "chatter, timing, and color, and emit only the agent's reply. Auto-enabled "
    "when a single-shot run is piped (stdout is not a TTY).",
)
@click.option(
    "--verify",
    "verify_agent",
    is_flag=True,
    default=False,
    help="Preflight the configured agent: run ONE real turn and exit 0 if it "
    "completed cleanly, non-zero otherwise. Catches a missing key / broken tool "
    "/ bad graph before you rely on it (e.g. in CI).",
)
@click.option(
    "--continue",
    "-c",
    "continue_session",
    is_flag=True,
    default=False,
    help="Resume the MOST RECENT session for this workspace and keep the "
    "conversation going (cross-invocation memory). Starts fresh if there is none.",
)
@click.option(
    "--resume",
    "resume_id",
    is_flag=False,
    flag_value=_RESUME_LIST_SENTINEL,
    default=None,
    help="Resume a specific session by id (as shown by --list-sessions). Bare "
    "--resume lists recent sessions for this workspace to pick from.",
)
@click.option(
    "--list-sessions",
    "list_sessions",
    is_flag=True,
    default=False,
    help="List recent persisted sessions for this workspace and exit.",
)
@click.option(
    "--persist/--no-persist",
    "persist",
    default=None,
    help="Persist this session to a durable store so it can be continued later "
    "(default: on; also LANGSTAGE_PERSIST / [session] persist in langstage.toml). "
    "With --continue/--resume, --no-persist resumes read-only (nothing is saved).",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="For `init`: overwrite existing my_agent.py / langstage.toml (default: refuse).",
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
    agui: bool,
    show_config: bool,
    quiet: bool,
    verify_agent: bool,
    continue_session: bool,
    resume_id: Optional[str],
    list_sessions: bool,
    persist: Optional[bool],
    force: bool,
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
        langstage-cli init                      # scaffold my_agent.py + langstage.toml
        langstage-cli --show-config
        langstage-cli --verify -a my_agent.py   # preflight one real turn; exit 0/1
        langstage-cli "remember: I'm Kedar"     # persisted session
        langstage-cli -c "what's my name?"      # continue the most recent session
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

    # Scriptable output (gh #53, gh #93). Auto-enable quiet when stdout is not a TTY
    # AND the run is non-interactive input: an explicit single-shot (a MESSAGE arg,
    # -f/--file, or a --verify preflight) OR a message on piped stdin
    # (`echo "hi" | langstage-cli`). Piped stdin is non-interactive input — it is
    # scriptable too, so it must not leak the REPL's `····` rules, the `❯` prompt
    # (with bracketed-paste bytes), the `Nms` timing line or a trailing `Goodbye!`.
    # The gate is `stdin is not a tty`, so a LIVE terminal session (a human typing,
    # stdin IS a tty, no single-shot input) is never auto-quieted and its interactive
    # UX is unchanged; --quiet still forces quiet anywhere. Color is additionally
    # stripped whenever stdout is not a TTY, matching well-behaved CLIs.
    _is_tty = _is_a_tty(sys.stdout)
    _stdin_is_tty = _is_a_tty(sys.stdin)
    global _QUIET
    _QUIET = quiet or (
        (bool(message or prompt_file) or verify_agent or not _stdin_is_tty) and not _is_tty
    )
    if _QUIET or not _is_tty:
        _disable_ansi()

    # `langstage-cli init` — scaffold a runnable starter agent + langstage.toml, then
    # exit. Handled here (before any agent/config resolution) because init needs
    # neither: it just writes files into the current directory. (gh #104)
    if message == "init":
        sys.exit(scaffold_init(Path.cwd(), force=force))

    # --continue and --resume are mutually exclusive — they name two different threads
    # to resume, so passing both is a contradiction. (gh #102)
    if continue_session and resume_id is not None:
        _status(f"{RED}⏺ Error: --continue and --resume are mutually exclusive{RESET}")
        sys.exit(1)

    if demo:
        if agent_spec:
            _status(f"{RED}⏺ Error: --demo and -a/--agent are mutually exclusive{RESET}")
            sys.exit(1)
        # The keyless echo agent shipped with the shared core.
        agent_spec = "langstage_core.demo.stub:graph"

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
        "verbose": True if verbose else None,
    }

    # --stream-mode is deprecated and inert since the AG-UI streaming migration
    # (gh #62): accepted so scripts don't break, but say so once when it's passed.
    if stream_mode is not None:
        _status(
            f"{DIM}⏺ Note: --stream-mode is deprecated and has no effect "
            f"(streaming is uniform since the AG-UI migration).{RESET}"
        )

    # --async-mode/--sync-mode and --agui are deprecated and inert too (gh #88): ADR
    # 0003 collapsed every turn onto the one async AG-UI path, so neither flag has a
    # branch left to take. Same posture as --stream-mode above — accepted so existing
    # invocations don't hard-error, with one notice each.
    if use_async is not None:
        _status(
            f"{DIM}⏺ Note: --async-mode/--sync-mode is deprecated and has no effect "
            f"(every turn streams through the one async path).{RESET}"
        )
    if agui:
        _status(
            f"{DIM}⏺ Note: --agui is deprecated and has no effect "
            f"(the AG-UI adapter is the only streaming path).{RESET}"
        )

    if show_config:
        # The COMPLETE diagnostic — fields (server/web keys this surface ignores omitted,
        # gh #36) + the honored [configurable] table (gh #57/#66) — comes from the one
        # describe() renderer, so --show-config and /config can't disagree by construction.
        _toml, _ = config_module.load_config()
        _configurable = config_module.get(_toml, "configurable")
        _cfg = config_module.CodeConfig.resolve(toml_start=Path.cwd(), overrides=cli_overrides)
        _diag = _cfg.describe(
            omit_keys=_INERT_KEYS,
            configurable=_configurable if isinstance(_configurable, dict) else None,
        )
        # Session persistence isn't a CodeConfig field, so append it here (gh #108) — the
        # interactive /config appends the identical block, keeping the two in lock-step.
        _diag += "\n" + _persist_diagnostic_block(
            persist, _toml, Path(_cfg.workspace_root).expanduser().resolve()
        )
        print(_diag)
        return

    try:
        # Handle -f/--file option: read message from file
        if prompt_file and message:
            _status(f"{RED}⏺ Error: Cannot use both MESSAGE argument and -f/--file option{RESET}")
            sys.exit(1)

        if prompt_file:
            try:
                with open(prompt_file, "r", encoding="utf-8") as f:
                    message = f.read().strip()
                if not message:
                    _status(f"{RED}⏺ Error: File '{prompt_file}' is empty{RESET}")
                    sys.exit(1)
            except Exception as e:
                _status(f"{RED}⏺ Error reading file '{prompt_file}': {e}{RESET}")
                sys.exit(1)

        # Load TOML configuration (global + project, merged)
        try:
            toml_config, toml_sources = config_module.load_config()
        except config_module.ConfigError as e:
            _status(f"{RED}⏺ {e}{RESET}")
            sys.exit(1)

        # Resolve all standard settings through the shared chain in one shot:
        # CLI overrides > DEEPAGENT_* env > deepagents.toml > defaults.
        # (DEEPAGENT_AGENT_SPEC is canonical; DEEPAGENT_SPEC is a deprecated alias.)
        cfg = config_module.CodeConfig.resolve(
            toml_start=Path.cwd(),
            overrides=cli_overrides,
        )
        # Snapshot the resolved-config diagnostic NOW, before apply_workspace() below
        # self-publishes LANGSTAGE_WORKSPACE_ROOT into os.environ (ADR 0005). The
        # interactive /config used to re-resolve at display time, see the tool's own
        # published var, and misreport workspace_root's source as [env:...] — diverging
        # from --show-config (which runs before apply_workspace). Reuse this snapshot so
        # /config shows the true provenance. (gh #64)
        _snap_configurable = config_module.get(toml_config, "configurable")
        resolved_config_report = cfg.describe(
            omit_keys=_INERT_KEYS,
            configurable=_snap_configurable if isinstance(_snap_configurable, dict) else None,
        )
        final_spec = cfg.agent_spec
        final_graph_name_default = cfg.graph_name
        # stream_mode is deprecated and inert (gh #62) — it only ever comes from the
        # accepted-and-ignored flag now (env/TOML no longer resolve it), so there is
        # nothing to validate; it changes no rendering.
        final_stream_mode = cfg.stream_mode
        verbose = cfg.verbose
        # Whether a workspace root was explicitly configured (vs the default cwd);
        # cli chdirs into it only when it was, matching prior behavior.
        workspace_explicit = cfg.sources.get("workspace_root") != "default"

        # ---- Session persistence (gh #102) ----
        # The resolved workspace keys the per-workspace session store. Compute it from
        # the same workspace_root the run uses (before apply_workspace's chdir, which
        # fires only for an explicit root — so this is stable either way).
        workspace = Path(cfg.workspace_root).expanduser().resolve()

        # A bare --resume (or --list-sessions) just lists this workspace's sessions and
        # exits — no agent needed.
        if list_sessions or resume_id == _RESUME_LIST_SENTINEL:
            _print_sessions(workspace)
            return

        # Persistence is on by default so a fresh run can be continued later; disable via
        # --no-persist / LANGSTAGE_PERSIST=0 / [session] persist=false. --continue and
        # --resume imply it over the env/TOML layers (a CLI arg outranks them) — but an
        # explicit --no-persist on the same command line wins (gh #147): the session is
        # resumed READ-ONLY (prior context is read, nothing is written back).
        persist_flag = persist  # raw --persist/--no-persist (None if unset), for the diagnostic
        persist = _resolve_persist(persist, toml_config)
        resuming = continue_session or resume_id is not None
        read_only_resume = resuming and persist_flag is False
        if resuming and not read_only_resume:
            persist = True

        # If no spec provided, try the default agent
        if not final_spec:
            default_agent_path = Path(__file__).parent.parent / "examples" / "agent.py"
            if default_agent_path.exists():
                final_spec = f"{default_agent_path}:agent"
            else:
                _status(f"{RED}⏺ Error: No agent specified.{RESET}")
                _status(f"\n{DIM}Usage:{RESET}")
                _status("  langstage-cli path/to/agent.py:graph")
                _status("  langstage-cli mypackage.module:agent")
                _status(f"\n{DIM}Or set the LANGSTAGE_AGENT_SPEC environment variable{RESET}")
                sys.exit(1)

        # Resolve a relative file-path spec to an absolute path BEFORE we chdir into
        # the workspace root — otherwise it's looked up under workspace_root, not where
        # the file is. The BASE directory depends on WHERE the spec came from:
        #   - a spec from a discovered `langstage.toml` resolves against THAT toml's own
        #     directory (the project root the walk-up found), like a path in
        #     pyproject.toml / tsconfig — so the project runs identically from its root
        #     and any subdirectory (gh #116);
        #   - a spec from `-a` / `LANGSTAGE_AGENT_SPEC` (or the built-in default) stays
        #     cwd-relative — "the file is where the user typed the command" (gh #30).
        spec_toml_dir = (
            config_module.toml_dir_for(cfg, "agent_spec")
            if cfg.sources.get("agent_spec", "").startswith("toml")
            else None
        )
        if spec_toml_dir is not None:
            final_spec = _rebase_toml_file_spec(final_spec, spec_toml_dir)
        else:
            final_spec = _absolutize_file_spec(final_spec)

        # Apply the resolved workspace as the single source of truth (ADR 0005):
        # publish it (env + active) so the agent's tools can read workspace_root(),
        # and chdir into it (cli is single-process) when one was explicitly
        # configured. Runs AFTER _absolutize_file_spec so `-a my_agent.py` still
        # resolves against the invocation cwd, not the workspace (gh #30).
        apply_workspace(Path(cfg.workspace_root).expanduser(), chdir=workspace_explicit)

        # Load the graph with a spinner (both are chrome; quiet mode stays silent
        # until the reply). (gh #53)
        loading = None if _QUIET else Spinner("Loading agent")
        if loading:
            loading.start()
        # Load WITHOUT the in-memory default so we can see whether the graph brought its
        # OWN checkpointer. A user-supplied checkpointer always wins (same rule as the
        # #38 in-memory default): we attach our durable per-workspace saver only when the
        # graph has none AND persistence is on. Otherwise restore the #38 behavior below.
        graph, final_graph_name = load_graph(
            final_spec, final_graph_name_default, attach_default_checkpointer=False
        )
        had_user_checkpointer = getattr(graph, "checkpointer", None) is not None
        use_durable = (persist or read_only_resume) and not had_user_checkpointer
        if not use_durable:
            _ensure_checkpointer(graph)
        if loading:
            loading.stop()
            print(f"{GREEN}✓{RESET} {DIM}Loaded {final_spec}{RESET}")

        # --verify: preflight the configured agent by running ONE real turn through
        # the shared core primitive (langstage-core >= 1.0.6), then exit on its
        # verdict. A green here means the agent actually completed a turn — not just
        # that it imported — so `langstage-cli --verify -a my_agent.py` is a real CI
        # gate. Delegates to core.verify so "healthy" means the same across surfaces.
        if verify_agent:
            from langstage_core.agui import verify as _core_verify

            result = _core_verify(graph)
            if result.ok:
                print(f"{GREEN}✓{RESET} agent verified: {result.reason}")
                sys.exit(0)
            _status(f"{RED}✗ agent verification failed: {result.reason}{RESET}")
            sys.exit(1)

        # Seed LangGraph RunnableConfig from TOML [configurable] table if present
        config_dict: Dict[str, Any] = {"configurable": {}}
        toml_configurable = config_module.get(toml_config, "configurable")
        if isinstance(toml_configurable, dict):
            config_dict["configurable"].update(toml_configurable)
        if "thread_id" not in config_dict["configurable"]:
            config_dict["configurable"]["thread_id"] = str(uuid.uuid4())

        # Expose TOML sources to slash commands via the config dict
        config_dict["_toml_sources"] = [str(p) for p in toml_sources]
        # The resolved-config diagnostic snapshotted before apply_workspace, so /config
        # reports the true source of workspace_root instead of the self-published env (gh #64).
        config_dict["_resolved_config_report"] = resolved_config_report
        # Also stash the resolved CodeConfig object itself (with its startup-frozen
        # provenance) and the snapshot [configurable] key set, so bare /config can
        # RE-RENDER the same describe() diagnostic from live state — reflecting runtime
        # mutations (/verbose, /config verbose on, /reset) instead of the frozen string,
        # while still never re-resolving (so #64's self-published-env fix holds). (gh #97)
        config_dict["_resolved_config"] = cfg
        config_dict["_snap_configurable"] = (
            _snap_configurable if isinstance(_snap_configurable, dict) else None
        )
        # The session-persistence block interactive /config appends to the diagnostic —
        # computed from the SAME (flag, toml, workspace) as --show-config so the two agree
        # byte-for-byte (gh #108). Uses the raw flag (pre-forcing) to match --show-config.
        config_dict["_persist_diagnostic"] = _persist_diagnostic_block(
            persist_flag, toml_config, workspace
        )

        # ---- Resolve the session thread + wire up persistence (gh #102) ----
        persist_sqlite_path: Optional[Path] = None
        session_thread: Optional[str] = None
        on_turn = None
        if read_only_resume:
            session_thread = _resolve_resume_thread(workspace, continue_session, resume_id)
            persist_sqlite_path = None
            if session_thread is not None:
                config_dict["configurable"]["thread_id"] = session_thread
                if use_durable:
                    persist_sqlite_path = _snapshot_session_store(workspace)
                _status(
                    f"{DIM}⏺ --no-persist: resuming session {session_thread[:8]} read-only — "
                    f"this run will not be saved.{RESET}"
                )
            if persist_sqlite_path is None and use_durable:
                # Nothing to read (no prior session / no store): a plain ephemeral run,
                # exactly like --no-persist without --continue.
                _ensure_checkpointer(graph)
            # No record_session / touch_session / on_turn: the index is never touched.
            config_dict["_session_info"] = {
                "persist": False,
                "read_only": True,
                "durable": persist_sqlite_path is not None,
                "store": str(persist_sqlite_path) if persist_sqlite_path else None,
                "thread": session_thread,
            }
        elif persist:
            session_thread = _resolve_resume_thread(workspace, continue_session, resume_id)
            if session_thread is None:
                # Fresh persisted session: honour a pinned [configurable] thread_id if the
                # user set one (now it actually persists across runs — closing the "I
                # pinned a thread and it still forgot" gap), else the fresh uuid above.
                session_thread = config_dict["configurable"]["thread_id"]
            config_dict["configurable"]["thread_id"] = session_thread
            sessions.record_session(workspace, session_thread, first_message=message)
            if use_durable:
                persist_sqlite_path = sessions.db_path(workspace)

            def on_turn(msg: str) -> None:  # bump index recency + first-message snippet
                sessions.touch_session(workspace, session_thread, first_message=msg)

            config_dict["_session_info"] = {
                "persist": True,
                "durable": use_durable,
                "store": str(persist_sqlite_path) if persist_sqlite_path else None,
                "thread": session_thread,
            }

        # Extract agent name and description from graph object
        agent_name = get_agent_name(graph)
        agent_description = get_agent_description(graph)

        # Run the conversation loop
        # Single-shot mode: exit after processing if message was provided via CLI.
        # In single-shot mode it returns whether the agent turn errored, so a piped /
        # scripted caller can tell a failed run from a success (gh #47).
        turn_had_error = run_conversation_loop(
            graph=graph,
            config=config_dict,
            agent_name=agent_name,
            agent_description=agent_description,
            interactive=interactive,
            verbose=verbose,
            stream_mode=final_stream_mode,
            initial_message=message,
            single_shot=bool(message),
            persist_sqlite_path=persist_sqlite_path,
            on_turn=on_turn,
        )
        if turn_had_error:
            sys.exit(1)

    except BrokenPipeError:
        # An early-closing consumer on the scriptable path (`| head`, `| grep -m1`, …)
        # closes the pipe while we're still writing, raising BrokenPipeError. That is
        # idiomatic, expected usage — not an error — so swallow it and exit quietly
        # instead of surfacing the ⏺-decorated error line the generic handler below
        # would (which also leaks the ⏺ glyph quiet mode otherwise suppresses).
        # Redirect the remaining stdout to devnull so the interpreter's shutdown flush
        # doesn't raise a second BrokenPipeError — the standard Python SIGPIPE recipe.
        # (gh #74)
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, ValueError):  # no real fd (e.g. under test capture) — nothing to redirect
            pass
        sys.exit(0)
    except FileNotFoundError as e:
        _status(f"{RED}⏺ Error: {_fmt_exc(e)}{RESET}")
        sys.exit(1)
    except AttributeError as e:
        _status(f"{RED}⏺ Error: {_fmt_exc(e)}{RESET}")
        sys.exit(1)
    except ModuleNotFoundError as e:
        _status(f"{RED}⏺ Error: {_fmt_exc(e)}{RESET}")
        _status(f"\n{DIM}Make sure your agent's dependencies are installed.{RESET}")
        sys.exit(1)
    except Exception as e:
        # Name the exception class (gh #109): an empty str(e) — a bare `assert`, a
        # `raise NotImplementedError`, `RuntimeError()` — otherwise collapsed this to a
        # blank, typeless `Error:`. Always point at -v for the traceback, like the
        # runtime turn-error path, so a stuck user gets something to act on.
        _status(f"{RED}⏺ Error: {_fmt_exc(e)}{RESET}")
        if verbose:
            import traceback

            _status(traceback.format_exc())
        else:
            _status(f"{DIM}Re-run with -v for the full traceback.{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
