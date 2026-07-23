"""Scriptable single-shot output (gh #53).

A single-shot run that is piped (stdout is not a TTY) auto-enables quiet output,
and ``--quiet`` forces it in a terminal: the reply is emitted with no header box,
welcome text, "Loaded" line, spinner, tool chatter, timing, or color — just the
agent's text on stdout, with diagnostics and errors routed to stderr so a pipe
never sees them.

CliRunner's stdout is not a TTY, so ``invoke(main, [...])`` exercises exactly the
piped path these tests care about.
"""

import re

from click.testing import CliRunner

from langstage_cli import cli as c
from langstage_cli.cli import main, print_chunk, _status


def test_piped_single_shot_is_only_the_reply(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["--demo", "hello world"])
    assert r.exit_code == 0, r.output
    assert "hello world" in r.output  # the echo stub replies with the message
    # None of the interactive chrome or ANSI leaks into the pipe.
    assert "\x1b[" not in r.output, r.output  # color stripped
    assert "⏺" not in r.output, r.output  # no streaming marker
    assert "Loaded" not in r.output, r.output  # no load line
    assert "Thinking" not in r.output, r.output  # no spinner


def test_quiet_flag_forces_clean_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["-q", "--demo", "ping"])
    assert r.exit_code == 0, r.output
    assert "ping" in r.output
    assert "\x1b[" not in r.output and "⏺" not in r.output, r.output


def test_piped_stdin_quiet_output_matches_the_message_arg_path(tmp_path, monkeypatch):
    # gh #93: a message on piped stdin (`echo "hi" | langstage-cli --demo -q`) used to
    # be routed through the interactive REPL, which honored neither -q nor the non-TTY
    # auto-quiet — so it leaked the `····` separator rules, the `❯` prompt row (with
    # `\x01\x02` bracketed-paste bytes), the `Nms` timing line, and a trailing `Goodbye!`
    # around the reply (~380 bytes of chrome). It must now emit exactly what the
    # MESSAGE-arg quiet path emits: only the agent's reply. CliRunner's stdin and stdout
    # are both non-TTYs, so `input="hi\n"` with no MESSAGE arg reproduces the piped pipe.
    monkeypatch.chdir(tmp_path)
    arg = CliRunner().invoke(main, ["--demo", "-q", "hi"])
    stdin = CliRunner().invoke(main, ["--demo", "-q"], input="hi\n")
    assert arg.exit_code == 0 and stdin.exit_code == 0, (arg.output, stdin.output)
    # Byte-for-byte identical reply streams — the stdin path carries no extra chrome.
    assert stdin.stdout == arg.stdout, repr((arg.stdout, stdin.stdout))
    assert stdin.stdout == "(demo agent) You said: hi\n", repr(stdin.stdout)
    # And none of the specific chrome the issue grepped for reaches the reply stream.
    assert re.search(r"\d+ms", stdin.stdout) is None, repr(stdin.stdout)  # no timing line
    assert "·" not in stdin.stdout, repr(stdin.stdout)  # no `····` separator rule
    assert "❯" not in stdin.stdout, repr(stdin.stdout)  # no prompt row
    assert "\x01" not in stdin.stdout and "\x02" not in stdin.stdout  # no bracketed-paste bytes
    assert "Goodbye" not in stdin.stdout, repr(stdin.stdout)  # no trailing farewell


def test_piped_stdin_auto_quiets_without_the_quiet_flag(tmp_path, monkeypatch):
    # gh #93 / #53: the non-TTY auto-quiet must ALSO fire when the message arrives on
    # stdin, with no -q — `echo "hi" | langstage-cli --demo | tool` is clean by default,
    # exactly like the MESSAGE-arg auto-quiet. Piped stdin is non-interactive input, so
    # the run is scriptable even without the explicit flag.
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["--demo"], input="hi\n")
    assert r.exit_code == 0, r.output
    assert r.stdout == "(demo agent) You said: hi\n", repr(r.stdout)
    assert re.search(r"\d+ms", r.stdout) is None, repr(r.stdout)
    assert "·" not in r.stdout and "❯" not in r.stdout and "Goodbye" not in r.stdout


def test_make_prompt_uses_disabled_color_globals_after_ansi_is_disabled():
    c._disable_ansi()

    prompt = c.make_prompt()

    assert "\x1b[" not in prompt, repr(prompt)


