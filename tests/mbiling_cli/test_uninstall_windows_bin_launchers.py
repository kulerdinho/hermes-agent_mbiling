"""Uninstall must not leave a dangling ``mbiling`` command on Windows.

Every uninstall mode deletes the code checkout, but the launcher copies
staged onto PATH in the managed binary dir (the default Mbiling root's
``bin``) live outside it. A surviving launcher makes ``mbiling`` in a new
terminal resolve and then error on its missing venv target — worse than
command-not-found. The dir is wholly mbiling-owned (pm keeps uv in its own
store entry), so the sweep takes everything in it.

Platform verdicts are injected parameters (input→output, not host fakes).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mbiling_cli import uninstall


@pytest.fixture
def managed_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Default-root ``bin`` holding staged launcher copies."""
    home = tmp_path / "mbiling"
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "mbiling.exe").write_bytes(b"MZ launcher")
    (bin_dir / "mbiling-acp.cmd").write_text("@echo off\r\n", encoding="ascii")
    monkeypatch.setenv("MBILING_HOME", str(home))
    return bin_dir


def test_removes_the_whole_managed_bin_dir(managed_bin: Path):
    removed = uninstall.remove_windows_bin_launchers(windows=True)

    assert sorted(p.name for p in removed) == ["mbiling-acp.cmd", "mbiling.exe"]
    assert not managed_bin.exists()


def test_anchors_on_default_root_not_profile_home(
    managed_bin: Path, monkeypatch: pytest.MonkeyPatch
):
    """The launcher dir is per-machine; a profile MBILING_HOME must not
    redirect the sweep into ``profiles/<name>/bin``."""
    home = managed_bin.parent
    monkeypatch.setenv("MBILING_HOME", str(home / "profiles" / "work"))

    removed = uninstall.remove_windows_bin_launchers(windows=True)

    assert sorted(p.name for p in removed) == ["mbiling-acp.cmd", "mbiling.exe"]
    assert not (managed_bin / "mbiling.exe").exists()


def test_noop_on_posix(managed_bin: Path):
    assert uninstall.remove_windows_bin_launchers(windows=False) == []
    assert (managed_bin / "mbiling.exe").exists()


def test_noop_when_no_bin_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "mbiling"
    home.mkdir()
    monkeypatch.setenv("MBILING_HOME", str(home))

    assert uninstall.remove_windows_bin_launchers(windows=True) == []
