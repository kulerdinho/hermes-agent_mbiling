"""Login shells in the image keep Mbiling's own dirs on PATH.

Debian's ``/etc/profile`` resets PATH for every login shell, so without
``/etc/profile.d/mbiling.sh`` a ``bash -l`` (``docker exec <c> bash -l``, ssh, the
terminal tool's background ``bash -lic``) lost ``/opt/mbiling/bin`` and bare
``mbiling`` exited 127 (#56634).
"""
from __future__ import annotations

import subprocess


def test_login_shell_resolves_mbiling_to_the_image_shim(built_image: str) -> None:
    r = subprocess.run(
        ["docker", "run", "--rm", "-u", "mbiling", "--entrypoint", "bash", built_image,
         "-lc", 'echo "mbiling=$(command -v mbiling)"; echo "path=$PATH"'],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert r.returncode == 0, r.stderr
    lines = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    assert lines["mbiling"] == "/opt/mbiling/bin/mbiling"
    entries = lines["path"].split(":")
    assert entries.index("/opt/mbiling/bin") < entries.index("/opt/mbiling/.venv/bin")
    assert entries.count("/opt/mbiling/bin") == 1
