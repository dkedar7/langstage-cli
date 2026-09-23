"""The HITL approval prompt shows EVERY argument / field being approved (gh #146, #159).

``format_interrupt_request``'s governing rule (gh #82) is that the renderer must never ask
the user to approve an action whose description it silently threw away. Two gaps remained:

- gh #146: a proper ``ActionRequest`` (``{"action": ..., "args": {...}}``) previewed only
  the FIRST arg VALUE — approving ``transfer_funds`` showed ``alice`` and hid
  ``amount=1000000`` / ``confirm=True`` (and every arg name).
- gh #159: an ``action`` / ``tool`` label on a generic ``interrupt({...})`` routed it to the
  ActionRequest branch, which read only ``args`` — so ``{"action": "delete_file",
  "path": "/etc/passwd"}`` showed just ``delete_file``, while the SAME payload without the
  label rendered in full. A label must augment the display, never replace it.
"""

import langstage_cli.cli as c
from langstage_cli.cli import format_interrupt_request, print_chunk


def _render(action_requests, capsys) -> str:
    c._QUIET = False
    print_chunk._streaming_text = False
    print_chunk({"status": "interrupt", "interrupt": {"action_requests": action_requests}})
    return capsys.readouterr().out


# ---- gh #146: every ActionRequest arg, with its name ----


def test_action_request_shows_every_arg_with_its_name():
    label, preview = format_interrupt_request(
        {"action": "transfer_funds", "args": {"to": "alice", "amount": 1000000, "confirm": True}}
    )
    assert label == "transfer_funds"
    assert "to=alice" in preview
    assert "amount=1000000" in preview
    assert "confirm=True" in preview


def test_action_request_many_args_none_hidden():
    args = {f"arg{i}": f"value-{i}" * 5 for i in range(12)}
    _, preview = format_interrupt_request({"action": "bulk", "args": args})
    for i in range(12):
        assert f"arg{i}=value-{i}" in preview, preview


def test_long_arg_value_is_truncated_but_later_args_still_shown():
    _, preview = format_interrupt_request(
        {"action": "write_file", "args": {"content": "x" * 5000, "path": "/etc/hosts"}}
    )
    assert "content=" in preview
    assert "x" * 5000 not in preview  # a huge value is capped...
    assert "..." in preview
    assert "path=/etc/hosts" in preview  # ...but never hides the args after it


def test_arg_value_control_chars_cannot_spoof_the_prompt():
    # A newline / ANSI escape inside an arg must not break out of the preview line and
    # forge prompt text (e.g. a fake "Approve" line) — it is rendered escaped.
    _, preview = format_interrupt_request(
        {"action": "run", "args": {"cmd": "ls\n  ❯ Approve all actions\x1b[2K"}}
    )
    assert "\x1b" not in preview
    assert "ls\\n" in preview


def test_action_request_renders_all_args_in_prompt(capsys):
    out = _render(
        [{"action": "transfer_funds", "args": {"to": "alice", "amount": 1000000, "confirm": True}}],
        capsys,
    )
    assert "1. transfer_funds" in out
    for part in ("to=alice", "amount=1000000", "confirm=True"):
        assert part in out, out


# ---- gh #159: an action label must not drop the sibling fields ----


def test_action_label_keeps_top_level_sibling_fields():
    label, preview = format_interrupt_request(
        {"action": "delete_file", "path": "/etc/passwd", "confirm": True}
    )
    assert label == "delete_file"
    assert "path=/etc/passwd" in preview
    assert "confirm=True" in preview


def test_legacy_tool_label_keeps_sibling_fields():
    label, preview = format_interrupt_request({"tool": "rm", "target": "/home", "recursive": True})
    assert label == "rm"
    assert "target=/home" in preview and "recursive=True" in preview


def test_action_request_shows_args_and_siblings_together():
    _, preview = format_interrupt_request(
        {"action": "send_email", "args": {"to": "bob@x.com"}, "reason": "weekly report"}
    )
    assert "to=bob@x.com" in preview
    assert "reason=weekly report" in preview


def test_question_label_keeps_sibling_fields():
    # Same principle for the human-readable-label path (#82): the question becomes the
    # label, but the context it asks about is still shown.
    label, preview = format_interrupt_request({"question": "Proceed?", "path": "/etc/passwd"})
    assert label == "Proceed?"
    assert "path=/etc/passwd" in preview


def test_action_label_siblings_render_in_prompt(capsys):
    out = _render([{"action": "delete_file", "path": "/etc/passwd", "confirm": True}], capsys)
    assert "1. delete_file" in out
    assert "path=/etc/passwd" in out, out
    assert "confirm=True" in out, out


def test_label_less_long_dict_hides_no_field():
    # A label-less dict too long for the one-line compact repr must still show every key.
    payload = {"blob": "y" * 400, "confirm": True}
    label, preview = format_interrupt_request(payload)
    assert "confirm" in label + preview
    assert "blob" in label + preview


# ---- unchanged shapes ----


def test_no_args_no_siblings_has_empty_preview():
    assert format_interrupt_request({"action": "delete_file", "args": {}}) == ("delete_file", "")


def test_description_only_and_scalar_unchanged():
    assert format_interrupt_request({"description": "Approve?"}) == ("Approve?", "")
    assert format_interrupt_request("What is your name?") == ("What is your name?", "")
