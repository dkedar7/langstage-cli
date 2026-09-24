"""Interactive `/config` reports the true config source, matching `--show-config` (gh #64).

`/config` used to re-resolve `CodeConfig` at display time — but by then the startup path
had already called `apply_workspace()`, which self-publishes `LANGSTAGE_WORKSPACE_ROOT`
into `os.environ` (ADR 0005). The re-resolve saw the tool's own published var and reported
`workspace_root`'s source as `[env:LANGSTAGE_WORKSPACE_ROOT]` even when the user never set
it — diverging from `--show-config` (which runs before `apply_workspace`). `/config` now
reuses the report snapshotted at startup, before the self-publish.

The bare `/config` table also used to be rendered from a STRING frozen at startup, so after
a runtime mutation (`/verbose`, `/config verbose on`, `/reset`) it contradicted `/status`,
the single-key `/config <key>` read, and even its own `✓ Set` line — and mislabelled an
overridden value `[default]` (gh #97). It now RE-RENDERS the one `describe()` diagnostic
from the startup-resolved cfg overlaid with live state, so the live view stays consistent
while still not re-resolving (so the #64 fix above is preserved).
"""

import re

from click.testing import CliRunner

from langstage_cli.cli import cmd_config, main


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


def test_bare_config_reflects_runtime_verbose_mutation(repl_via_stdin):
    # gh #97: drive the real interactive loop — flip verbose ON, then the bare `/config`
    # table must show the LIVE value with a non-[default] source, agreeing with the
    # single-key `/config verbose` read and `/status`. Before the fix this printed the
    # frozen startup snapshot: `verbose = False [default]`, contradicting both.
    with CliRunner().isolated_filesystem():  # no stray langstage.toml
        r = CliRunner().invoke(main, ["--demo"], input="/verbose on\n/config\n/quit\n")
    assert r.exit_code == 0, r.output
    # The full-table verbose line reflects the mutation and is labelled [override].
    assert re.search(r"verbose\s*=\s*True\s*\[override\]", r.output), r.output
    # And it is NOT the stale frozen snapshot (the #97 bug).
    assert not re.search(r"verbose\s*=\s*False\s*\[default\]", r.output), r.output


def test_bare_config_agrees_with_status_and_single_key_after_verbose_on(repl_via_stdin):
    # The three views that used to diverge must now agree after a mutation (gh #97):
    # `/status` (on), single-key `/config verbose` (True), and the bare `/config` table.
    with CliRunner().isolated_filesystem():
        r = CliRunner().invoke(
            main, ["--demo"], input="/config verbose on\n/config verbose\n/status\n/config\n/quit\n"
        )
    assert r.exit_code == 0, r.output
    assert "verbose: True" in r.output  # single-key read
    assert re.search(r"Verbose:\s*on", r.output), r.output  # /status
    assert re.search(r"verbose\s*=\s*True\s*\[override\]", r.output), r.output  # full table


def test_bare_config_reflects_reset_thread_id(tmp_path, monkeypatch, repl_via_stdin):
    # gh #97 (Repro B): a `[configurable] thread_id` shown by `/config` must track a
    # runtime `/reset`, not keep printing the pre-reset value.
    (tmp_path / "langstage.toml").write_text('[configurable]\nthread_id = "T-123"\n')
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["--demo"], input="/config\n/reset\n/config\n/quit\n")
    assert r.exit_code == 0, r.output
    # First /config shows the TOML value; after /reset the second /config shows the new
    # thread_id, and the stale one no longer appears in the post-reset table.
    pre, _, post = r.output.partition("Session reset")
    assert "thread_id: T-123" in pre, r.output
    assert "thread_id: T-123" not in post, r.output
    assert re.search(r"thread_id:\s*[0-9a-f-]{16,}", post), r.output
