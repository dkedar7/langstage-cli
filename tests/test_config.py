"""Tests for langstage_cli.config."""

from __future__ import annotations

from pathlib import Path

import pytest

from langstage_cli import config


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_returns_empty_when_no_files(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPAGENTS_CONFIG_HOME", str(tmp_path / "empty"))
    monkeypatch.chdir(tmp_path)
    cfg, sources = config.load_config()
    assert cfg == {}
    assert sources == []


def test_loads_global_only(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write(home / "config.toml", "[ui]\nverbose = true\n")
    monkeypatch.setenv("DEEPAGENTS_CONFIG_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    cfg, sources = config.load_config()
    assert cfg == {"ui": {"verbose": True}}
    assert sources == [home / "config.toml"]


def test_loads_project_only(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPAGENTS_CONFIG_HOME", str(tmp_path / "empty"))
    project = tmp_path / "proj"
    _write(project / "deepagents.toml", '[agent]\nspec = "foo.py:agent"\n')
    monkeypatch.chdir(project)
    cfg, sources = config.load_config()
    assert cfg == {"agent": {"spec": "foo.py:agent"}}
    assert sources == [project / "deepagents.toml"]


def test_project_overrides_global(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write(home / "config.toml", '[ui]\nverbose = false\nstream_mode = "values"\n')
    monkeypatch.setenv("DEEPAGENTS_CONFIG_HOME", str(home))

    project = tmp_path / "proj"
    _write(project / "deepagents.toml", "[ui]\nverbose = true\n")
    monkeypatch.chdir(project)

    cfg, sources = config.load_config()
    # project wins on overlapping keys, global keys still present
    assert cfg == {"ui": {"verbose": True, "stream_mode": "values"}}
    assert sources == [home / "config.toml", project / "deepagents.toml"]


def test_walks_up_for_project_config(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPAGENTS_CONFIG_HOME", str(tmp_path / "empty"))
    project = tmp_path / "proj"
    nested = project / "a" / "b" / "c"
    nested.mkdir(parents=True)
    _write(project / "deepagents.toml", "[ui]\nverbose = true\n")
    monkeypatch.chdir(nested)

    cfg, sources = config.load_config()
    assert cfg == {"ui": {"verbose": True}}
    assert sources == [project / "deepagents.toml"]


def test_invalid_toml_raises(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write(home / "config.toml", "this is = not = valid toml\n")
    monkeypatch.setenv("DEEPAGENTS_CONFIG_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(config.ConfigError):
        config.load_config()


def test_get_dotted_path():
    data = {"ui": {"verbose": True}, "agent": {"spec": "x"}}
    assert config.get(data, "ui.verbose") is True
    assert config.get(data, "agent.spec") == "x"
    assert config.get(data, "missing") is None
    assert config.get(data, "ui.missing", default="d") == "d"
    assert config.get(data, "ui.verbose.nested", default="d") == "d"


def test_resolve_precedence(monkeypatch):
    cfg = {"ui": {"verbose": True}}
    monkeypatch.delenv("UI_VERBOSE", raising=False)

    # default only
    assert config.resolve({}, "ui.verbose", default=False) is False
    # toml beats default
    assert config.resolve(cfg, "ui.verbose", default=False) is True
    # env beats toml
    monkeypatch.setenv("UI_VERBOSE", "false")
    assert config.resolve(cfg, "ui.verbose", env_var="UI_VERBOSE", cast=bool) is False
    # cli beats env
    assert (
        config.resolve(cfg, "ui.verbose", cli_value=True, env_var="UI_VERBOSE", cast=bool) is True
    )


def test_resolve_bool_cast(monkeypatch):
    for truthy in ("1", "true", "yes", "on", "TRUE"):
        monkeypatch.setenv("X", truthy)
        assert config.resolve({}, "missing", env_var="X", cast=bool) is True
    for falsy in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("X", falsy)
        assert config.resolve({}, "missing", env_var="X", cast=bool) is False
