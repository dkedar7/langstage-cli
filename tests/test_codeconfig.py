"""Tests for CodeConfig — langstage-cli's HostConfig subclass."""

from pathlib import Path

import pytest

from langstage_cli.config import CodeConfig


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("DEEPAGENTS_CONFIG_HOME", str(empty))
    return tmp_path


def _toml(d: Path, body: str) -> None:
    (d / "deepagents.toml").write_text(body)


def test_defaults(isolated, tmp_path):
    cfg = CodeConfig.resolve(env={}, toml_start=tmp_path)
    assert cfg.stream_mode == "auto"
    assert cfg.graph_name == "graph"
    assert cfg.verbose is False
    assert cfg.async_mode is False
    assert cfg.agent_spec is None


def test_env_stream_mode(isolated, tmp_path):
    cfg = CodeConfig.resolve(env={"DEEPAGENT_STREAM_MODE": "values"}, toml_start=tmp_path)
    assert cfg.stream_mode == "values"
    assert cfg.sources["stream_mode"] == "env:DEEPAGENT_STREAM_MODE"


def test_toml_keys(isolated, tmp_path):
    _toml(
        tmp_path,
        '[agent]\nspec = "a.py:g"\ngraph_name = "myg"\n'
        '[ui]\nverbose = true\nasync_mode = true\nstream_mode = "values"\n',
    )
    cfg = CodeConfig.resolve(env={}, toml_start=tmp_path)
    assert cfg.agent_spec == "a.py:g"
    assert cfg.graph_name == "myg"
    assert cfg.verbose is True
    assert cfg.async_mode is True
    assert cfg.stream_mode == "values"


def test_deepagent_spec_alias_is_deprecated(isolated, tmp_path):
    with pytest.warns(DeprecationWarning):
        cfg = CodeConfig.resolve(env={"DEEPAGENT_SPEC": "legacy.py:g"}, toml_start=tmp_path)
    assert cfg.agent_spec == "legacy.py:g"


def test_canonical_var_beats_alias(isolated, tmp_path):
    cfg = CodeConfig.resolve(
        env={"DEEPAGENT_AGENT_SPEC": "new.py:g", "DEEPAGENT_SPEC": "old.py:g"},
        toml_start=tmp_path,
    )
    assert cfg.agent_spec == "new.py:g"


def test_overrides_win(isolated, tmp_path):
    cfg = CodeConfig.resolve(
        env={"DEEPAGENT_STREAM_MODE": "values"},
        overrides={"stream_mode": "updates"},
        toml_start=tmp_path,
    )
    assert cfg.stream_mode == "updates"
    assert cfg.sources["stream_mode"] == "override"


def test_describe_lists_var_names(isolated, tmp_path):
    text = CodeConfig.resolve(env={}, toml_start=tmp_path).describe()
    assert "DEEPAGENT_AGENT_SPEC" in text
    assert "DEEPAGENT_STREAM_MODE" in text
    assert "stream_mode" in text
