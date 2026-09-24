"""Cross-invocation session persistence for langstage-cli (gh #102).

Claude-Code-style continuity at the terminal: a plain ``langstage-cli "..."`` run
persists its conversation so a later ``langstage-cli --continue`` (or ``--resume
<id>``) can pick it back up — WITHOUT the user baking a durable checkpointer into
their own graph. The CLI owns the store; any ``CompiledGraph`` (including ``--demo``)
becomes resumable.

Two pieces live here:

* **The durable store location.** State is a per-workspace SQLite file under the
  config home — ``<config-home>/sessions/<workspace-hash>.sqlite`` — where the config
  home honours ``LANGSTAGE_CONFIG_HOME`` (then legacy ``DEEPAGENTS_CONFIG_HOME``),
  defaulting to ``~/.langstage``. A dedicated ``LANGSTAGE_CLI_SESSIONS_DIR`` overrides
  the whole sessions directory (used to keep tests hermetic). Keying by the resolved
  workspace path matches how the CLI already resolves its workspace, so two projects
  never share a thread namespace. The *checkpointer itself* is attached by the CLI at
  run time (an ``AsyncSqliteSaver`` over this file) — see ``cli.py``.

* **A small session index** (a JSON sidecar next to the sqlite file) mapping each
  ``thread_id`` to ``created`` / ``updated`` timestamps and a snippet of the first user
  message, so ``--resume <id>`` can target one, ``--list-sessions`` can show them, and
  ``--continue`` can pick the most-recently-updated thread.
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from langstage_core.host.config import _global_toml_path

# Env override for the whole sessions directory. Primarily a hermeticity lever (tests
# point it at a temp dir); a user could also relocate the store with it. Deliberately
# separate from LANGSTAGE_CONFIG_HOME so pointing it at a temp dir never perturbs config
# resolution.
_SESSIONS_DIR_ENV = "LANGSTAGE_CLI_SESSIONS_DIR"

_SNIPPET_MAX = 60


def _config_home() -> Path:
    """The LangStage config home — ``LANGSTAGE_CONFIG_HOME`` (then legacy
    ``DEEPAGENTS_CONFIG_HOME``), else ``~/.langstage``.

    An override is resolved by core's ``_global_toml_path()`` — the same function that
    locates the global ``config.toml`` — so sessions live beside the config the CLI
    already reads, and the legacy ``DEEPAGENTS_CONFIG_HOME`` gets core's one-time
    deprecation notice instead of being honored silently (gh #154). With no override the
    store stays under ``~/.langstage`` even when only a legacy ``~/.deepagents`` config
    file exists (sessions are a LangStage-era feature; nothing to migrate)."""
    if config_home_source() == "default":
        return Path.home() / ".langstage"
    return _global_toml_path().parent


def config_home_source() -> str:
    """Which env var (if any) relocates the config home, for ``--show-config``'s
    ``[source]`` column — a label only; the value is always resolved through core."""
    for var in ("LANGSTAGE_CONFIG_HOME", "DEEPAGENTS_CONFIG_HOME"):
        if os.getenv(var):
            return f"env:{var}"
    return "default"


def sessions_dir() -> Path:
    """Directory holding every workspace's session store + index."""
    override = os.getenv(_SESSIONS_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return _config_home() / "sessions"


def workspace_key(workspace: Path) -> str:
    """A stable, filesystem-safe key for a resolved workspace path.

    A short SHA-256 hex of the absolute path: stable across runs, collision-safe in
    practice, and never leaks the path into a filename.
    """
    resolved = str(Path(workspace).expanduser().resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def db_path(workspace: Path) -> Path:
    """Path to this workspace's SQLite checkpoint store (parent dir ensured)."""
    d = sessions_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{workspace_key(workspace)}.sqlite"


def _index_path(workspace: Path) -> Path:
    return sessions_dir() / f"{workspace_key(workspace)}.index.json"


def _snippet(text: Optional[str]) -> str:
    """A one-line, length-capped preview of the first user message."""
    if not text:
        return ""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= _SNIPPET_MAX else flat[:_SNIPPET_MAX] + "…"


def load_index(workspace: Path) -> Dict[str, dict]:
    """Load the workspace's ``thread_id -> {created, updated, first_message}`` map.

    A missing or unreadable index degrades to empty — a corrupt sidecar must never
    stop the CLI from running a turn.
    """
    path = _index_path(workspace)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    sessions = data.get("sessions") if isinstance(data, dict) else None
    return sessions if isinstance(sessions, dict) else {}


def _save_index(workspace: Path, sessions: Dict[str, dict]) -> None:
    path = _index_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"sessions": sessions}, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic-ish swap so a crash mid-write can't corrupt the index


def record_session(workspace: Path, thread_id: str, first_message: Optional[str] = None) -> None:
    """Register a NEW thread in the index (no-op if it already exists).

    Called when a fresh session starts, so ``--continue`` / ``--list-sessions`` can see
    it even if the run later errors.
    """
    sessions = load_index(workspace)
    if thread_id in sessions:
        return
    now = time.time()
    sessions[thread_id] = {
        "created": now,
        "updated": now,
        "first_message": _snippet(first_message),
    }
    _save_index(workspace, sessions)


def touch_session(workspace: Path, thread_id: str, first_message: Optional[str] = None) -> None:
    """Bump a thread's ``updated`` timestamp (creating the entry if missing).

    ``first_message`` fills the snippet only if it is still empty — so the FIRST user
    message of a session sticks, and ``--continue`` orders by real recency of use.
    """
    sessions = load_index(workspace)
    entry = sessions.get(thread_id)
    now = time.time()
    if entry is None:
        entry = {"created": now, "updated": now, "first_message": _snippet(first_message)}
        sessions[thread_id] = entry
    else:
        entry["updated"] = now
        if not entry.get("first_message") and first_message:
            entry["first_message"] = _snippet(first_message)
    _save_index(workspace, sessions)


def most_recent_thread(workspace: Path) -> Optional[str]:
    """The most-recently-updated thread id for this workspace, or ``None`` if none."""
    sessions = load_index(workspace)
    if not sessions:
        return None
    return max(sessions.items(), key=lambda kv: kv[1].get("updated", 0))[0]


def resolve_thread(workspace: Path, ref: str) -> Optional[str]:
    """Resolve a ``--resume`` reference to a full thread id.

    Accepts an exact id or an unambiguous prefix (ids are UUIDs; ``--list-sessions``
    shows short forms). Returns the full id, or ``None`` if it matches nothing / is
    ambiguous.
    """
    sessions = load_index(workspace)
    if ref in sessions:
        return ref
    matches = [tid for tid in sessions if tid.startswith(ref)]
    return matches[0] if len(matches) == 1 else None


def list_sessions(workspace: Path) -> List[Tuple[str, dict]]:
    """All sessions for this workspace, most-recently-updated first."""
    sessions = load_index(workspace)
    return sorted(sessions.items(), key=lambda kv: kv[1].get("updated", 0), reverse=True)
