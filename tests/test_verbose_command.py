"""Bare `/verbose` honours its advertised "Toggle verbose output mode" contract (gh #79).

`/verbose` is registered — and shown in `/help` — as "Toggle verbose output mode", but the
shipped bare form only *printed* the current state and told the user to type `/verbose on|off`;
it never flipped anything. A one-word command called "Toggle" that no-ops is advertised !=
honoured. `cmd_verbose` now flips the value on a bare call and reports the new state, while the
explicit `/verbose on|off` form keeps setting it. These tests fail before the fix (the bare
calls left `verbose` unchanged) and pass after.
"""

from langstage_cli.cli import cmd_verbose, command_registry


def test_bare_verbose_toggles_from_off_to_on():
    ctx = {"verbose": False}
    cmd_verbose("", ctx)
    assert ctx["verbose"] is True


def test_bare_verbose_toggles_from_on_to_off():
    ctx = {"verbose": True}
    cmd_verbose("", ctx)
    assert ctx["verbose"] is False


def test_two_bare_verbose_calls_flip_then_flip_back():
    # The exact reproduction from #79: two consecutive bare /verbose from the default.
    ctx = {"verbose": False}
    cmd_verbose("", ctx)
    assert ctx["verbose"] is True
    cmd_verbose("", ctx)
    assert ctx["verbose"] is False


def test_bare_verbose_defaults_to_off_then_toggles_on():
    # No "verbose" key set yet — defaults to off, so a bare toggle turns it on.
    ctx = {}
    cmd_verbose("", ctx)
    assert ctx["verbose"] is True


def test_bare_verbose_reports_the_new_state():
    ctx = {"verbose": False}
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_verbose("", ctx)
    out = buf.getvalue()
    # Reports the *new* state (enabled), not a bare "Verbose mode: off" read-out.
    assert "enabled" in out
    assert "Use /verbose on or /verbose off to change" not in out


def test_explicit_on_off_still_sets_state():
    ctx = {"verbose": False}
    cmd_verbose("on", ctx)
    assert ctx["verbose"] is True
    cmd_verbose("off", ctx)
    assert ctx["verbose"] is False
    # Idempotent: explicit set does not toggle — /verbose off from off stays off.
    cmd_verbose("off", ctx)
    assert ctx["verbose"] is False


def test_unknown_arg_does_not_change_state_or_falsely_confirm():
    ctx = {"verbose": True}
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_verbose("bogus", ctx)
    out = buf.getvalue()
    assert ctx["verbose"] is True  # unchanged
    assert "Usage:" in out
    assert "enabled" not in out and "disabled" not in out


def test_help_description_stays_toggle():
    # The command is still advertised as a toggle; behaviour now matches the ad.
    cmd = command_registry.get("verbose")
    assert cmd is not None
    assert cmd.description == "Toggle verbose output mode"
