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


def _inscrire(client, organisation="org-test", nom="PC de Marie", utilisateur="", session=""):
    rep = client.post("/interne/appareils", json={"organisation": organisation, "nom": nom,
                                                  "utilisateur": utilisateur, "session": session},
                      headers={"Authorization": f"Bearer {JETON_INTERNE}"})
    assert rep.status_code == 200, rep.text
    return rep.json()


def _bonjour(client, jeton, info=None):
    with client.websocket_connect("/mains") as ws:
        ws.send_text(json.dumps({"t": "bonjour", "jeton": jeton, "info": info or {}}))
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

    def faux_inscrire(config, nom, utilisateur="", session=""):
        appels.append((config.organisation, nom) + ((utilisateur, session) if utilisateur or session else ()))
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

    def vers_relais(config, nom, utilisateur="", session=""):
        return _inscrire(client_relais, config.organisation, nom, utilisateur, session)

    from plugins.mbiling_mains.dashboard import plugin_api
    client, _ = _cellule(monkeypatch, session=SimpleNamespace(org_id=None))
    monkeypatch.setattr(plugin_api, "inscrire_appareil", vers_relais)
    jeton = client.post("/api/plugins/mbiling_mains/appareil", json={"nom": "PC"}).json()["jeton"]
    assert _bonjour(client_relais, jeton)["t"] == "bienvenue"


# --- Appareils rattachés au compte : « Mes appareils » et révocation (C6) -------------------------

INTERNE = {"Authorization": f"Bearer {JETON_INTERNE}"}


def _appareils(client, utilisateur):
    rep = client.get("/interne/appareils", params={"utilisateur": utilisateur}, headers=INTERNE)
    assert rep.status_code == 200, rep.text
    return {a["identifiant"]: a for a in rep.json()["appareils"]}


def test_chaque_compte_ne_voit_que_ses_appareils(relais):
    client, fichier = relais
    marie = _inscrire(client, nom="PC de Marie", utilisateur="u-marie", session="s-marie")
    _inscrire(client, nom="PC de Paul", utilisateur="u-paul", session="s-paul")
    _bonjour(client, marie["jeton"], {"systeme": "win32 10.0.22631"})

    vus = _appareils(client, "u-marie")
    assert list(vus) == [marie["appareil"]]
    fiche = vus[marie["appareil"]]
    assert fiche["nom"] == "PC de Marie" and fiche["session"] == "s-marie"
    assert fiche["systeme"] == "win32 10.0.22631" and fiche["derniere_connexion"]
    assert "jeton" not in json.dumps(fiche) and marie["jeton"] not in json.dumps(fiche)
    assert _appareils(client, "") == {}


def test_revoquer_une_session_coupe_seulement_ses_appareils(relais):
    client, _ = relais
    pc1 = _inscrire(client, utilisateur="u-marie", session="s-1")
    pc2 = _inscrire(client, utilisateur="u-marie", session="s-2")
    rep = client.post("/interne/appareils/revoquer", json={"utilisateur": "u-marie", "session": "s-1"}, headers=INTERNE)
    assert rep.json() == {"revoques": [pc1["appareil"]]}
    assert _bonjour(client, pc1["jeton"])["t"] == "refus"
    assert _bonjour(client, pc2["jeton"])["t"] == "bienvenue"
    assert list(_appareils(client, "u-marie")) == [pc2["appareil"]]


def test_tout_deconnecter_ne_touche_pas_les_appareils_d_un_autre_compte(relais):
    client, _ = relais
    pc_marie = _inscrire(client, utilisateur="u-marie", session="s-1")
    pc_paul = _inscrire(client, utilisateur="u-paul", session="s-2")
    # Un identifiant d'un autre compte est ignoré : on ne révoque que les PC du compte nommé.
    rep = client.post("/interne/appareils/revoquer",
                      json={"utilisateur": "u-marie", "identifiant": pc_paul["appareil"]}, headers=INTERNE)
    assert rep.json() == {"revoques": []}
    rep = client.post("/interne/appareils/revoquer", json={"utilisateur": "u-marie"}, headers=INTERNE)
    assert rep.json() == {"revoques": [pc_marie["appareil"]]}
    assert _bonjour(client, pc_paul["jeton"])["t"] == "bienvenue"


def test_revoquer_coupe_les_mains_connectees(relais):
    client, _ = relais
    pc = _inscrire(client, utilisateur="u-marie", session="s-1")
    with client.websocket_connect("/mains") as ws:
        ws.send_text(json.dumps({"t": "bonjour", "jeton": pc["jeton"], "info": {}}))
        assert json.loads(ws.receive_text())["t"] == "bienvenue"
        client.post("/interne/appareils/revoquer", json={"utilisateur": "u-marie", "session": "s-1"}, headers=INTERNE)
        with pytest.raises(Exception):
            ws.receive_text()


def test_liste_et_revocation_reservees_aux_services_internes(relais):
    client, _ = relais
    assert client.get("/interne/appareils", params={"utilisateur": "u"}).status_code == 401
    assert client.post("/interne/appareils/revoquer", json={"utilisateur": "u"}).status_code == 401
    assert client.post("/interne/appareils/revoquer", json={"utilisateur": " "}, headers=INTERNE).status_code == 400


def test_route_rattache_le_pc_au_compte_et_a_sa_session(monkeypatch):
    import jwt as pyjwt
    id_token = pyjwt.encode({"sub": "u-marie", "sid": "s-marie"}, "x" * 32, algorithm="HS256")
    session = SimpleNamespace(org_id="org-test", user_id="u-marie", access_token=id_token)
    client, appels = _cellule(monkeypatch, session=session)
    assert client.post("/api/plugins/mbiling_mains/appareil", json={"nom": "PC"}).status_code == 200
    assert appels == [("org-test", "PC", "u-marie", "s-marie")]
