"""Resolve MBILING_HOME for standalone skill scripts.

Skill scripts may run outside the Mbiling process (e.g. system Python,
nix env, CI) where ``mbiling_constants`` is not importable.  This module
provides the same ``get_mbiling_home()`` and ``display_mbiling_home()``
contracts as ``mbiling_constants`` without requiring it on ``sys.path``.

When ``mbiling_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``mbiling_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``MBILING_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from mbiling_constants import display_mbiling_home
    from mbiling_constants import get_mbiling_home
except (ModuleNotFoundError, ImportError):

    def get_mbiling_home() -> Path:
        """Return the Mbiling home directory (default: ~/.mbiling).

        Mirrors ``mbiling_constants.get_mbiling_home()``."""
        val = os.environ.get("MBILING_HOME", "").strip()
        return Path(val) if val else Path.home() / ".mbiling"

    def display_mbiling_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``mbiling_constants.display_mbiling_home()``."""
        home = get_mbiling_home()
        try:
            return "~/" + home.relative_to(Path.home()).as_posix()
        except ValueError:
            return str(home)
