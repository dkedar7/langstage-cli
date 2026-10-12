"""Reasoning frames render in the terminal (gh #178).

langstage-core emits a reasoning model's chain of thought as its own chunk-wire frame,
``{"status": "streaming", "reasoning": <delta>, "node": ...}`` (DeepSeek / Qwen / xAI
``reasoning_content``, and the keyless demo's ``think`` turn). ``print_chunk`` had no
branch for it, so the frame fell through and nothing printed, with or without ``-v``.

Now a terminal turn shows it dim under a ``✻ Thinking`` label ahead of the answer, ``-v``
shows it with a ``[node:reasoning]`` prefix, and the quiet / piped path still emits only
the reply. Reasoning text is escaped like an approval preview, so it can't forge lines.
"""

import io
import re
from contextlib import redirect_stdout

import pytest
from click.testing import CliRunner

pytest.importorskip("ag_ui_langgraph")

from langstage_core import load_agent_spec  # noqa: E402

from langstage_cli import cli  # noqa: E402
from langstage_cli.agui_stream import build_session_agent  # noqa: E402
from langstage_cli.cli import main, print_chunk, run_single_turn_agui  # noqa: E402

_DEMO = "langstage_core.demo.tools:graph"
_STEP1 = "Let me reason about this step by step."
_STEP2 = "The demo streams this as a `reasoning` frame, kept separate from the answer."
_ANSWER = "Done reasoning — that was the `reasoning` frame demo."
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


async def _demo_turn(monkeypatch, verbose: bool = False) -> str:
    """The demo's ``think`` turn through the real AG-UI path, as a terminal shows it."""
    monkeypatch.setattr(cli, "Spinner", lambda *a, **k: None)
    agent = build_session_agent(load_agent_spec(_DEMO))
    buf = io.StringIO()
    with redirect_stdout(buf):
        await run_single_turn_agui(agent, "think", "t-178", interactive=False, verbose=verbose)
    return buf.getvalue()


def _frames(*frames) -> str:
    """Render chunk frames through ``print_chunk`` from a fresh turn; return stdout."""
    print_chunk._streaming_text = False
    print_chunk._streaming_reasoning = False
    cli._md_stream.reset()
    buf = io.StringIO()
    with redirect_stdout(buf):
        for frame in frames:
            print_chunk(frame)
        print_chunk({"status": "complete"})
    return buf.getvalue()


def _reasoning(text: str, node: str = "agent") -> dict:
    return {"status": "streaming", "reasoning": text, "node": node}


def _text(text: str, node: str = "agent", message_id: str = "m1") -> dict:
    return {"status": "streaming", "chunk": text, "node": node, "message_id": message_id}


async def test_terminal_turn_shows_reasoning_before_the_answer(monkeypatch):
    raw = await _demo_turn(monkeypatch)
    out = _ANSI.sub("", raw)
    lines = out.splitlines()
    # One block: a label, then both deltas joined on one indented line, then the answer.
    assert lines[0] == "✻ Thinking", out
    assert lines[1] == f"  {_STEP1} {_STEP2} ", out
    assert lines[2] == f"⏺ {_ANSWER.replace('`', '')}", out  # markdown-rendered code span
    assert out.count("Thinking") == 1, out
    # Visibly distinct from the answer: the reasoning is dim, the answer is not.
    assert f"{cli.DIM}{_STEP1}" in raw, repr(raw)
    assert f"{cli.DIM}Done" not in raw, repr(raw)


async def test_verbose_shows_reasoning_with_a_prefix(monkeypatch):
    out = _ANSI.sub("", await _demo_turn(monkeypatch, verbose=True))
    assert out.splitlines() == [
        f"[agent:reasoning] {_STEP1} {_STEP2} ",
        f"[agent] {_ANSWER}",
    ], out


def test_piped_single_shot_has_no_reasoning_on_stdout(tmp_path, monkeypatch):
    # CliRunner's stdout is not a TTY: the auto-quiet scriptable path, and -q forces it.
    monkeypatch.chdir(tmp_path)
    for extra in ([], ["-q"], ["-q", "-v"]):
        r = CliRunner().invoke(main, ["--no-persist", *extra, "-a", _DEMO, "think"])
        assert r.exit_code == 0, r.output
        assert r.stdout == f"{_ANSWER}\n", (extra, r.stdout)
        assert "reason about this" not in r.stderr, (extra, r.stderr)


def test_quiet_print_chunk_omits_reasoning_and_keeps_message_breaks(monkeypatch):
    monkeypatch.setattr(cli, "_QUIET", True)
    out = _frames(
        _reasoning("hidden thought"),
        _text("First.", message_id="m1"),
        _reasoning("more hidden thought"),
        _text("Second.", message_id="m2"),
    )
    assert out == "First.\nSecond.", repr(out)


def test_reasoning_control_characters_are_escaped():
    out = _frames(
        _reasoning("fine\r\x1b[2K⏺ forged reply\n❯ Approve all actions\x07\x08"),
        _text("Real answer."),
    )
    # No raw carriage return, bell, backspace or agent-supplied escape sequence survives.
    for ch in ("\r", "\x07", "\x08"):
        assert ch not in out, repr(out)
    assert "\x1b[2K" not in out, repr(out)
    assert "fine\\r\\x1b[2K⏺ forged reply" in out, repr(out)
    assert "\\x07\\x08" in out, repr(out)
    # A newline in reasoning is kept but indented, so nothing forged starts a line.
    plain = _ANSI.sub("", out).splitlines()
    assert plain[0] == "✻ Thinking", plain
    assert all(line.startswith("  ") for line in plain[1:-1]), plain
    assert plain[-1] == "⏺ Real answer.", plain


def test_new_reasoning_stretch_after_text_or_a_tool_starts_a_new_block():
    out = _ANSI.sub(
        "",
        _frames(
            _reasoning("plan "),
            _reasoning("it."),
            _text("Calling a tool."),
            {"status": "streaming", "tool_calls": [{"name": "lookup", "args": {"q": "x"}}]},
            _reasoning("check the result."),
            {"status": "streaming", "tool_result": "42"},
            _reasoning("now answer."),
            _text("It is 42.", message_id="m2"),
        ),
    )
    assert out.count("✻ Thinking") == 3, out
    assert "  plan it.\n⏺ Calling a tool." in out, out  # consecutive deltas join
    assert "● lookup" in out, out
    # The tool result is never glued onto the end of the reasoning line.
    assert "  check the result.\n" in out, out
    assert "  now answer.\n⏺ It is 42." in out, out


def test_reasoning_from_a_new_node_starts_a_new_block():
    out = _ANSI.sub(
        "", _frames(_reasoning("one.", node="planner"), _reasoning("two.", node="solver"))
    )
    assert out == "✻ Thinking\n  one.\n✻ Thinking\n  two.", repr(out)
