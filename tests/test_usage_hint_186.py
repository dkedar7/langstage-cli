"""gh #186: the "No agent specified" hint shows commands that work (with -a)."""

import pytest
from click.testing import CliRunner

from langstage_cli import cli
from langstage_cli.cli import _looks_like_agent_spec, main


@pytest.fixture
def no_agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for var in ("LANGSTAGE_AGENT_SPEC", "DEEPAGENT_AGENT_SPEC", "DEEPAGENT_SPEC"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cli, "_default_agent_path", lambda: None)


def test_usage_hint_uses_dash_a(no_agent):
    r = CliRunner().invoke(main, ["hi"])
    assert r.exit_code == 1, r.output
    assert "No agent specified" in r.output
    assert "langstage-cli -a path/to/agent.py:graph" in r.output
    assert "langstage-cli -a mypackage.module:agent" in r.output
    assert "[agent] spec" in r.output and "langstage-cli init" in r.output
    # Not a spec-looking message: no "goes after -a" line.
    assert "goes after -a" not in r.output


@pytest.mark.parametrize("spec", ["my_agent.py:graph", "mypackage.module:agent"])
def test_spec_passed_as_message_names_the_working_command(no_agent, spec):
    r = CliRunner().invoke(main, [spec])
    assert r.exit_code == 1, r.output
    assert f"The agent spec goes after -a: langstage-cli -a {spec}" in r.output


@pytest.mark.parametrize(
    "message,expected",
    [
        ("my_agent.py:graph", True),
        ("./agents/a.py:graph", True),
        (r"C:\work\agent.py:graph", True),
        ("pkg.module:agent", True),
        ("hello", False),
        ("what is 2:3", False),
        ("ratio 3:1 please", False),
        ("http://example.org:80", False),
        ("", False),
        (None, False),
    ],
)
def test_looks_like_agent_spec(message, expected):
    assert _looks_like_agent_spec(message) is expected
