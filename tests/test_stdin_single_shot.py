"""Single-shot input: an explicit empty MESSAGE (gh #123) and piped stdin (gh #127).

CliRunner's stdin is a buffer, never a terminal, so ``input=`` is exactly a pipe or a
``</dev/null`` redirect.
"""

from click.testing import CliRunner

from langstage_cli import sessions
from langstage_cli.cli import main


# --- gh #127: piped stdin is ONE message ---


def test_multiline_piped_prompt_is_one_turn(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prompt = "Summarize:\nParis is the capital of France.\nIt has a population of 2M.\n"
    r = CliRunner().invoke(main, ["--demo"], input=prompt)
    assert r.exit_code == 0, r.output
    assert r.stdout == f"(demo agent) You said: {prompt.strip()}\n", repr(r.stdout)
    assert r.stdout.count("You said:") == 1


def test_piped_slash_lines_are_sent_not_executed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["--demo"], input="/version\n")
    assert r.exit_code == 0, r.output
    assert r.stdout == "(demo agent) You said: /version\n", repr(r.stdout)

    # A /quit line mid-prompt no longer truncates the rest of it.
    r = CliRunner().invoke(main, ["--demo"], input="analyze this\n/quit\nand then report\n")
    assert r.exit_code == 0, r.output
    assert "and then report" in r.stdout, repr(r.stdout)
    assert r.stdout.count("You said:") == 1


def test_piped_stdin_matches_the_file_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prompt = "line one\nline two\n"
    (tmp_path / "p.txt").write_text(prompt, encoding="utf-8")
    via_file = CliRunner().invoke(main, ["--demo", "-f", "p.txt"])
    via_pipe = CliRunner().invoke(main, ["--demo"], input=prompt)
    assert via_pipe.exit_code == 0 and via_file.exit_code == 0
    assert via_pipe.stdout == via_file.stdout, (via_file.stdout, via_pipe.stdout)


def test_empty_piped_stdin_is_a_clean_error_and_records_no_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["--demo"], input="")
    assert r.exit_code == 1, r.output
    assert "no message on stdin" in r.stderr, r.stderr
    assert r.stdout == ""
    assert sessions.list_sessions(tmp_path.resolve()) == []


def test_message_arg_wins_and_stdin_is_not_read(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["--demo", "from the arg"], input="from stdin\n")
    assert r.exit_code == 0, r.output
    assert r.stdout == "(demo agent) You said: from the arg\n", repr(r.stdout)


def test_list_sessions_does_not_need_stdin(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["--list-sessions"], input="")
    assert r.exit_code == 0, r.output
    assert "no message on stdin" not in r.output


# --- gh #123: an explicit empty MESSAGE is not "no message" ---


def test_empty_message_arg_errors_instead_of_entering_the_repl(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Before: "" was taken as no message, so the REPL read "hi" off stdin and ran it
    # (or hung on an open pipe), despite --no-interactive.
    r = CliRunner().invoke(main, ["--demo", "", "--no-interactive"], input="hi\n")
    assert r.exit_code == 1, r.output
    assert "MESSAGE is empty" in r.stderr, r.stderr
    assert "You said" not in r.output


def test_whitespace_message_arg_is_empty_too(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["--demo", "   ", "--no-interactive"], input="")
    assert r.exit_code == 1, r.output
    assert "MESSAGE is empty" in r.stderr, r.stderr
