"""Mbiling ships with Nous Portal off (``nous.enabled: false``): nothing offers it, nothing calls it."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.nous_disabled


def _set_nous(monkeypatch, value):
    from mbiling_cli import config
    monkeypatch.setattr(config, "load_config_readonly", lambda *a, **k: {"nous": value})


def test_default_config_turns_nous_off():
    from mbiling_cli.config_defaults import DEFAULT_CONFIG
    assert DEFAULT_CONFIG["nous"]["enabled"] is False


@pytest.mark.parametrize("value, expected", [({"enabled": False}, False), ({}, False), (None, False),
                                             ({"enabled": True}, True)])
def test_nous_enabled_reads_config(monkeypatch, value, expected):
    from mbiling_cli.anon_auth import nous_enabled
    _set_nous(monkeypatch, value)
    assert nous_enabled() is expected


def test_switch_off_blocks_guest_tools_and_catalogs(monkeypatch):
    _set_nous(monkeypatch, {"enabled": False, "guest": True})
    from mbiling_cli.anon_auth import guest_allowed
    from mbiling_cli.models import fetch_nous_recommended_models
    from providers import unlisted_provider_names
    from tools.managed_tool_gateway import resolve_free_search_gateway
    from tools.tool_backend_helpers import managed_nous_tools_enabled

    assert guest_allowed() is False
    assert "nous" in unlisted_provider_names()
    assert managed_nous_tools_enabled() is False
    assert resolve_free_search_gateway() is None
    assert fetch_nous_recommended_models(force_refresh=True) == {}


def test_switch_off_hides_nous_slash_commands(monkeypatch):
    _set_nous(monkeypatch, {"enabled": False})
    from mbiling_cli.commands import build_commands_by_category, resolve_command
    for name in ("login", "subscription", "topup", "upgrade"):
        assert resolve_command(name) is None
    listed = {c for group in build_commands_by_category().values() for c in group}
    assert not {"/login", "/subscription", "/topup"} & listed
    assert resolve_command("help") is not None


def test_switch_off_never_sends_shared_metrics(monkeypatch):
    _set_nous(monkeypatch, {"enabled": False})
    from mbiling_cli.observability.shared_metrics_send_config import resolve_send_config
    cfg = {"telemetry": {"shared_metrics": {"enabled": True, "send": True}}}
    assert resolve_send_config(cfg).send is False