def test_errors_go_to_stderr_not_stdout(tmp_path, monkeypatch):
    # A failed single-shot run must keep stdout clean so a scripted caller can
    # consume the (empty) reply and read the diagnostic off stderr / the exit code.
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["-a", "nope_missing.module:graph", "hi"])
    assert r.exit_code != 0
    assert r.stdout.strip() == "", r.stdout  # nothing on the reply stream
    assert "nope_missing" in r.stderr or "Error" in r.stderr, r.stderr


def test_print_chunk_quiet_emits_raw_text_only(capsys):
    c._QUIET = True
    print_chunk({"status": "streaming", "chunk": "**bold** and `code`", "node": "n"})
    out = capsys.readouterr().out
    # Verbatim: no cyan bullet, no [node] label, and no markdown rewrite — a script
    # gets exactly what the model produced.
    assert out == "**bold** and `code`", repr(out)


def test_print_chunk_quiet_suppresses_tool_chatter(capsys):
    c._QUIET = True
    print_chunk({"status": "streaming", "tool_calls": [{"name": "t", "args": {"x": 1}}]})
    print_chunk({"status": "streaming", "tool_result": "big result"})
    assert capsys.readouterr().out == ""  # tool decoration omitted in quiet mode


def test_print_chunk_quiet_routes_error_to_stderr(capsys):
    c._QUIET = True
    print_chunk({"status": "error", "error": "boom"})
    captured = capsys.readouterr()
    assert captured.out == ""  # the reply stream stays clean
    assert "boom" in captured.err


def test_interactive_default_keeps_decoration(capsys):
    # The default (TTY / not quiet) path is unchanged: the marker still renders.
    c._QUIET = False
    print_chunk._streaming_text = False
    print_chunk._streaming_node = None
    print_chunk({"status": "streaming", "chunk": "hi", "node": "n"})
    assert "⏺" in capsys.readouterr().out


def test_print_chunk_quiet_breaks_between_nodes(capsys):
    # gh #74: two AIMessages produced by different nodes in one turn (a ReAct /
    # plan→execute shape) must not run together on the scriptable path. The gh #43
    # node-change break covered the verbose/non-verbose branches but not _QUIET, so
    # the two messages were emitted back-to-back (`…up.The capital…`) with no
    # separator — on the one output mode meant to be machine-parseable. A bare
    # newline (no marker, no color) now separates them.
    c._QUIET = True
    print_chunk._streaming_text = False
    print_chunk._streaming_node = None
    print_chunk({"status": "streaming", "chunk": "Let me look that up.", "node": "think"})
    print_chunk(
        {"status": "streaming", "chunk": "The capital of France is Paris.", "node": "answer"}
    )
    out = capsys.readouterr().out
    assert out == "Let me look that up.\nThe capital of France is Paris.", repr(out)
    # No decoration leaks onto the scriptable stream — only the boundary newline.
    assert "⏺" not in out and "\x1b[" not in out, repr(out)


def test_print_chunk_quiet_single_node_tokens_join_unbroken(capsys):
    # Contrast (gh #74): tokens of ONE message share a node, so they must still join
    # with no separator — that's correct token streaming; only the cross-node
    # boundary is a break.
    c._QUIET = True
    print_chunk._streaming_text = False
    print_chunk._streaming_node = None
    for tok in ["Hel", "lo ", "world"]:
        print_chunk({"status": "streaming", "chunk": tok, "node": "answer"})
    out = capsys.readouterr().out
    assert out == "Hello world", repr(out)


_HITL_AGENT = """
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import MessagesState
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt
from langchain_core.messages import AIMessage

def ask(state):
    decision = interrupt({"action": "delete_file", "path": "/etc/passwd"})
    return {"messages": [AIMessage(content=f"Decision was: {decision}")]}

g = StateGraph(MessagesState)
g.add_node("ask", ask)
g.add_edge(START, "ask")
g.add_edge("ask", END)
graph = g.compile(checkpointer=MemorySaver())
"""


