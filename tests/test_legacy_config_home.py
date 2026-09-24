"""Legacy ``DEEPAGENTS_CONFIG_HOME`` gets exactly one deprecation note (gh #154).

It relocates both the global config and the durable session store, yet it was the one
legacy alias honored silently: ``sessions.py`` (and ``--show-config``'s source label)
read it with a bare ``os.getenv``. The store location now resolves through core's
``_global_toml_path()``, whose legacy branch emits the shared once-per-var notice — so
the user sees one note, not zero and not two.
"""

import pytest
from click.testing import CliRunner

from langstage_core.host import config as core_config

from langstage_cli import sessions
from langstage_cli.cli import main

_NOTE = "DEEPAGENTS_CONFIG_HOME is deprecated"


def _unhide_notes(monkeypatch):
    # Core hides the stderr note while PYTEST_CURRENT_TEST is set, and pytest re-sets
    # that var at the start of each test phase — so drop it inside the test body.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)


@pytest.fixture
def visible_legacy_notes(monkeypatch):
    # Core dedupes the note once per process; reset that so the test sees exactly what
    # a user's terminal would.
    monkeypatch.delenv("LANGSTAGE_SUPPRESS_LEGACY_NOTICE", raising=False)
    monkeypatch.delenv("LANGSTAGE_CONFIG_HOME", raising=False)
    core_config._warned_legacy_env.discard("DEEPAGENTS_CONFIG_HOME")
    yield
    core_config._warned_legacy_env.discard("DEEPAGENTS_CONFIG_HOME")


def test_show_config_notes_the_legacy_home_exactly_once(
    tmp_path, monkeypatch, visible_legacy_notes
):
    legacy = tmp_path / "legacy_home"
    monkeypatch.setenv("DEEPAGENTS_CONFIG_HOME", str(legacy))
    # The session-store path must come from the config home here, not the hermetic
    # sessions-dir override the suite sets.
    monkeypatch.delenv("LANGSTAGE_CLI_SESSIONS_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _unhide_notes(monkeypatch)

    r = CliRunner().invoke(main, ["--show-config"])
    assert r.exit_code == 0, r.output
    # Still honored: the store is relocated under the legacy home...
    assert str(legacy / "sessions") in r.stdout, r.stdout
    assert "[env:DEEPAGENTS_CONFIG_HOME]" in r.stdout, r.stdout
    # ...and now announced, once.
    assert r.stderr.count(_NOTE) == 1, r.stderr
    assert "LANGSTAGE_CONFIG_HOME" in r.stderr, r.stderr


def test_sessions_dir_under_legacy_home_warns_via_core(
    tmp_path, monkeypatch, visible_legacy_notes, capsys
):
    legacy = tmp_path / "legacy_home"
    monkeypatch.setenv("DEEPAGENTS_CONFIG_HOME", str(legacy))
    monkeypatch.delenv("LANGSTAGE_CLI_SESSIONS_DIR", raising=False)
    _unhide_notes(monkeypatch)

    assert sessions.sessions_dir() == legacy / "sessions"
    assert sessions.sessions_dir() == legacy / "sessions"  # dedupe: still one note
    assert capsys.readouterr().err.count(_NOTE) == 1


def test_canonical_home_wins_and_is_silent(tmp_path, monkeypatch, visible_legacy_notes, capsys):
    monkeypatch.setenv("LANGSTAGE_CONFIG_HOME", str(tmp_path / "new_home"))
    monkeypatch.setenv("DEEPAGENTS_CONFIG_HOME", str(tmp_path / "legacy_home"))
    monkeypatch.delenv("LANGSTAGE_CLI_SESSIONS_DIR", raising=False)
    _unhide_notes(monkeypatch)

    assert sessions.sessions_dir() == tmp_path / "new_home" / "sessions"
    assert _NOTE not in capsys.readouterr().err


def test_default_home_is_langstage_even_with_only_a_legacy_global_file(monkeypatch, tmp_path):
    # With no override, sessions stay under ~/.langstage even if core would read a
    # legacy ~/.deepagents/config.toml as the global config.
    monkeypatch.delenv("LANGSTAGE_CONFIG_HOME", raising=False)
    monkeypatch.delenv("DEEPAGENTS_CONFIG_HOME", raising=False)
    monkeypatch.delenv("LANGSTAGE_CLI_SESSIONS_DIR", raising=False)
    monkeypatch.setattr(sessions.Path, "home", classmethod(lambda cls: tmp_path))
    assert sessions.sessions_dir() == tmp_path / ".langstage" / "sessions"
