"""Configuration for deepagent-code.

Shares the TOML loader and the ``DEEPAGENT_*`` schema with the rest of the
deep-agent family via ``langgraph_stream_parser.host``: global
``~/.deepagents/config.toml`` + project ``deepagents.toml`` (merged), then env
vars, then CLI overrides.

``CodeConfig`` is deepagent-code's view of that shared config. ``load_config`` /
``get`` / ``resolve`` remain for the ``[configurable]`` passthrough and ad-hoc
lookups.
"""
import os
import tomllib
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, List, Optional, Tuple

from langgraph_stream_parser.host import HostConfig, load_toml_config


class ConfigError(Exception):
    """Raised when a config file exists but cannot be parsed."""


def load_config(start: Optional[Path] = None) -> Tuple[dict, List[Path]]:
    """Load global + project ``deepagents.toml``, merged (project wins).

    Delegates to the shared loader so every deep-agent tool reads the same
    files with the same precedence. Returns ``(config, sources_used)``.
    """
    try:
        return load_toml_config(start)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Invalid TOML: {e}") from e


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
    """deepagent-code's view of the shared config.

    Adds the CLI-specific keys on top of ``HostConfig``'s shared ones, resolved
    through the same ``defaults < deepagents.toml < DEEPAGENT_* env <
    overrides`` chain. ``DEEPAGENT_AGENT_SPEC`` is canonical;
    ``DEEPAGENT_SPEC`` is a deprecated alias.
    """

    stream_mode: str = "updates"
    graph_name: str = "graph"
    verbose: bool = False
    async_mode: bool = False

    _ENV: ClassVar[dict] = {
        "stream_mode": ("DEEPAGENT_STREAM_MODE", str),
    }
    _TOML: ClassVar[dict] = {
        "stream_mode": "ui.stream_mode",
        "graph_name": "agent.graph_name",
        "verbose": "ui.verbose",
        "async_mode": "ui.async_mode",
    }

    @classmethod
    def resolve(cls, *, env: Optional[dict] = None, **kwargs: Any) -> "CodeConfig":
        env = dict(os.environ if env is None else env)
        # Reconcile the legacy spec var — DEEPAGENT_AGENT_SPEC is canonical.
        if not env.get("DEEPAGENT_AGENT_SPEC") and env.get("DEEPAGENT_SPEC"):
            env["DEEPAGENT_AGENT_SPEC"] = env["DEEPAGENT_SPEC"]
            warnings.warn(
                "DEEPAGENT_SPEC is deprecated; use DEEPAGENT_AGENT_SPEC.",
                DeprecationWarning,
                stacklevel=2,
            )
        return super().resolve(env=env, **kwargs)
