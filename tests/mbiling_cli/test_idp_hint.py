"""Mbiling: "Continue with Google / Apple" preselects that sign-in on the accounts service."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.responses import RedirectResponse

from mbiling_cli.dashboard_auth import routes

IDP = "https://comptes.mbiling.test/authorize?client_id=mbiling&state=abc"


def _config(monkeypatch, hint):
    from mbiling_cli import config
    monkeypatch.setattr(config, "load_config_readonly", lambda *a, **k: {"dashboard": {"oauth": {"idp_hint": hint}}})


def _query(resp):
    return parse_qs(urlparse(resp.headers["location"]).query)


def test_an_allowed_hint_is_added_with_the_configured_parameter(monkeypatch):
    _config(monkeypatch, {"param": "kc_idp_hint", "allowed": ["google", "apple"]})
    resp = routes._with_idp_hint(RedirectResponse(IDP, status_code=302), "Google")
    q = _query(resp)
    assert q["kc_idp_hint"] == ["google"]
    assert q["client_id"] == ["mbiling"] and q["state"] == ["abc"], "the original parameters are kept"


@pytest.mark.parametrize("hint", ["", "facebook", "google&evil=1"])
def test_unknown_or_missing_hints_change_nothing(monkeypatch, hint):
    _config(monkeypatch, {"param": "kc_idp_hint", "allowed": ["google", "apple"]})
    resp = routes._with_idp_hint(RedirectResponse(IDP, status_code=302), hint)
    assert resp.headers["location"] == IDP


def test_feature_off_without_a_parameter_name(monkeypatch):
    _config(monkeypatch, {"param": "", "allowed": ["google"]})
    resp = routes._with_idp_hint(RedirectResponse(IDP, status_code=302), "google")
    assert resp.headers["location"] == IDP


def test_default_config_keeps_it_off_until_an_accounts_service_is_chosen():
    from mbiling_cli.config_defaults import DEFAULT_CONFIG
    hint = DEFAULT_CONFIG["dashboard"]["oauth"]["idp_hint"]
    assert hint["param"] == ""
    assert set(hint["allowed"]) == {"google", "apple"}
