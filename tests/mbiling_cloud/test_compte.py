"""Mbiling Compte, « Mes appareils » (C6) : chacun ne voit et ne révoque que ses connexions ; révoquer
ferme la session au serveur de comptes ET révoque ses PC au Relais. Serveur de comptes et Relais sont
simulés (httpx.MockTransport) avec de vrais jetons signés RSA."""
from __future__ import annotations

import json
import re
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from mbiling_cloud.compte.app import Config, creer_app

EMETTEUR = "https://exemple.test/comptes/realms/mbiling"
URL = "https://exemple.test/compte"
ADMIN = "/comptes/admin/realms/mbiling"


class FauxComptes:
    def __init__(self) -> None:
        self.cle = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.nonce = ""
        maintenant = int(time.time() * 1000)
        self.sessions = {
            "u-marie": [
                {"id": "s-navigateur", "ipAddress": "203.0.113.5", "start": maintenant, "lastAccess": maintenant,
                 "clients": {"c1": "compte"}},
                {"id": "s-pc", "ipAddress": "198.51.100.7", "start": maintenant - 9000, "lastAccess": maintenant - 5000,
                 "clients": {"c2": "cellule"}},
            ],
            "u-paul": [{"id": "s-paul", "ipAddress": "192.0.2.1", "start": maintenant, "lastAccess": maintenant,
                        "clients": {"c2": "cellule"}}],
        }
        self.fermees: list[str] = []
        self.deconnectes: list[str] = []

    def jeton(self, **contenu) -> str:
        base = {"iss": EMETTEUR, "iat": int(time.time()), "exp": int(time.time()) + 300}
        return jwt.encode({**base, **contenu}, self.cle, algorithm="RS256", headers={"kid": "k1"})

    def __call__(self, requete: httpx.Request) -> httpx.Response:
        chemin, methode = requete.url.path, requete.method
        if chemin.endswith("/protocol/openid-connect/certs"):
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.cle.public_key()))
            return httpx.Response(200, json={"keys": [{**jwk, "kid": "k1", "use": "sig", "alg": "RS256"}]})
        if chemin.endswith("/protocol/openid-connect/token"):
            champs = parse_qs(requete.content.decode())
            if champs["grant_type"] == ["client_credentials"]:
                return httpx.Response(200, json={"access_token": "jeton-api", "expires_in": 300})
            assert champs["code_verifier"][0], "PKCE obligatoire"
            return httpx.Response(200, json={"id_token": self.jeton(
                aud="compte", sub="u-marie", sid="s-navigateur", email="marie@exemple.test", nonce=self.nonce)})
        assert requete.headers["authorization"] == "Bearer jeton-api"
        if m := re.fullmatch(rf"{ADMIN}/users/([^/]+)/sessions", chemin):
            return httpx.Response(200, json=self.sessions.get(m[1], []))
        if (m := re.fullmatch(rf"{ADMIN}/sessions/([^/]+)", chemin)) and methode == "DELETE":
            self.fermees.append(m[1])
            for liste in self.sessions.values():
                liste[:] = [s for s in liste if s["id"] != m[1]]
            return httpx.Response(204)
        if (m := re.fullmatch(rf"{ADMIN}/users/([^/]+)/logout", chemin)) and methode == "POST":
            self.deconnectes.append(m[1])
            self.sessions[m[1]] = []
            return httpx.Response(204)
        return httpx.Response(404)


class FauxRelais:
    def __init__(self) -> None:
        self.appareils = {
            "u-marie": [{"identifiant": "pc-marie", "nom": "PC-DE-MARIE", "systeme": "win32 10.0.22631",
                         "session": "s-pc", "cree": 1, "derniere_connexion": 2, "connecte": True},
                        {"identifiant": "pc-ancien", "nom": "VIEUX-PC", "systeme": "", "session": "s-expiree",
                         "cree": 1, "derniere_connexion": None, "connecte": False}],
            "u-paul": [{"identifiant": "pc-paul", "nom": "PC-DE-PAUL", "systeme": "", "session": "s-paul",
                        "cree": 1, "derniere_connexion": None, "connecte": True}],
        }
        self.revocations: list[dict] = []

    def __call__(self, requete: httpx.Request) -> httpx.Response:
        assert requete.headers["authorization"] == "Bearer jeton-relais"
        if requete.url.path == "/interne/appareils":
            return httpx.Response(200, json={"appareils": self.appareils.get(requete.url.params["utilisateur"], [])})
        if requete.url.path == "/interne/appareils/revoquer":
            demande = json.loads(requete.content)
            self.revocations.append(demande)
            return httpx.Response(200, json={"revoques": [
                a["identifiant"] for a in self.appareils.get(demande["utilisateur"], [])
                if (not demande["session"] or a["session"] == demande["session"])
                and (not demande["identifiant"] or a["identifiant"] == demande["identifiant"])]})
        return httpx.Response(404)


