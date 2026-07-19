"""Configuration for langstage-cli.

Shares the TOML loader and the ``DEEPAGENT_*`` schema with the rest of the
deep-agent family via ``langstage_core.host``: global
``~/.deepagents/config.toml`` + project ``deepagents.toml`` (merged), then env
vars, then CLI overrides.

``CodeConfig`` is langstage-cli's view of that shared config. ``load_config`` /
``get`` / ``resolve`` remain for the ``[configurable]`` passthrough and ad-hoc
lookups.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, List, Optional, Tuple

from langstage_core.host import HostConfig, load_toml_config
from langstage_core.host.config import _warn_legacy_env, _warned_legacy_env


class ConfigError(Exception):
    """Raised when a config file exists but cannot be parsed."""


def load_config(start: Optional[Path] = None) -> Tuple[dict, List[Path]]:
    """Load global + project ``langstage.toml`` (legacy ``deepagents.toml``), merged.

    Delegates to the shared loader so every LangStage tool reads the same files with
    the same precedence. Since langstage-core 1.0.3 a malformed config file is skipped
    with a stderr notice rather than raising — a typo in a config file must not crash
    the CLI (gh langstage-jupyter#42). Returns ``(config, sources_used)``.
    """
    return load_toml_config(start)


def get(config: dict, dotted_key: str, default: Any = None) -> Any:
    """Fetch a nested value via dotted path, e.g. ``'ui.verbose'``."""
    node: Any = config
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def resolve(
    config: dict,
    dotted_key: str,
    cli_value: Any = None,
    env_var: Optional[str] = None,
    default: Any = None,
    cast: Optional[type] = None,
) -> Any:
    """Resolve a single value with precedence: CLI > env > TOML > default.

    Retained for ad-hoc lookups (e.g. the ``[configurable]`` table); the
    standard keys resolve via :class:`CodeConfig`.
    """
    if cli_value is not None:
        return cli_value
    if env_var:
        env_value = os.getenv(env_var)
        if env_value is not None:
            if cast is bool:
                return env_value.lower() in ("1", "true", "yes", "on")
            if cast is not None:
                return cast(env_value)
            return env_value
    toml_value = get(config, dotted_key)
    if toml_value is not None:
        return toml_value
    return default


@dataclass
class CodeConfig(HostConfig):
    """langstage-cli's view of the shared config.

    Adds the CLI-specific keys on top of ``HostConfig``'s shared ones, resolved
    through the same ``defaults < langstage.toml < LANGSTAGE_* env <
    overrides`` chain. The legacy ``deepagents.toml`` / ``DEEPAGENT_*``
    vocabulary still resolves as a deprecated fallback (handled by the shared
    resolver); the even-older ``DEEPAGENT_SPEC`` alias is reconciled below.
    """

    # stream_mode is retained as an inert field (default only) so existing code and
    # `--stream-mode` overrides don't break, but it is DEPRECATED: it has no effect
    # since the AG-UI streaming migration (all three modes render identically). It is
    # no longer resolved from LANGSTAGE_STREAM_MODE / [ui] stream_mode, and is omitted
    # from `--show-config`. (gh #62)
    stream_mode: str = "auto"
    graph_name: str = "graph"
    verbose: bool = False
    # async_mode is likewise retained as an inert field (default only) so nothing that
    # reads the dataclass breaks, but it is DEPRECATED: ADR 0003 collapsed every turn
    # onto the single async AG-UI path, so `--async-mode` / `--sync-mode` selected
    # between two identical behaviors. It is no longer resolved from `[ui] async_mode`
    # and is omitted from `--show-config`. An existing `langstage.toml` that still sets
    # the key keeps loading — the key is simply ignored, never an error. (gh #88)
    async_mode: bool = False

    _ENV: ClassVar[dict] = {}
    _TOML: ClassVar[dict] = {
        "graph_name": "agent.graph_name",
        "verbose": "ui.verbose",
    }

    @classmethod
    def resolve(cls, *, env: Optional[dict] = None, **kwargs: Any) -> "CodeConfig":
        env = dict(os.environ if env is None else env)
        # Reconcile the oldest legacy spec var. DEEPAGENT_AGENT_SPEC itself is
        # the shared resolver's legacy twin of LANGSTAGE_AGENT_SPEC, so mapping
        # onto it keeps the full precedence chain intact.
        if (
            not env.get("LANGSTAGE_AGENT_SPEC")
            and not env.get("DEEPAGENT_AGENT_SPEC")
            and env.get("DEEPAGENT_SPEC")
        ):
            env["DEEPAGENT_AGENT_SPEC"] = env["DEEPAGENT_SPEC"]
            # Emit the deprecation signal for the var the user ACTUALLY set —
            # DEEPAGENT_SPEC — through the shared resolver's own helper, so the
            # DeprecationWarning, the visible stderr note, the once-per-var dedupe, the
            # LANGSTAGE_SUPPRESS_LEGACY_NOTICE opt-out, and the pytest-note suppression
            # all match every other legacy-env notice (gh #73).
            _warn_legacy_env("DEEPAGENT_SPEC", "LANGSTAGE_AGENT_SPEC")
            # The copy above populated DEEPAGENT_AGENT_SPEC only to keep the shared
            # resolver's precedence chain intact; the user never set that var. Mark it
            # already-warned so the resolver stays silent about it — otherwise the one
            # signal a real user actually sees (the stderr note) would name a variable
            # absent from their environment, defeating the deprecation nudge (gh #73).
            _warned_legacy_env.add("DEEPAGENT_AGENT_SPEC")
        return super().resolve(env=env, **kwargs)