def test_print_chunk_quiet_suppresses_hitl_banner(capsys):
    # gh #77: the `⚠ Action Required` HITL banner is human decoration. On the
    # scriptable path it must not reach stdout — it would corrupt the machine-readable
    # reply (a leading blank line, the literal `⚠` glyph, and the action list, ahead
    # of the real text). Like tool chatter, the interrupt branch is fully omitted in
    # quiet mode; nothing leaks to stderr either (the banner is suppressed, not rerouted).
    c._QUIET = True
    print_chunk._streaming_text = False
    print_chunk._streaming_node = None
    print_chunk(
        {
            "status": "interrupt",
            "interrupt": {
                "action_requests": [{"action": "delete_file", "args": {"path": "/etc/passwd"}}]
            },
        }
    )
    captured = capsys.readouterr()
    assert captured.out == "", repr(captured.out)  # reply stream stays clean
    assert "⚠" not in captured.out and "Action Required" not in captured.out
    assert captured.err == "", repr(captured.err)


def test_print_chunk_interactive_keeps_hitl_banner(capsys):
    # Contrast: the default (non-quiet) path still renders the banner and the pending
    # action so an operator can see what they are approving (the gh #69 tool name).
    c._QUIET = False
    print_chunk._streaming_text = False
    print_chunk(
        {
            "status": "interrupt",
            "interrupt": {"action_requests": [{"action": "delete_file", "args": {}}]},
        }
    )
    out = capsys.readouterr().out
    assert "Action Required" in out, repr(out)
    assert "delete_file" in out, repr(out)


def test_hitl_renders_structured_action_with_args_unchanged(capsys):
    # gh #82 regression guard: the deepagents ActionRequest path (#69) must render
    # exactly as before — tool name from the `action` key plus a first-arg preview.
    c._QUIET = False
    print_chunk._streaming_text = False
    print_chunk(
        {
            "status": "interrupt",
            "interrupt": {
                "action_requests": [{"action": "delete_file", "args": {"path": "/etc/passwd"}}]
            },
        }
    )
    out = capsys.readouterr().out
    assert "1. delete_file" in out, repr(out)
    assert "/etc/passwd" in out, repr(out)  # args preview intact
    assert "unknown" not in out, repr(out)


def test_hitl_renders_bare_string_interrupt(capsys):
    # gh #82: a generic `interrupt("Approve ...?")` reaching the renderer as a bare
    # string action_request used to raise `'str' object has no attribute 'get'`; the
    # user must instead SEE the string they are approving.
    c._QUIET = False
    print_chunk._streaming_text = False
    print_chunk(
        {
            "status": "interrupt",
            "interrupt": {"action_requests": ["Approve deleting production database? (yes/no)"]},
        }
    )
    out = capsys.readouterr().out
    assert "Action Required" in out, repr(out)
    assert "Approve deleting production database? (yes/no)" in out, repr(out)
    assert "unknown" not in out, repr(out)


def test_hitl_renders_plain_dict_interrupt_content(capsys):
    # gh #82: `interrupt({"question": "..."})` used to render `1. unknown`, silently
    # dropping the question. The human-readable field must be surfaced instead.
    c._QUIET = False
    print_chunk._streaming_text = False
    print_chunk(
        {
            "status": "interrupt",
            "interrupt": {"action_requests": [{"question": "Approve deleting file X?"}]},
        }
    )
    out = capsys.readouterr().out
    assert "Approve deleting file X?" in out, repr(out)
    assert "unknown" not in out, repr(out)


def test_hitl_renders_unrecognized_dict_as_compact_repr(capsys):
    # gh #82: a dict with no known tool/human-readable key must still show its content
    # (a compact repr), never the content-dropping `unknown`.
    c._QUIET = False
    print_chunk._streaming_text = False
    print_chunk(
        {
            "status": "interrupt",
            "interrupt": {"action_requests": [{"foo": "bar", "count": 3}]},
        }
    )
    out = capsys.readouterr().out
    assert "foo" in out and "bar" in out, repr(out)
    assert "unknown" not in out, repr(out)


def test_hitl_empty_action_requests_notes_missing_detail(capsys):
    # gh #82: when the payload is dropped upstream (action_requests == []), the banner
    # must not be silently contentless — it says no detail was provided so the operator
    # knows they'd be approving blind.
    c._QUIET = False
    print_chunk._streaming_text = False
    print_chunk({"status": "interrupt", "interrupt": {"action_requests": []}})
    out = capsys.readouterr().out
    assert "Action Required" in out, repr(out)
    assert "no action details" in out, repr(out)


