"""Scriptable single-shot output (gh #53).

A single-shot run that is piped (stdout is not a TTY) auto-enables quiet output,
and ``--quiet`` forces it in a terminal: the reply is emitted with no header box,
welcome text, "Loaded" line, spinner, tool chatter, timing, or color — just the
agent's text on stdout, with diagnostics and errors routed to stderr so a pipe
never sees them.

CliRunner's stdout is not a TTY, so ``invoke(main, [...])`` exercises exactly the
piped path these tests care about.
"""

from click.testing import CliRunner

from langstage_cli import cli as c
from langstage_cli.cli import main, print_chunk


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
