"""Tests for CLI functions."""

import pytest
from click.testing import CliRunner

from deepagent_code.cli import main, parse_agent_spec


class TestCliFlags:
    """Tests for --show-config and --demo."""

    def test_show_config_prints_resolved_config(self):
        result = CliRunner().invoke(main, ["--show-config"])
        assert result.exit_code == 0, result.output
        assert "DEEPAGENT_AGENT_SPEC" in result.output

    def test_demo_and_agent_are_mutually_exclusive(self):
        result = CliRunner().invoke(main, ["--demo", "-a", "x.py:g", "hi"])
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output

    def test_demo_replies_without_any_key(self, monkeypatch):
        # A full one-shot turn through the real stub agent — proves --demo
        # works on a machine with no API keys configured.
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = CliRunner().invoke(main, ["--demo", "--no-interactive", "hello demo"])
        assert result.exit_code == 0, result.output
        assert "hello demo" in result.output


class TestParseAgentSpec:
    """Tests for parse_agent_spec function."""

    def test_valid_spec(self):
        """Test parsing valid agent spec."""
        file_path, var_name = parse_agent_spec("my_agent.py:graph")

        assert file_path == "my_agent.py"
        assert var_name == "graph"

    def test_valid_spec_with_path(self):
        """Test parsing agent spec with directory path."""
        file_path, var_name = parse_agent_spec("path/to/my_agent.py:custom_graph")

        assert file_path == "path/to/my_agent.py"
        assert var_name == "custom_graph"

    def test_valid_spec_with_colon_in_path(self):
        """Test parsing agent spec with colon in variable name."""
        # The function uses rsplit with maxsplit=1, so only the last colon matters
        file_path, var_name = parse_agent_spec("some/path/agent.py:my_graph")

        assert file_path == "some/path/agent.py"
        assert var_name == "my_graph"

    def test_missing_colon(self):
        """Test error when colon is missing."""
        with pytest.raises(ValueError, match="Invalid agent spec format"):
            parse_agent_spec("my_agent.py")

    def test_non_python_file(self):
        """Test error when file is not a .py file."""
        with pytest.raises(ValueError, match="Agent spec file must be a .py file"):
            parse_agent_spec("my_agent.txt:graph")

    def test_absolute_path(self):
        """Test parsing absolute path."""
        file_path, var_name = parse_agent_spec("/abs/path/to/agent.py:graph")

        assert file_path == "/abs/path/to/agent.py"
        assert var_name == "graph"

    def test_windows_path(self):
        """Test parsing Windows-style path."""
        file_path, var_name = parse_agent_spec("C:\\path\\to\\agent.py:graph")

        assert file_path == "C:\\path\\to\\agent.py"
        assert var_name == "graph"

    def test_empty_variable_name(self):
        """Test with empty variable name after colon."""
        file_path, var_name = parse_agent_spec("agent.py:")

        assert file_path == "agent.py"
        assert var_name == ""  # Empty but valid

    def test_complex_variable_name(self):
        """Test with complex variable names."""
        file_path, var_name = parse_agent_spec("agent.py:my_custom_graph_v2")

        assert file_path == "agent.py"
        assert var_name == "my_custom_graph_v2"
