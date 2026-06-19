"""The deepagent_code → langstage_cli rename ships a deprecated alias package."""

import sys

import pytest


def test_legacy_import_works_and_warns():
    sys.modules.pop("deepagent_code", None)
    sys.modules.pop("deepagent_code.cli", None)
    sys.modules.pop("deepagent_code.config", None)
    with pytest.warns(DeprecationWarning, match="langstage_cli"):
        import deepagent_code  # noqa: F401


def test_legacy_submodules_alias_the_new_ones():
    import deepagent_code.config as old_config
    import langstage_cli.config as new_config

    assert old_config is new_config

    import deepagent_code.cli as old_cli
    import langstage_cli.cli as new_cli

    assert old_cli is new_cli


def test_legacy_package_reexports_public_api():
    import deepagent_code
    import langstage_cli

    assert callable(deepagent_code.prepare_agent_input)
    # The shim mirrors the new package's version (now derived from metadata),
    # so assert equality rather than a hard-coded literal that would drift.
    assert deepagent_code.__version__ == langstage_cli.__version__
