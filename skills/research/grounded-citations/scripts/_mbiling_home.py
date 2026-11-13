"""Resolve MBILING_HOME for standalone skill scripts.

Skill scripts may run outside the Mbiling process (system Python, nix env,
CI) where ``mbiling_constants`` is not importable.  This module provides the
same ``get_mbiling_home()`` contract without requiring it on ``sys.path``.

When ``mbiling_constants`` IS available it is used directly so profile
resolution and any future enhancements are picked up automatically.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from mbiling_constants import get_mbiling_home
except (ModuleNotFoundError, ImportError):

    def get_mbiling_home() -> Path:
        """Return the Mbiling home directory (default: ``~/.mbiling``)."""
        val = os.environ.get("MBILING_HOME", "").strip()
        return Path(val) if val else Path.home() / ".mbiling"
