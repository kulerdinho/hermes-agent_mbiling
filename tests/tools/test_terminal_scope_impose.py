"""Mbiling Cloud cell: MBILING_TERMINAL_IMPOSE wins over the profile's terminal backend."""
from __future__ import annotations

from tools.terminal_scope import terminal_env


def test_imposed_backend_overrides_process_env(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("MBILING_TERMINAL_IMPOSE", "mbiling_mains")
    assert terminal_env("TERMINAL_ENV", "local") == "mbiling_mains"


def test_imposed_backend_only_concerns_the_backend(monkeypatch):
    monkeypatch.setenv("MBILING_TERMINAL_IMPOSE", "mbiling_mains")
    monkeypatch.setenv("TERMINAL_TIMEOUT", "60")
    assert terminal_env("TERMINAL_TIMEOUT") == "60"


def test_without_lock_the_configured_backend_is_used(monkeypatch):
    monkeypatch.delenv("MBILING_TERMINAL_IMPOSE", raising=False)
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    assert terminal_env("TERMINAL_ENV", "local") == "docker"
