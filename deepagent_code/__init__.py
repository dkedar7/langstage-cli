"""Deprecated alias package: ``deepagent_code`` is now ``langstage_cli``.

Kept for one transition window so existing imports keep working. Import
``langstage_cli`` instead.
"""

import sys as _sys
import warnings as _warnings

import langstage_cli as _new
from langstage_cli import *  # noqa: F401,F403
from langstage_cli import cli, config  # noqa: F401

_warnings.warn(
    "deepagent_code has been renamed to langstage_cli; "
    "this alias package will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

_sys.modules[__name__ + ".cli"] = cli
_sys.modules[__name__ + ".config"] = config
__version__ = getattr(_new, "__version__", "0")
