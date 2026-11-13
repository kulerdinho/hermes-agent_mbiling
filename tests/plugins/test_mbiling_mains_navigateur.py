"""Tunnel du navigateur (lot 6) côté Relais, avec un faux PC : découverte CDP et canal WebSocket.

Le Relais tourne dans un vrai serveur uvicorn (une seule boucle d'événements, comme en
production) : le client de test de Starlette donne à chaque requête sa propre boucle, ce qui ne
représente pas le partage des connexions entre la découverte HTTP et la connexion des Mains.
"""
from __future__ import annotations

import json
import secrets
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from websockets.sync.client import connect

JETON_INTERNE = secrets.token_urlsafe(32)
JETON_PC = secrets.token_urlsafe(32)
DELAI = 10


def _port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def relais(tmp_path, monkeypatch):
    config = tmp_path / "relais.yaml"
    config.write_text(json.dumps({
        "jeton_interne_env": "ESSAI_JETON_INTERNE",
        "appareils": [{"identifiant": "pc-1", "organisation": "org-test", "jeton_env": "ESSAI_JETON_PC"}],
        "limites": {"delai_bonjour_s": 5, "taille_script_max": 100000, "file_sortie_max": 100, "delai_navigateur_s": 5},
    }), encoding="utf-8")
    monkeypatch.setenv("MBILING_RELAIS_CONFIG", str(config))
    monkeypatch.setenv("ESSAI_JETON_INTERNE", JETON_INTERNE)
    monkeypatch.setenv("ESSAI_JETON_PC", JETON_PC)
    from mbiling_cloud.relais.app import creer_app

    port = _port_libre()
    serveur = uvicorn.Server(uvicorn.Config(creer_app(), host="127.0.0.1", port=port, log_level="warning"))
    fil = threading.Thread(target=serveur.run, daemon=True)
    fil.start()
    limite = time.monotonic() + DELAI
    while not serveur.started:
        assert time.monotonic() < limite, "le Relais n'a pas démarré"
        time.sleep(0.05)
    yield f"127.0.0.1:{port}"
    serveur.should_exit = True
    fil.join(DELAI)


def _connecter_pc(relais):
    ws = connect(f"ws://{relais}/mains", open_timeout=DELAI)
    ws.send(json.dumps({"t": "bonjour", "jeton": JETON_PC, "info": {}}))
    assert json.loads(ws.recv(DELAI))["t"] == "bienvenue"
    return ws


@pytest.fixture
def pc(relais):
    """Un faux PC connecté au Relais comme le seraient les Mains."""
    ws = _connecter_pc(relais)
    yield ws
    ws.close()


def _recevoir(ws) -> dict:
    return json.loads(ws.recv(DELAI))


def _en_fond(fn):
    resultat: dict = {}

    def courir():
        try:
            resultat["valeur"] = fn()
        except Exception as exc:
            resultat["erreur"] = exc

    fil = threading.Thread(target=courir, daemon=True)
    fil.start()
    return fil, resultat


def _decouverte(relais):
    return httpx.get(f"http://{relais}/cdp/{JETON_INTERNE}/org-test/json/version", timeout=DELAI)


def test_la_decouverte_est_repondue_par_le_pc_avec_l_adresse_du_relais(relais, pc):
    fil, res = _en_fond(lambda: _decouverte(relais))
    demande = _recevoir(pc)
    assert demande["t"] == "cdp_http" and demande["chemin"] == "/json/version" and demande["methode"] == "GET"
    corps = {"Browser": "Edg/150", "webSocketDebuggerUrl": "ws://127.0.0.1:53111/devtools/browser/abc"}
    pc.send(json.dumps({"t": "cdp_http_rep", "id": demande["id"], "statut": 200, "corps": json.dumps(corps)}))
    fil.join(DELAI)
    assert "erreur" not in res, res.get("erreur")
    reponse = res["valeur"].json()
    assert reponse["webSocketDebuggerUrl"] == f"ws://{relais}/cdp/{JETON_INTERNE}/org-test/devtools/browser/abc"
    assert "53111" not in json.dumps(reponse), "le port local du navigateur ne doit pas sortir du PC"


def test_un_mauvais_jeton_et_une_organisation_sans_pc_sont_refuses(relais):
    assert httpx.get(f"http://{relais}/cdp/mauvais/org-test/json/version", timeout=DELAI).status_code == 401
    assert _decouverte(relais).status_code == 503


def test_un_canal_cdp_relaie_les_messages_dans_les_deux_sens(relais, pc):
    echange = {}

    def agent():
        with connect(f"ws://{relais}/cdp/{JETON_INTERNE}/org-test/devtools/browser/abc", open_timeout=DELAI) as cdp:
            cdp.send('{"id":1,"method":"Browser.getVersion"}')
            echange["reponse"] = cdp.recv(DELAI)

    fil, res = _en_fond(agent)
    ouvrir = _recevoir(pc)
    assert ouvrir == {"t": "cdp_ouvrir", "canal": ouvrir["canal"], "chemin": "/devtools/browser/abc"}
    pc.send(json.dumps({"t": "cdp_ouvert", "canal": ouvrir["canal"]}))
    assert _recevoir(pc) == {"t": "cdp", "canal": ouvrir["canal"], "d": '{"id":1,"method":"Browser.getVersion"}'}
    pc.send(json.dumps({"t": "cdp", "canal": ouvrir["canal"], "d": '{"id":1,"result":{}}'}))
    fil.join(DELAI)
    assert "erreur" not in res, res.get("erreur")
    assert echange["reponse"] == '{"id":1,"result":{}}'
    # L'agent a fermé sa connexion : le Relais demande au PC de fermer le canal.
    assert _recevoir(pc) == {"t": "cdp_fermer", "canal": ouvrir["canal"]}


def test_un_navigateur_qui_ne_peut_pas_s_ouvrir_ferme_le_canal(relais, pc):
    def agent():
        with connect(f"ws://{relais}/cdp/{JETON_INTERNE}/org-test/devtools/browser/abc", open_timeout=DELAI) as cdp:
            cdp.recv(DELAI)

    fil, res = _en_fond(agent)
    ouvrir = _recevoir(pc)
    pc.send(json.dumps({"t": "cdp_erreur", "canal": ouvrir["canal"], "raison": "aucun navigateur trouvé"}))
    fil.join(DELAI)
    assert not fil.is_alive()
    assert "erreur" in res, "l'agent doit voir la connexion refusée"


def test_la_perte_du_pc_fait_echouer_la_decouverte_en_cours(relais):
    ws = _connecter_pc(relais)
    fil, res = _en_fond(lambda: _decouverte(relais))
    assert _recevoir(ws)["t"] == "cdp_http"
    ws.close()  # le PC part avant de répondre
    fil.join(DELAI)
    assert "erreur" not in res, res.get("erreur")
    assert res["valeur"].status_code == 503