def test_hitl_empty_action_requests_surfaces_raw_value(capsys):
    # gh #82: if a raw interrupt value survives on the frame even when action_requests
    # is empty, render it rather than the "no detail" note.
    c._QUIET = False
    print_chunk._streaming_text = False
    print_chunk(
        {
            "status": "interrupt",
            "interrupt": {
                "action_requests": [],
                "value": "Approve deleting production database?",
            },
        }
    )
    out = capsys.readouterr().out
    assert "Approve deleting production database?" in out, repr(out)


def test_hitl_quiet_stdout_is_only_the_reply(tmp_path, monkeypatch):
    # gh #77 end-to-end: a HITL agent run with --no-interactive on the scriptable path
    # (single-shot + piped => quiet). stdout must be ONLY the agent's reply — no banner,
    # no `⚠`, no leading blank line — while the auto-approve diagnostic stays on stderr.
    (tmp_path / "hitl_agent.py").write_text(_HITL_AGENT)
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["-a", "hitl_agent.py:graph", "go", "--no-interactive"])
    assert r.exit_code == 0, r.output
    assert r.stdout == "Decision was: {'decisions': [{'type': 'approve'}]}\n", repr(r.stdout)
    assert "⚠" not in r.stdout and "Action Required" not in r.stdout, repr(r.stdout)
    # The auto-approve diagnostic is off the reply stream, on stderr.
    assert "Auto-approving" in r.stderr, repr(r.stderr)


def test_status_strips_error_glyph_in_quiet(capsys):
    # gh #76: the `⏺` in `_status(f"{RED}⏺ Error: …{RESET}")` is a literal glyph, not
    # ANSI, so _disable_ansi() (which blanks RED/RESET) leaves it. Quiet mode routes
    # the line to stderr AND must drop the glyph, matching print_chunk's bare
    # `Error: …` quiet contract. Emulate a real quiet run: ansi disabled + _QUIET set.
    c._disable_ansi()
    c._QUIET = True
    _status(f"{c.RED}⏺ Error: Agent file not found: /no/such/agent.py{c.RESET}")
    captured = capsys.readouterr()
    assert captured.out == "", repr(captured.out)  # reply stream stays clean
    assert captured.err == "Error: Agent file not found: /no/such/agent.py\n", repr(captured.err)
    assert "⏺" not in captured.err, repr(captured.err)


def test_status_keeps_glyph_and_uses_stdout_when_not_quiet(capsys):
    # The interactive (non-quiet) path is unchanged: the glyph stays and the line
    # goes to stdout, so the terminal keeps its familiar decoration.
    c._QUIET = False
    _status("⏺ Error: boom")
    captured = capsys.readouterr()
    assert captured.out == "⏺ Error: boom\n", repr(captured.out)
    assert captured.err == "", repr(captured.err)


def test_error_stderr_has_no_glyph_in_quiet(tmp_path, monkeypatch):
    # gh #76 end-to-end: a load error on the scriptable path routes to stderr (stdout
    # stays clean) but must not carry the `⏺` glyph — the #74 suppression only covered
    # BrokenPipeError, so every other error path still leaked it via _status().
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["-a", "no_such_module_xyz:graph", "hi"])
    assert r.exit_code == 1
    assert r.stdout.strip() == "", r.stdout  # nothing on the reply stream
    assert "Error" in r.stderr, r.stderr
    assert "⏺" not in r.stderr, repr(r.stderr)


def test_broken_pipe_is_swallowed_not_surfaced(tmp_path, monkeypatch):
    # gh #74 (secondary): an early-closing consumer (`| head`, `| grep -m1`) closes
    # the pipe mid-write, raising BrokenPipeError. That is idiomatic scriptable usage,
    # not a failure — it must be swallowed (Python's usual SIGPIPE handling) and exit
    # cleanly, never surfaced as a ⏺-decorated error line (the glyph quiet mode
    # otherwise suppresses).
    monkeypatch.chdir(tmp_path)

    def _raise_broken_pipe(*args, **kwargs):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(c, "run_conversation_loop", _raise_broken_pipe)
    r = CliRunner().invoke(main, ["--demo", "hi"])
    assert r.exit_code == 0, r.output
    assert "Broken pipe" not in r.output, r.output
    assert "⏺" not in r.output, r.output
    assert "Error" not in r.output, r.output
