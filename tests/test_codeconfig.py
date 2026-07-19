"""Tests for CodeConfig — langstage-cli's HostConfig subclass."""

from pathlib import Path

import pytest

from langstage_core.host import config as core_config
from langstage_cli.config import CodeConfig


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("DEEPAGENTS_CONFIG_HOME", str(empty))
    # The shared resolver dedupes each legacy-env notice once per process via a
    # module-level set; clear the spec-alias entries so warning-asserting tests are
    # order-independent (a prior resolve() in the same session would otherwise have
    # already "warned" and silenced the DeprecationWarning these tests check).
    for var in ("DEEPAGENT_SPEC", "DEEPAGENT_AGENT_SPEC"):
        core_config._warned_legacy_env.discard(var)
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


def test_stream_mode_env_and_toml_are_deprecated_and_ignored(isolated, tmp_path):
    # gh #62: stream_mode has no effect since the AG-UI migration, so it is no longer
    # resolved from LANGSTAGE_STREAM_MODE / [ui] stream_mode — both are now ignored and
    # the field stays at its default.
    _toml(tmp_path, '[ui]\nstream_mode = "messages"\n')
    cfg = CodeConfig.resolve(env={"LANGSTAGE_STREAM_MODE": "updates"}, toml_start=tmp_path)
    assert cfg.stream_mode == "auto"  # default, neither env nor TOML applied
    assert cfg.sources["stream_mode"] == "default"


def test_async_mode_toml_is_deprecated_and_ignored(isolated, tmp_path):
    # gh #88: async_mode has no effect since ADR 0003 collapsed every turn onto the one
    # async AG-UI path, so it is no longer resolved from `[ui] async_mode`. A config file
    # that still sets it must keep LOADING (the key is ignored, never an error) — a
    # langstage.toml that suddenly failed to resolve would be a far worse regression than
    # the dead knob it retires.
    _toml(tmp_path, '[agent]\nspec = "a.py:g"\n[ui]\nasync_mode = true\n')
    cfg = CodeConfig.resolve(env={}, toml_start=tmp_path)
    assert cfg.async_mode is False  # default, TOML not applied
    assert cfg.sources["async_mode"] == "default"
    # ...and the rest of the file still resolves normally.
    assert cfg.agent_spec == "a.py:g"


def test_toml_keys(isolated, tmp_path):
    _toml(
        tmp_path,
        '[agent]\nspec = "a.py:g"\ngraph_name = "myg"\n[ui]\nverbose = true\n',
    )
    cfg = CodeConfig.resolve(env={}, toml_start=tmp_path)
    assert cfg.agent_spec == "a.py:g"
    assert cfg.graph_name == "myg"
    assert cfg.verbose is True


def test_deepagent_spec_alias_is_deprecated(isolated, tmp_path):
    with pytest.warns(DeprecationWarning):
        cfg = CodeConfig.resolve(env={"DEEPAGENT_SPEC": "legacy.py:g"}, toml_start=tmp_path)
    assert cfg.agent_spec == "legacy.py:g"


def test_deepagent_spec_alias_notice_names_the_var_the_user_set(isolated, tmp_path):
    # gh #73: setting DEEPAGENT_SPEC must produce a deprecation notice that names
    # DEEPAGENT_SPEC — the var actually in the user's environment — not
    # DEEPAGENT_AGENT_SPEC, which the CLI only synthesizes internally to keep the
    # shared resolver's precedence chain intact. Before the fix the CLI copied the
    # value onto DEEPAGENT_AGENT_SPEC and the resolver then emitted the (visible)
    # notice for THAT name, pointing the user at a variable they never set.
    #
    # The stderr note derives from the same _warn_legacy_env(legacy=...) call as the
    # DeprecationWarning (and is pytest-suppressed), so asserting on the warning's
    # name is a faithful proxy for the name the user-visible note carries.
    with pytest.warns(DeprecationWarning) as records:
        cfg = CodeConfig.resolve(env={"DEEPAGENT_SPEC": "legacy.py:g"}, toml_start=tmp_path)

    assert cfg.agent_spec == "legacy.py:g"
    messages = [str(w.message) for w in records]
    assert any(m.startswith("DEEPAGENT_SPEC is deprecated") for m in messages), messages
    # The bug: a second warning named DEEPAGENT_AGENT_SPEC, a var the user never set.
    assert not any(m.startswith("DEEPAGENT_AGENT_SPEC is deprecated") for m in messages), messages


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
    # A field with a live TOML key is listed with its key (stream_mode's env/TOML
    # were removed as a deprecated no-op — gh #62 — so it's no longer var-backed).
    assert "graph_name" in text
