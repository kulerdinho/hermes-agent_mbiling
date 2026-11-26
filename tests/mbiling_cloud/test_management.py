"""Management Mbiling : accès réservé (rôle + double authentification), réglages d'emails chiffrés
et appliqués au serveur de comptes, journal d'audit inaltérable. Le serveur de comptes est simulé
(httpx.MockTransport) avec de vrais jetons signés RSA."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from mbiling_cloud.management.app import Config, creer_app

EMETTEUR = "https://exemple.test/comptes/realms/mbiling"
INTERNE = "http://comptes:8080/comptes"
URL = "https://exemple.test/management"


class FauxComptes:
    """Le serveur de comptes, côté réseau interne : jetons, clés, API d'administration."""

    def __init__(self) -> None:
        self.cle = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.roles = ["management-admin"]
        self.otp = True
        self.nonce = ""
        self.realm_mis_a_jour: list[dict] = []

    def jeton(self, **contenu) -> str:
        base = {"iss": EMETTEUR, "iat": int(time.time()), "exp": int(time.time()) + 300}
        return jwt.encode({**base, **contenu}, self.cle, algorithm="RS256", headers={"kid": "k1"})

    def __call__(self, requete: httpx.Request) -> httpx.Response:
        chemin = requete.url.path
        if chemin.endswith("/protocol/openid-connect/certs"):
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.cle.public_key()))
            return httpx.Response(200, json={"keys": [{**jwk, "kid": "k1", "use": "sig", "alg": "RS256"}]})
        if chemin.endswith("/protocol/openid-connect/token"):
            champs = parse_qs(requete.content.decode())
            if champs["grant_type"] == ["client_credentials"]:
                return httpx.Response(200, json={"access_token": "jeton-api", "expires_in": 300})
            assert champs["code_verifier"][0], "PKCE obligatoire"
            return httpx.Response(200, json={
                "id_token": self.jeton(aud="management", sub="u1", email="admin@exemple.test", name="Admin",
                                       nonce=self.nonce),
                "access_token": self.jeton(aud="account", azp="management", sub="u1",
                                           realm_access={"roles": self.roles + ["offline_access"]})})
        if chemin == "/comptes/admin/realms/mbiling/users/u1/credentials":
            return httpx.Response(200, json=[{"type": "password"}] + ([{"type": "otp"}] if self.otp else []))
        if chemin == "/comptes/admin/realms/mbiling" and requete.method == "PUT":
            assert requete.headers["authorization"] == "Bearer jeton-api"
            self.realm_mis_a_jour.append(json.loads(requete.content))
            return httpx.Response(204)
        return httpx.Response(404)


@pytest.fixture
def env(tmp_path, monkeypatch):
    for nom in ("MBILING_EMAIL_SERVEUR", "MBILING_EMAIL_MOT_DE_PASSE"):
        monkeypatch.delenv(nom, raising=False)
    faux = FauxComptes()
    envois: list[tuple] = []
    config = Config(url_publique=URL, emetteur=EMETTEUR, comptes_interne=INTERNE, royaume="mbiling",
                    client_secret="s-client", api_secret="s-api", cle="c" * 40, base=tmp_path / "m.db",
                    relais_url="http://relais:8000", relais_jeton="jeton-relais")

    def fabriquer():
        app = creer_app(config, transport=httpx.MockTransport(faux),
                        envoyeur=lambda smtp, mdp, a: envois.append((smtp, mdp, a)))
        return TestClient(app, base_url="https://exemple.test", follow_redirects=False)

    return faux, fabriquer, envois, tmp_path / "m.db"


def connecter(client: TestClient, faux: FauxComptes) -> httpx.Response:
    depart = client.get("/management/connexion")
    cible = urlparse(depart.headers["location"])
    q = parse_qs(cible.query)
    assert cible.geturl().startswith(f"{EMETTEUR}/protocol/openid-connect/auth")
    assert q["code_challenge_method"] == ["S256"] and q["redirect_uri"] == [f"{URL}/retour"]
    faux.nonce = q["nonce"][0]
    return client.get("/management/retour", params={"code": "c1", "state": q["state"][0]})


