"""Mbiling: the phone app signs in through the native flow with its own return address
(``dashboard.oauth.retours_applis``), the desktop's loopback rule staying unchanged."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from mbiling_cli import web_server
from mbiling_cli.dashboard_auth import clear_providers, native_flow, register_provider, retours_applis
from tests.mbiling_cli.conftest_dashboard_auth import StubAuthProvider
from tests.mbiling_cli.test_dashboard_auth_native_flow import _make_pkce

RETOUR = "mbiling://connexion"


def _config(monkeypatch, retours):
    from mbiling_cli import config
    monkeypatch.setattr(
        config, "load_config_readonly",
        lambda *a, **k: {"dashboard": {"oauth": {"retours_applis": retours}}})


@pytest.fixture
def client():
    clear_providers()
    register_provider(StubAuthProvider())
    etat = {k: getattr(web_server.app.state, k, None) for k in ("bound_host", "bound_port", "auth_required")}
    web_server.app.state.bound_host = "cellule.mbiling.test"
    web_server.app.state.bound_port = 443
    web_server.app.state.auth_required = True
    native_flow._pending.clear()
    native_flow._issued.clear()
    yield TestClient(web_server.app, base_url="https://cellule.mbiling.test", follow_redirects=False)
    clear_providers()
    for k, v in etat.items():
        setattr(web_server.app.state, k, v)


def _autoriser(client, redirect_uri, challenge, state="etat-mobile"):
    return client.get("/auth/native/authorize", params={
        "provider": "stub", "code_challenge": challenge, "code_challenge_method": "S256",
        "redirect_uri": redirect_uri, "state": state})


@pytest.mark.parametrize("adresse", [
    "mbiling://connexion", "com.mbiling.app:/connexion", "mbiling://connexion?appareil=1"])
def test_app_specific_schemes_are_valid(adresse):
    assert retours_applis.adresse_valide(adresse)


@pytest.mark.parametrize("adresse", [
    "", "https://mbiling.com/connexion", "http://127.0.0.1:8000/cb", "javascript:alert(1)",
    "mbiling://connexion#x", "mbiling://pirate@connexion", "mbiling:// connexion", "1abc://x",
    "mbiling:", "connexion"])
def test_web_dangerous_or_malformed_addresses_are_refused(adresse):
    assert not retours_applis.adresse_valide(adresse)


def test_invalid_entries_are_dropped_from_the_setting():
    assert retours_applis.adresses_autorisees([RETOUR, "https://evil.example", " ", 3]) == {RETOUR}
    assert retours_applis.adresses_autorisees("mbiling://connexion") == frozenset(), "a list is required"


def test_default_config_accepts_no_app_address():
    from mbiling_cli.config_defaults import DEFAULT_CONFIG
    assert DEFAULT_CONFIG["dashboard"]["oauth"]["retours_applis"] == []


def test_the_phone_app_signs_in_with_its_own_return_address(client, monkeypatch):
    _config(monkeypatch, [RETOUR])
    verifier, challenge = _make_pkce()

    r = _autoriser(client, RETOUR, challenge)
    assert r.status_code == 302, r.text
    rappel = parse_qs(urlparse(r.headers["location"]).query)
    r2 = client.get("/auth/callback", params={"code": rappel["code"][0], "state": rappel["state"][0]},
                    cookies=r.cookies)
    assert r2.status_code == 302, r2.text

    retour = urlparse(r2.headers["location"])
    assert f"{retour.scheme}://{retour.netloc}{retour.path}" == RETOUR
    q = parse_qs(retour.query)
    assert q["state"] == ["etat-mobile"]

    jetons = client.post("/auth/native/token", json={"code": q["code"][0], "code_verifier": verifier})
    assert jetons.status_code == 200, jetons.text
    assert jetons.json()["token_type"] == "Bearer" and jetons.json()["access_token"]


def test_the_code_is_useless_without_the_apps_verifier(client, monkeypatch):
    _config(monkeypatch, [RETOUR])
    _verifier, challenge = _make_pkce()
    r = _autoriser(client, RETOUR, challenge)
    rappel = parse_qs(urlparse(r.headers["location"]).query)
    r2 = client.get("/auth/callback", params={"code": rappel["code"][0], "state": rappel["state"][0]},
                    cookies=r.cookies)
    code = parse_qs(urlparse(r2.headers["location"]).query)["code"][0]
    autre_verifier = "un-autre-verificateur-que-celui-de-l-application-0123456789"
    assert client.post("/auth/native/token", json={"code": code, "code_verifier": autre_verifier}).status_code == 400


@pytest.mark.parametrize(("reglage", "demande"), [
    ([], RETOUR),
    ([RETOUR], "mbiling://autre"),
    ([RETOUR], "mbiling://connexion/"),
    (["https://evil.example/cb"], "https://evil.example/cb"),
])
def test_an_unlisted_address_is_refused(client, monkeypatch, reglage, demande):
    _config(monkeypatch, reglage)
    _verifier, challenge = _make_pkce()
    r = _autoriser(client, demande, challenge)
    assert r.status_code == 400
    assert "loopback" in r.json()["detail"].lower()


def test_the_desktop_loopback_still_works_when_app_addresses_are_set(client, monkeypatch):
    _config(monkeypatch, [RETOUR])
    _verifier, challenge = _make_pkce()
    assert _autoriser(client, "http://127.0.0.1:53999/cb", challenge).status_code == 302
