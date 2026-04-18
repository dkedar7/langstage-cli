"""TOML configuration loader for deepagent-code.

Reads two files and merges them (project wins on conflict):
- ~/.deepagents/config.toml — shared with the upstream deepagents CLI
- deepagents.toml — nearest ancestor of cwd, per-project overrides
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


GLOBAL_CONFIG_PATH = Path.home() / ".deepagents" / "config.toml"
PROJECT_CONFIG_NAME = "deepagents.toml"


class ConfigError(Exception):
    """Raised when a config file exists but cannot be parsed."""


def global_config_path() -> Path:
    override = os.getenv("DEEPAGENTS_CONFIG_HOME")
    if override:
        return Path(override).expanduser() / "config.toml"
    return GLOBAL_CONFIG_PATH


def find_project_config(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from `start` (or cwd) looking for deepagents.toml."""
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / PROJECT_CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def _read_toml(path: Path) -> Dict[str, Any]:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Invalid TOML in {path}: {e}") from e


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge overlay into base. Overlay wins on leaf conflicts."""
    result = dict(base)
    for key, value in overlay.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(start: Optional[Path] = None) -> Tuple[Dict[str, Any], List[Path]]:
    """Load global + project TOML, merged. Returns (config, sources_used)."""
    sources: List[Path] = []
    merged: Dict[str, Any] = {}

    gpath = global_config_path()
    if gpath.exists():
        merged = _deep_merge(merged, _read_toml(gpath))
        sources.append(gpath)

    ppath = find_project_config(start)
    if ppath is not None:
        merged = _deep_merge(merged, _read_toml(ppath))
        sources.append(ppath)

    return merged, sources


def get(config: Dict[str, Any], dotted_key: str, default: Any = None) -> Any:
    """Fetch a nested value via dotted path, e.g. 'ui.verbose'."""
    node: Any = config
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def resolve(
    config: Dict[str, Any],
    dotted_key: str,
    cli_value: Any = None,
    env_var: Optional[str] = None,
    default: Any = None,
    cast: Optional[type] = None,
) -> Any:
    """Resolve a value with precedence: CLI > env > TOML > default."""
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