def csrf(page: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', page).group(1)


def test_sans_session_on_est_renvoye_a_la_connexion(env):
    _, fabriquer, _, _ = env
    rep = fabriquer().get("/management/email")
    assert rep.status_code == 303 and rep.headers["location"] == "/management/connexion"


def test_un_compte_sans_role_management_est_refuse(env):
    faux, fabriquer, _, _ = env
    faux.roles = []
    client = fabriquer()
    assert connecter(client, faux).status_code == 403
    assert client.get("/management/").status_code == 303  # aucune session ouverte


def test_sans_double_authentification_l_acces_est_refuse(env):
    faux, fabriquer, _, _ = env
    faux.otp = False
    client = fabriquer()
    rep = connecter(client, faux)
    assert rep.status_code == 403 and "Double authentification" in rep.text


def test_un_etat_de_connexion_falsifie_est_refuse(env):
    faux, fabriquer, _, _ = env
    client = fabriquer()
    client.get("/management/connexion")
    assert client.get("/management/retour", params={"code": "c1", "state": "autre"}).status_code == 400


def test_l_admin_regle_le_smtp_le_mot_de_passe_est_chiffre_et_jamais_reaffiche(env):
    faux, fabriquer, _, base = env
    client = fabriquer()
    assert connecter(client, faux).status_code == 303
    page = client.get("/management/email")
    assert page.status_code == 200
    rep = client.post("/management/email", data={
        "csrf": csrf(page.text), "serveur": "smtp.exemple.test", "port": "587", "starttls": "on",
        "expediteur": "no-reply@exemple.test", "identifiant": "envoi", "mot_de_passe": "tres-secret-123"})
    assert rep.status_code == 200 and "appliqués" in rep.text
    assert "tres-secret-123" not in rep.text
    # Appliqué au serveur de comptes, vérification des adresses activée.
    pose = faux.realm_mis_a_jour[-1]
    assert pose["verifyEmail"] is True and pose["smtpServer"]["password"] == "tres-secret-123"
    assert pose["smtpServer"]["host"] == "smtp.exemple.test" and pose["smtpServer"]["starttls"] == "true"
    # Chiffré dans la base, absent du journal.
    brut = b"".join(bytes(v) if isinstance(v, (bytes, memoryview)) else str(v).encode()
                    for ligne in sqlite3.connect(base).execute("SELECT * FROM reglages") for v in ligne)
    assert b"tres-secret-123" not in brut
    assert "tres-secret-123" not in client.get("/management/journal").text
    # Un champ laissé vide garde l'ancien mot de passe.
    client.post("/management/email", data={
        "csrf": csrf(page.text), "serveur": "smtp2.exemple.test", "port": "465",
        "expediteur": "no-reply@exemple.test", "identifiant": "envoi", "mot_de_passe": ""})
    assert faux.realm_mis_a_jour[-1]["smtpServer"]["password"] == "tres-secret-123"
    assert faux.realm_mis_a_jour[-1]["smtpServer"]["ssl"] == "true"


def test_formulaire_sans_jeton_csrf_refuse(env):
    faux, fabriquer, _, _ = env
    client = fabriquer()
    connecter(client, faux)
    rep = client.post("/management/email", data={"serveur": "smtp.x.test", "port": "587", "expediteur": "a@b.test"})
    assert rep.status_code == 403 and not faux.realm_mis_a_jour


def test_un_lecteur_voit_mais_ne_modifie_pas(env):
    faux, fabriquer, _, _ = env
    faux.roles = ["management-lecteur"]
    client = fabriquer()
    connecter(client, faux)
    page = client.get("/management/email")
    assert page.status_code == 200 and "Enregistrer" not in page.text
    rep = client.post("/management/email", data={"csrf": csrf(page.text), "serveur": "smtp.x.test", "port": "587",
                                                 "expediteur": "a@b.test"})
    assert rep.status_code == 403 and not faux.realm_mis_a_jour
    assert client.get("/management/journal").status_code == 403


def test_email_de_test_envoye_a_l_administrateur_connecte(env):
    faux, fabriquer, envois, _ = env
    client = fabriquer()
    connecter(client, faux)
    page = client.get("/management/email")
    client.post("/management/email", data={"csrf": csrf(page.text), "serveur": "smtp.exemple.test", "port": "587",
                                           "starttls": "on", "expediteur": "no-reply@exemple.test",
                                           "identifiant": "envoi", "mot_de_passe": "mdp"})
    rep = client.post("/management/email/essai", data={"csrf": csrf(page.text)})
    assert rep.status_code == 200
    assert envois[-1][1:] == ("mdp", "admin@exemple.test")


def test_le_journal_est_inalterable_et_chaine(env):
    faux, fabriquer, _, base = env
    client = fabriquer()
    connecter(client, faux)
    page = client.get("/management/journal")
    assert "Journal intact" in page.text and "connexion" in page.text
    db = sqlite3.connect(base)
    with pytest.raises(sqlite3.DatabaseError, match="inaltérable"):
        db.execute("UPDATE journal SET qui = 'pirate'")
    with pytest.raises(sqlite3.DatabaseError, match="inaltérable"):
        db.execute("DELETE FROM journal")
    # Une ligne glissée hors de Management (sans la bonne empreinte) est détectée.
    db.execute("INSERT INTO journal (quand, qui, action, detail, ip, precedent, empreinte) "
               "VALUES (0, 'pirate', 'x', '{}', NULL, 'faux', 'faux')")
    db.commit()
    assert "altéré" in client.get("/management/journal").text


def test_les_reglages_de_env_sont_importes_une_seule_fois(env, monkeypatch):
    faux, fabriquer, _, base = env
    monkeypatch.setenv("MBILING_EMAIL_SERVEUR", "smtp.gmail.test")
    monkeypatch.setenv("MBILING_EMAIL_PORT", "587")
    monkeypatch.setenv("MBILING_EMAIL_EXPEDITEUR", "envoi@gmail.test")
    monkeypatch.setenv("MBILING_EMAIL_IDENTIFIANT", "envoi@gmail.test")
    monkeypatch.setenv("MBILING_EMAIL_MOT_DE_PASSE", "mdp-env")
    client = fabriquer()
    connecter(client, faux)
    assert "smtp.gmail.test" in client.get("/management/email").text
    monkeypatch.setenv("MBILING_EMAIL_SERVEUR", "autre.test")
    client = fabriquer()
    connecter(client, faux)
    assert "smtp.gmail.test" in client.get("/management/email").text  # pas réimporté


def test_entetes_de_securite(env):
    _, fabriquer, _, _ = env
    rep = fabriquer().get("/management/sante")
    assert "frame-ancestors 'none'" in rep.headers["content-security-policy"]
    assert rep.headers["cache-control"] == "no-store, no-transform"
