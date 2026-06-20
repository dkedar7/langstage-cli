"""Regression: the headline `langstage-cli --demo` must not crash on a non-UTF-8
console (Windows cp1252).

The spinner (Braille frames) and status glyphs (✓ ⏺) raised UnicodeEncodeError on
a default Windows console, so the documented one-liner crashed before output.
`main()` now reconfigures stdio to UTF-8 (errors="replace"). We force `cp1252`
via PYTHONIOENCODING so this reproduces on any platform — without the fix this
test exits non-zero with a UnicodeEncodeError traceback.
"""

import os
import subprocess
import sys


def test_demo_runs_on_cp1252_console():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp1252"
    env.pop("PYTHONUTF8", None)
    proc = subprocess.run(
        [sys.executable, "-c", "from langstage_cli.cli import main; main()", "--demo", "hello"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    assert "UnicodeEncodeError" not in proc.stderr, proc.stderr
    # The demo stub echoes the input back.
    assert "demo agent" in proc.stdout.lower() or "hello" in proc.stdout.lower(), proc.stdout