@pytest.fixture
def espace():
    comptes, relais = FauxComptes(), FauxRelais()
    config = Config(url_publique=URL, emetteur=EMETTEUR, comptes_interne="http://comptes:8080/comptes",
                    royaume="mbiling", client_secret="secret-compte", cle="c" * 40,
                    relais_url="http://relais:8000", relais_jeton="jeton-relais")
    app = creer_app(config, transport=httpx.MockTransport(comptes), transport_relais=httpx.MockTransport(relais))
    return TestClient(app, base_url="https://exemple.test"), comptes, relais


def _connecter(client, comptes):
    aller = client.get("/compte/connexion", follow_redirects=False)
    params = parse_qs(urlparse(aller.headers["location"]).query)
    assert params["client_id"] == ["compte"] and params["code_challenge_method"] == ["S256"]
    comptes.nonce = params["nonce"][0]
    rep = client.get("/compte/retour", params={"code": "c", "state": params["state"][0]}, follow_redirects=False)
    assert rep.headers["location"] == "/compte/appareils"
    page = client.get("/compte/appareils")
    assert page.status_code == 200
    return re.search(r'name="csrf" value="([^"]+)"', page.text)[1], page.text


def test_sans_connexion_on_va_a_la_page_de_connexion(espace):
    client, _, _ = espace
    rep = client.get("/compte/appareils", follow_redirects=False)
    assert rep.status_code == 303 and rep.headers["location"] == "/compte/connexion"


def test_la_page_liste_mes_appareils_et_seulement_les_miens(espace):
    client, comptes, _ = espace
    _, page = _connecter(client, comptes)
    assert "PC-DE-MARIE" in page and "win32 10.0.22631" in page and "198.51.100.7" in page
    assert "VIEUX-PC" in page and "Session terminée" in page
    assert "cet appareil" in page
    assert "PC-DE-PAUL" not in page and "s-paul" not in page


def test_revoquer_un_pc_ferme_sa_session_et_coupe_ses_mains(espace):
    client, comptes, relais = espace
    csrf, _ = _connecter(client, comptes)
    rep = client.post("/compte/appareils/revoquer", data={"csrf": csrf, "session": "s-pc"})
    assert rep.status_code == 200 and "Appareil révoqué" in rep.text
    assert comptes.fermees == ["s-pc"]
    assert relais.revocations == [{"utilisateur": "u-marie", "session": "s-pc", "identifiant": ""}]


def test_revoquer_un_pc_dont_la_session_est_terminee(espace):
    client, comptes, relais = espace
    csrf, _ = _connecter(client, comptes)
    client.post("/compte/appareils/revoquer", data={"csrf": csrf, "appareil": "pc-ancien"})
    assert comptes.fermees == []
    assert relais.revocations == [{"utilisateur": "u-marie", "session": "", "identifiant": "pc-ancien"}]


def test_on_ne_peut_pas_revoquer_la_session_d_un_autre_compte(espace):
    client, comptes, relais = espace
    csrf, _ = _connecter(client, comptes)
    rep = client.post("/compte/appareils/revoquer", data={"csrf": csrf, "session": "s-paul"})
    assert rep.status_code == 404
    assert comptes.fermees == [] and relais.revocations == []


def test_sans_jeton_de_formulaire_rien_n_est_revoque(espace):
    client, comptes, relais = espace
    _connecter(client, comptes)
    assert client.post("/compte/appareils/revoquer", data={"session": "s-pc"}).status_code == 403
    assert client.post("/compte/appareils/tout", data={"csrf": "faux"}).status_code == 403
    assert comptes.fermees == [] and comptes.deconnectes == [] and relais.revocations == []


def test_tout_deconnecter(espace):
    client, comptes, relais = espace
    csrf, _ = _connecter(client, comptes)
    rep = client.post("/compte/appareils/tout", data={"csrf": csrf}, follow_redirects=False)
    assert rep.headers["location"] == "/compte/connexion"
    assert comptes.deconnectes == ["u-marie"]
    assert relais.revocations == [{"utilisateur": "u-marie", "session": "", "identifiant": ""}]


def test_revoquer_ma_propre_session_me_deconnecte(espace):
    client, comptes, _ = espace
    csrf, _ = _connecter(client, comptes)
    rep = client.post("/compte/appareils/revoquer", data={"csrf": csrf, "session": "s-navigateur"}, follow_redirects=False)
    assert rep.headers["location"] == "/compte/connexion"
    assert client.get("/compte/appareils", follow_redirects=False).headers["location"] == "/compte/connexion"


def test_une_session_fermee_ailleurs_renvoie_a_la_connexion(espace):
    client, comptes, _ = espace
    _connecter(client, comptes)
    comptes.sessions["u-marie"] = [s for s in comptes.sessions["u-marie"] if s["id"] != "s-navigateur"]
    assert client.get("/compte/appareils", follow_redirects=False).headers["location"] == "/compte/connexion"
