"""Inscription automatique des PC pour les Mains : Relais (registre par empreinte) et route de la cellule."""
from __future__ import annotations

import json
import secrets
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

JETON_INTERNE = secrets.token_urlsafe(32)


@pytest.fixture
def relais(tmp_path, monkeypatch):
    config = tmp_path / "relais.yaml"
    fichier = tmp_path / "appareils.json"
    config.write_text(json.dumps({
        "jeton_interne_env": "ESSAI_JETON_INTERNE",
        "fichier_appareils": str(fichier),
        "appareils": [],
        "limites": {"delai_bonjour_s": 5, "taille_script_max": 100000, "file_sortie_max": 100},
    }), encoding="utf-8")
    monkeypatch.setenv("MBILING_RELAIS_CONFIG", str(config))
    monkeypatch.setenv("ESSAI_JETON_INTERNE", JETON_INTERNE)
    from mbiling_cloud.relais.app import creer_app
    return TestClient(creer_app()), fichier


def _inscrire(client, organisation="org-test", nom="PC de Marie"):
    rep = client.post("/interne/appareils", json={"organisation": organisation, "nom": nom},
                      headers={"Authorization": f"Bearer {JETON_INTERNE}"})
    assert rep.status_code == 200, rep.text
    return rep.json()


def _bonjour(client, jeton):
    with client.websocket_connect("/mains") as ws:
        ws.send_text(json.dumps({"t": "bonjour", "jeton": jeton, "info": {}}))
        return json.loads(ws.receive_text())


def test_inscription_rend_un_jeton_une_fois_et_ne_garde_que_son_empreinte(relais):
    client, fichier = relais
    inscrit = _inscrire(client)
    assert inscrit["appareil"].startswith("pc-")
    assert len(inscrit["jeton"]) >= 32
    stocke = fichier.read_text(encoding="utf-8")
    assert inscrit["jeton"] not in stocke, "le jeton ne doit jamais être stocké en clair"
    fiche = next(iter(json.loads(stocke).values()))
    assert fiche["organisation"] == "org-test" and fiche["nom"] == "PC de Marie"


def test_un_pc_inscrit_est_accepte_et_un_jeton_inconnu_refuse(relais):
    client, _ = relais
    inscrit = _inscrire(client)
    assert _bonjour(client, inscrit["jeton"]) == {"t": "bienvenue", "appareil": inscrit["appareil"]}
    assert _bonjour(client, "x" * 43)["t"] == "refus"


def test_un_pc_revoque_est_refuse(relais):
    client, _ = relais
    inscrit = _inscrire(client)
    rep = client.delete(f"/interne/appareils/{inscrit['appareil']}", headers={"Authorization": f"Bearer {JETON_INTERNE}"})
    assert rep.status_code == 200
    assert _bonjour(client, inscrit["jeton"])["t"] == "refus"
    assert client.delete("/interne/appareils/pc-inconnu", headers={"Authorization": f"Bearer {JETON_INTERNE}"}).status_code == 404


def test_le_registre_survit_au_redemarrage_du_relais(relais, monkeypatch):
    client, _ = relais
    inscrit = _inscrire(client)
    from mbiling_cloud.relais.app import creer_app
    assert _bonjour(TestClient(creer_app()), inscrit["jeton"])["t"] == "bienvenue"


def test_inscription_reservee_a_la_cellule(relais):
    client, _ = relais
    assert client.post("/interne/appareils", json={"organisation": "org-test"}).status_code == 401
    assert client.post("/interne/appareils", json={"organisation": "org-test"},
                       headers={"Authorization": "Bearer mauvais"}).status_code == 401
    assert client.post("/interne/appareils", json={"organisation": " "},
                       headers={"Authorization": f"Bearer {JETON_INTERNE}"}).status_code == 400


# --- Route de la cellule : /api/plugins/mbiling_mains/appareil -----------------------------------

def _cellule(monkeypatch, *, session=None, par_jeton=False, inscription=None):
    from plugins.mbiling_mains.dashboard import plugin_api

    monkeypatch.setenv("MBILING_RELAIS_URL", "http://relais.test")
    monkeypatch.setenv("MBILING_RELAIS_JETON", JETON_INTERNE)
    monkeypatch.setenv("MBILING_ORGANISATION", "org-test")
    monkeypatch.setenv("MBILING_RELAIS_URL_PUBLIQUE", "wss://api.mbiling.test/mains")
    appels = []

    def faux_inscrire(config, nom):
        appels.append((config.organisation, nom))
        return inscription or {"appareil": "pc-abc", "jeton": "jeton-unique"}

    monkeypatch.setattr(plugin_api, "inscrire_appareil", faux_inscrire)
    app = FastAPI()

    @app.middleware("http")
    async def authentification(request: Request, suite):
        request.state.session = session
        request.state.token_authenticated = par_jeton
        return await suite(request)

    app.include_router(plugin_api.router, prefix="/api/plugins/mbiling_mains")
    return TestClient(app), appels


def test_route_inscrit_le_pc_de_l_utilisateur_connecte(monkeypatch):
    client, appels = _cellule(monkeypatch, session=SimpleNamespace(org_id="org-test"))
    rep = client.post("/api/plugins/mbiling_mains/appareil", json={"nom": "PC de Marie"})
    assert rep.status_code == 200
    assert rep.json() == {"appareil": "pc-abc", "jeton": "jeton-unique", "relais_url": "wss://api.mbiling.test/mains"}
    assert appels == [("org-test", "PC de Marie")]


def test_route_refuse_sans_connexion(monkeypatch):
    client, appels = _cellule(monkeypatch)
    assert client.post("/api/plugins/mbiling_mains/appareil", json={}).status_code == 401
    assert appels == []


def test_route_refuse_un_compte_d_une_autre_organisation(monkeypatch):
    client, appels = _cellule(monkeypatch, session=SimpleNamespace(org_id="autre-org"))
    assert client.post("/api/plugins/mbiling_mains/appareil", json={}).status_code == 403
    assert appels == []


def test_route_accepte_la_connexion_par_jeton_de_la_cellule(monkeypatch):
    client, appels = _cellule(monkeypatch, par_jeton=True)
    assert client.post("/api/plugins/mbiling_mains/appareil", json={"nom": "x"}).status_code == 200
    assert appels == [("org-test", "x")]


def test_relais_et_route_ensemble(relais, monkeypatch):
    """La route de la cellule obtient un vrai jeton du Relais, que le Relais accepte ensuite."""
    client_relais, _ = relais

    def vers_relais(config, nom):
        return _inscrire(client_relais, config.organisation, nom)

    from plugins.mbiling_mains.dashboard import plugin_api
    client, _ = _cellule(monkeypatch, session=SimpleNamespace(org_id=None))
    monkeypatch.setattr(plugin_api, "inscrire_appareil", vers_relais)
    jeton = client.post("/api/plugins/mbiling_mains/appareil", json={"nom": "PC"}).json()["jeton"]
    assert _bonjour(client_relais, jeton)["t"] == "bienvenue"
