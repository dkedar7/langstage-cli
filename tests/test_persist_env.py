"""``LANGSTAGE_PERSIST`` uses core's strict boolean rules (gh #151).

Any value outside ``1/true/yes/on`` used to mean "off", so ``LANGSTAGE_PERSIST=enabled``
(an attempt to turn it ON) silently turned persistence off, and ``--show-config``
credited ``[env:LANGSTAGE_PERSIST]``. A malformed value now gets the same one-line note
as every other boolean env var, and the lower layers decide.
"""

import re

import pytest
from click.testing import CliRunner

from langstage_cli import sessions
from langstage_cli.cli import main


@pytest.fixture(autouse=True)
def _fresh_note_dedupe(monkeypatch):
    # Core prints each malformed-env note once per process; reset so every test sees it.
    from langstage_core.host import config as core_config

    monkeypatch.setattr(core_config, "_warned_malformed_env_value", set())


def test_malformed_value_falls_back_to_default_on_with_a_note(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LANGSTAGE_PERSIST", "enabled")
    r = CliRunner().invoke(main, ["--show-config"])
    assert r.exit_code == 0, r.output
    assert re.search(r"persist\s*=\s*True\s*\[default\]", r.stdout), r.stdout
    assert "[env:LANGSTAGE_PERSIST]" not in r.stdout
    assert "note: ignoring malformed LANGSTAGE_PERSIST='enabled'" in r.stderr, r.stderr
    assert "using default True" in r.stderr, r.stderr


def test_malformed_value_still_persists_the_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LANGSTAGE_PERSIST", "enabled")
    r = CliRunner().invoke(main, ["--demo", "--no-interactive", "hi"])
    assert r.exit_code == 0, r.output
    assert len(sessions.list_sessions(tmp_path.resolve())) == 1


def test_malformed_value_falls_back_to_the_toml_value(tmp_path, monkeypatch):
    (tmp_path / "langstage.toml").write_text("[session]\npersist = false\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LANGSTAGE_PERSIST", "nope-ish")
    r = CliRunner().invoke(main, ["--show-config"])
    assert re.search(r"persist\s*=\s*False\s*\[toml \(session\.persist\)\]", r.stdout), r.stdout
    assert "using False (toml (session.persist)) instead" in r.stderr, r.stderr


@pytest.mark.parametrize("value,expected", [("off", "False"), ("NO", "False"), ("On", "True")])
def test_recognized_values_are_still_honored(tmp_path, monkeypatch, value, expected):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LANGSTAGE_PERSIST", value)
    r = CliRunner().invoke(main, ["--show-config"])
    assert re.search(rf"persist\s*=\s*{expected}\s*\[env:LANGSTAGE_PERSIST\]", r.stdout), r.stdout
    assert "malformed" not in r.stderr
