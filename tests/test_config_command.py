"""Interactive `/config` reports the true config source, matching `--show-config` (gh #64).

`/config` used to re-resolve `CodeConfig` at display time — but by then the startup path
had already called `apply_workspace()`, which self-publishes `LANGSTAGE_WORKSPACE_ROOT`
into `os.environ` (ADR 0005). The re-resolve saw the tool's own published var and reported
`workspace_root`'s source as `[env:LANGSTAGE_WORKSPACE_ROOT]` even when the user never set
it — diverging from `--show-config` (which runs before `apply_workspace`). `/config` now
reuses the report snapshotted at startup, before the self-publish.
"""

from langstage_cli.cli import cmd_config


def test_config_uses_startup_snapshot_not_a_reresolve(monkeypatch, capsys):
    # Reproduce the trigger: apply_workspace has self-published the workspace env.
    monkeypatch.setenv("LANGSTAGE_WORKSPACE_ROOT", "/abs/self/published/ws")
    monkeypatch.setenv("DEEPAGENT_WORKSPACE_ROOT", "/abs/self/published/ws")

    # The snapshot captured at startup (before the self-publish) shows the true source.
    snapshot = (
        "agent_spec       = a.py:graph  [override]\n"
        "workspace_root   = .           [default]   (env: LANGSTAGE_WORKSPACE_ROOT, toml: workspace.root)"
    )
    ctx = {
        "config": {
            "_resolved_config_report": snapshot,
            "_toml_sources": [],
            "configurable": {},
        }
    }

    cmd_config("", ctx)
    out = capsys.readouterr().out

    # /config prints the snapshot's true provenance...
    assert "workspace_root" in out
    assert "[default]" in out
    # ...and does NOT misreport the self-published env var as the source (the #64 bug).
    assert "[env:LANGSTAGE_WORKSPACE_ROOT]" not in out
    assert "/abs/self/published/ws" not in out
