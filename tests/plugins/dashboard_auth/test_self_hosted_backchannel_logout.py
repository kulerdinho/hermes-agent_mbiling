"""Back-channel logout (OIDC Back-Channel Logout 1.0) for the self-hosted OIDC provider.

Contract: once the IDP has told the gateway a session is closed (device revoked, sign-out
everywhere), an ID token of that session no longer opens anything, even before its ``exp``; and
only a logout token signed by the IDP for this client can close a session.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mbiling_cli.dashboard_auth import InvalidCodeError, clear_providers, register_provider
from mbiling_cli.dashboard_auth.routes import router
from plugins.dashboard_auth.self_hosted import deconnexions
from tests.plugins.dashboard_auth.test_self_hosted_provider import (  # noqa: F401 (fixture)
    _CLIENT_ID,
    _ISSUER,
    _make_provider,
    _mint_id_token,
    rsa_keypair,
)


def _logout_token(rsa_keypair: dict[str, Any], **claims: Any) -> str:
    contenu = {"iss": _ISSUER, "aud": _CLIENT_ID, "iat": int(time.time()), "jti": uuid.uuid4().hex,
               "events": {deconnexions.EVENEMENT: {}}, **claims}
    contenu = {k: v for k, v in contenu.items() if v is not None}
    return jwt.encode(contenu, rsa_keypair["private_pem"], algorithm="RS256", headers={"kid": rsa_keypair["kid"]})


def test_closed_session_token_no_longer_verifies(rsa_keypair):
    p = _make_provider(rsa_keypair)
    fermee = _mint_id_token(rsa_keypair, extra_claims={"sid": "session-pc-1"})
    autre = _mint_id_token(rsa_keypair, extra_claims={"sid": "session-pc-2"})
    assert p.verify_session(access_token=fermee) is not None

    p.backchannel_logout(_logout_token(rsa_keypair, sub="usr_abc", sid="session-pc-1"))

    assert p.verify_session(access_token=fermee) is None
    assert p.verify_session(access_token=autre) is not None


def test_user_wide_logout_closes_only_earlier_sessions(rsa_keypair):
    p = _make_provider(rsa_keypair)
    avant = _mint_id_token(rsa_keypair, extra_claims={"sid": "s-avant", "iat": int(time.time()) - 30})

    p.backchannel_logout(_logout_token(rsa_keypair, sub="usr_abc"))
    apres = _mint_id_token(rsa_keypair, extra_claims={"sid": "s-apres", "iat": int(time.time()) + 1})

    assert p.verify_session(access_token=avant) is None
    assert p.verify_session(access_token=apres) is not None


@pytest.mark.parametrize("modification", [
    {"events": None},                       # not a logout event
    {"nonce": "n"},                         # a logout token never carries a nonce
    {"aud": "un-autre-client"},             # issued for another client
    {"iss": "https://faux.example.com"},    # another issuer
    {"sid": None, "sub": None},             # nothing to close
])
def test_invalid_logout_token_is_rejected_and_closes_nothing(rsa_keypair, modification):
    p = _make_provider(rsa_keypair)
    jeton = _mint_id_token(rsa_keypair, extra_claims={"sid": "session-pc-1"})

    with pytest.raises(InvalidCodeError):
        p.backchannel_logout(_logout_token(rsa_keypair, **{"sub": "usr_abc", "sid": "session-pc-1", **modification}))

    assert p.verify_session(access_token=jeton) is not None


def test_closed_session_survives_a_restart(rsa_keypair):
    jeton = _mint_id_token(rsa_keypair, extra_claims={"sid": "session-pc-1"})
    _make_provider(rsa_keypair).backchannel_logout(_logout_token(rsa_keypair, sub="usr_abc", sid="session-pc-1"))
    deconnexions._cache["cle"] = None  # a fresh process re-reads the file

    assert _make_provider(rsa_keypair).verify_session(access_token=jeton) is None


def test_route_accepts_only_a_valid_logout_token(rsa_keypair):
    p = _make_provider(rsa_keypair)
    clear_providers()
    register_provider(p)
    try:
        client = TestClient(_app_with(router))
        assert client.post("/auth/backchannel-logout", data={"logout_token": "pas-un-jeton"}).status_code == 400
        rep = client.post("/auth/backchannel-logout",
                          data={"logout_token": _logout_token(rsa_keypair, sub="usr_abc", sid="session-pc-1")})
        assert rep.status_code == 200
        assert rep.headers["cache-control"] == "no-store"
    finally:
        clear_providers()
    assert p.verify_session(access_token=_mint_id_token(rsa_keypair, extra_claims={"sid": "session-pc-1"})) is None


def _app_with(r) -> FastAPI:
    app = FastAPI()
    app.include_router(r)
    return app
