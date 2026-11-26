"""Management, écran Comptes (C9) : chercher un compte, voir ses connexions, ses PC et son journal,
le suspendre (connexion impossible, sessions fermées, PC révoqués, données gardées) ou le réactiver.
Serveur de comptes et Relais simulés ; vrais jetons signés RSA."""
from __future__ import annotations

import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from mbiling_cloud.management.app import Config, creer_app
from tests.mbiling_cloud.test_management import EMETTEUR, INTERNE, URL, FauxComptes, connecter, csrf

ADMIN = "/comptes/admin/realms/mbiling"
MARIE = "3f2a9c1e-0000-4000-8000-00000000abcd"
CHEF = "9b1d0c2e-0000-4000-8000-00000000c0de"


class FauxComptesAvecUtilisateurs(FauxComptes):
    def __init__(self) -> None:
        super().__init__()
        self.comptes = {
            MARIE: {"id": MARIE, "username": "marie", "email": "marie@exemple.test", "firstName": "Marie",
                    "enabled": True, "emailVerified": True, "createdTimestamp": 1_790_000_000_000},
            CHEF: {"id": CHEF, "username": "chef", "email": "chef@exemple.test", "enabled": True},
        }
        self.roles_de = {CHEF: ["management-admin"]}
        self.sessions = {MARIE: [{"id": "s1", "ipAddress": "203.0.113.5", "start": 1, "lastAccess": 2,
                                  "clients": {"c": "cellule"}}]}
        self.mises_a_jour: list[tuple[str, dict]] = []
        self.deconnectes: list[str] = []

    def __call__(self, requete: httpx.Request) -> httpx.Response:
        chemin, methode = requete.url.path, requete.method
        if chemin == f"{ADMIN}/users" and methode == "GET":
            texte = requete.url.params["search"]
            return httpx.Response(200, json=[c for c in self.comptes.values() if texte in c["email"]])
        if m := re.fullmatch(rf"{ADMIN}/users/([^/]+)", chemin):
            compte = self.comptes.get(m[1])
            if compte is None:
                return httpx.Response(404)
            if methode == "PUT":
                corps = json.loads(requete.content)
                self.mises_a_jour.append((m[1], corps))
                compte.update(corps)
                return httpx.Response(204)
            return httpx.Response(200, json=compte)
        if m := re.fullmatch(rf"{ADMIN}/users/([^/]+)/role-mappings/realm", chemin):
            return httpx.Response(200, json=[{"name": r} for r in self.roles_de.get(m[1], []) + ["offline_access"]])
        if m := re.fullmatch(rf"{ADMIN}/users/([^/]+)/sessions", chemin):
            return httpx.Response(200, json=self.sessions.get(m[1], []))
        if m := re.fullmatch(rf"{ADMIN}/users/([^/]+)/credentials", chemin):
            return httpx.Response(200, json=[{"type": "password"}, {"type": "otp"}] if m[1] == "u1" else [])
        if (m := re.fullmatch(rf"{ADMIN}/users/([^/]+)/logout", chemin)) and methode == "POST":
            self.deconnectes.append(m[1])
            self.sessions[m[1]] = []
            return httpx.Response(204)
        return super().__call__(requete)


class FauxRelais:
    def __init__(self) -> None:
        self.revocations: list[dict] = []
        self.pcs = {MARIE: [{"identifiant": "pc-1", "nom": "PC-DE-MARIE", "systeme": "win32", "session": "s1",
                             "cree": 1, "derniere_connexion": 2, "connecte": True}]}

    def __call__(self, requete: httpx.Request) -> httpx.Response:
        assert requete.headers["authorization"] == "Bearer jeton-relais"
        if requete.url.path == "/interne/appareils":
            return httpx.Response(200, json={"appareils": self.pcs.get(requete.url.params["utilisateur"], [])})
        demande = json.loads(requete.content)
        self.revocations.append(demande)
        revoques = [p["identifiant"] for p in self.pcs.pop(demande["utilisateur"], [])]
        return httpx.Response(200, json={"revoques": revoques})


@pytest.fixture
def console(tmp_path, monkeypatch):
    for nom in ("MBILING_EMAIL_SERVEUR", "MBILING_EMAIL_MOT_DE_PASSE"):
        monkeypatch.delenv(nom, raising=False)
    comptes, relais = FauxComptesAvecUtilisateurs(), FauxRelais()
    config = Config(url_publique=URL, emetteur=EMETTEUR, comptes_interne=INTERNE, royaume="mbiling",
                    client_secret="s-client", api_secret="s-api", cle="c" * 40, base=tmp_path / "m.db",
                    relais_url="http://relais:8000", relais_jeton="jeton-relais")

    def ouvrir(roles: list[str]) -> TestClient:
        comptes.roles = roles
        client = TestClient(creer_app(config, transport=httpx.MockTransport(comptes),
                                      transport_relais=httpx.MockTransport(relais)),
                            base_url="https://exemple.test", follow_redirects=False)
        assert connecter(client, comptes).status_code == 303
        return client

    return ouvrir, comptes, relais


def test_chercher_puis_ouvrir_la_fiche_d_un_compte(console):
    ouvrir, _, _ = console
    client = ouvrir(["management-support"])
    liste = client.get("/management/comptes", params={"q": "marie"})
    assert liste.status_code == 200 and f"/management/comptes/{MARIE}" in liste.text
    assert "chef@exemple.test" not in liste.text
    fiche = client.get(f"/management/comptes/{MARIE}")
    assert fiche.status_code == 200
    assert "PC-DE-MARIE" in fiche.text and "203.0.113.5" in fiche.text and "cellule" in fiche.text
    # Le Support voit, mais ne suspend pas.
    assert "Suspendre le compte" not in fiche.text


def test_le_support_ne_peut_pas_suspendre(console):
    ouvrir, comptes, relais = console
    client = ouvrir(["management-support"])
    jeton = csrf(client.get("/management/").text)
    rep = client.post(f"/management/comptes/{MARIE}/suspendre", data={"csrf": jeton, "motif": "abus"})
    assert rep.status_code == 403
    assert comptes.mises_a_jour == [] and relais.revocations == []


def test_un_lecteur_ne_voit_pas_les_comptes(console):
    ouvrir, _, _ = console
    assert ouvrir(["management-lecteur"]).get("/management/comptes").status_code == 403


def test_suspendre_coupe_tout_garde_les_donnees_et_s_inscrit_au_journal(console):
    ouvrir, comptes, relais = console
    client = ouvrir(["management-admin"])
    jeton = csrf(client.get(f"/management/comptes/{MARIE}").text)
    rep = client.post(f"/management/comptes/{MARIE}/suspendre", data={"csrf": jeton, "motif": "Paiement frauduleux"})
    assert rep.status_code == 200 and "Compte suspendu" in rep.text
    assert comptes.mises_a_jour == [(MARIE, {"enabled": False})]   # désactivé, jamais supprimé
    assert comptes.deconnectes == [MARIE]
    assert relais.revocations == [{"utilisateur": MARIE, "session": "", "identifiant": ""}]
    assert "Paiement frauduleux" in rep.text and "Réactiver le compte" in rep.text   # journal du compte
    journal = client.get("/management/journal").text
    assert "compte suspendu" in journal and "pc-1" in journal

    rep = client.post(f"/management/comptes/{MARIE}/reactiver", data={"csrf": jeton})
    assert rep.status_code == 200 and "Compte réactivé" in rep.text
    assert comptes.mises_a_jour[-1] == (MARIE, {"enabled": True})


def test_le_motif_est_obligatoire(console):
    ouvrir, comptes, _ = console
    client = ouvrir(["management-admin"])
    jeton = csrf(client.get(f"/management/comptes/{MARIE}").text)
    assert client.post(f"/management/comptes/{MARIE}/suspendre", data={"csrf": jeton, "motif": " "}).status_code == 400
    assert comptes.mises_a_jour == []


def test_un_admin_ne_suspend_ni_lui_meme_ni_un_compte_management(console):
    ouvrir, comptes, relais = console
    comptes.comptes["u1"] = {"id": "u1", "username": "admin", "email": "admin@exemple.test", "enabled": True}
    client = ouvrir(["management-admin"])
    jeton = csrf(client.get("/management/").text)
    assert client.post("/management/comptes/u1/suspendre", data={"csrf": jeton, "motif": "x"}).status_code == 400
    assert client.post(f"/management/comptes/{CHEF}/suspendre", data={"csrf": jeton, "motif": "x"}).status_code == 403
    assert comptes.mises_a_jour == [] and relais.revocations == []


def test_le_super_admin_peut_suspendre_un_compte_management(console):
    ouvrir, comptes, _ = console
    client = ouvrir(["management-superadmin"])
    jeton = csrf(client.get("/management/").text)
    assert client.post(f"/management/comptes/{CHEF}/suspendre", data={"csrf": jeton, "motif": "départ"}).status_code == 200
    assert comptes.mises_a_jour == [(CHEF, {"enabled": False})]


def test_sans_jeton_de_formulaire_rien_ne_change(console):
    ouvrir, comptes, relais = console
    client = ouvrir(["management-admin"])
    assert client.post(f"/management/comptes/{MARIE}/suspendre", data={"motif": "x"}).status_code == 403
    assert comptes.mises_a_jour == [] and relais.revocations == []


def test_un_identifiant_invalide_ou_inconnu(console):
    ouvrir, _, _ = console
    client = ouvrir(["management-admin"])
    assert client.get("/management/comptes/..%2Fadmin").status_code == 404
    assert client.get("/management/comptes/inconnu-0000").status_code == 404
