"""Relais des Mains Mbiling.

Fait le lien entre une cellule (le plugin ``mbiling_mains``, par HTTP interne) et les Mains
installées sur le PC du client (connexion WebSocket sortante depuis le PC).

Aucune valeur n'est écrite ici : tout vient du fichier désigné par ``MBILING_RELAIS_CONFIG``
(voir ``config.exemple.yaml``) ; les jetons sont lus dans les variables d'environnement
que ce fichier nomme.

Protocole avec les Mains (messages JSON, un par trame texte) :
  PC  -> Relais : {"t": "bonjour", "jeton": "...", "info": {...}}
  Relais -> PC  : {"t": "bienvenue", "appareil": "..."} | {"t": "refus", "raison": "..."}
  Relais -> PC  : {"t": "exec", "id", "script", "login", "timeout", "stdin"}
  PC  -> Relais : {"t": "sortie", "id", "d": "texte"}
  PC  -> Relais : {"t": "fin", "id", "code": 0}
  Relais -> PC  : {"t": "annuler", "id"}
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


def charger_config() -> dict[str, Any]:
    chemin = os.environ.get("MBILING_RELAIS_CONFIG")
    if not chemin:
        raise RuntimeError("MBILING_RELAIS_CONFIG doit désigner le fichier de configuration du Relais")
    texte = Path(chemin).read_text(encoding="utf-8")
    try:
        import yaml
        return yaml.safe_load(texte)
    except ModuleNotFoundError:  # dans l'environnement du moteur, c'est ruamel.yaml qui est installé
        from ruamel.yaml import YAML
        return YAML(typ="safe").load(texte)


def secret(nom_variable: str) -> str:
    valeur = os.environ.get(nom_variable, "")
    if len(valeur) < 24:
        raise RuntimeError(f"La variable {nom_variable} doit contenir un jeton d'au moins 24 caractères")
    return valeur


@dataclass
class Appareil:
    identifiant: str
    organisation: str
    socket: WebSocket
    info: dict[str, Any]
    files: dict[str, asyncio.Queue] = field(default_factory=dict)
    envoi: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def envoyer(self, message: dict[str, Any]) -> None:
        async with self.envoi:
            await self.socket.send_text(json.dumps(message, ensure_ascii=False))


class Demande(BaseModel):
    organisation: str
    script: str
    login: bool = False
    timeout: int = 120
    stdin: str | None = None


def creer_app() -> FastAPI:
    config = charger_config()
    limites = config["limites"]
    jeton_interne = secret(config["jeton_interne_env"])
    # jeton d'appareil -> (identifiant d'appareil, organisation)
    appareils_connus = {
        secret(a["jeton_env"]): (a["identifiant"], a["organisation"]) for a in config["appareils"]
    }
    connectes: dict[str, Appareil] = {}  # organisation -> appareil (un seul en phase 0)

    app = FastAPI(title="Relais des Mains Mbiling", docs_url=None, redoc_url=None)

    def verifier_interne(autorisation: str | None) -> None:
        attendu = f"Bearer {jeton_interne}"
        if not autorisation or not hmac.compare_digest(autorisation.encode(), attendu.encode()):
            raise HTTPException(status_code=401, detail="jeton interne invalide")

    @app.get("/sante")
    async def sante() -> dict[str, Any]:
        return {"ok": True, "appareils_connectes": len(connectes)}

    @app.websocket("/mains")
    async def mains(socket: WebSocket) -> None:
        await socket.accept()
        try:
            premier = json.loads(await asyncio.wait_for(socket.receive_text(), limites["delai_bonjour_s"]))
        except Exception:
            await socket.close(code=4400)
            return
        trouve = None
        for jeton, valeur in appareils_connus.items():
            if premier.get("t") == "bonjour" and hmac.compare_digest(str(premier.get("jeton", "")).encode(), jeton.encode()):
                trouve = valeur
        if not trouve:
            await socket.send_text(json.dumps({"t": "refus", "raison": "jeton d'appareil invalide"}))
            await socket.close(code=4401)
            return
        identifiant, organisation = trouve
        appareil = Appareil(identifiant, organisation, socket, premier.get("info") or {})
        ancien = connectes.get(organisation)
        connectes[organisation] = appareil
        if ancien:
            await _fermer(ancien, "remplacé par une nouvelle connexion")
        await appareil.envoyer({"t": "bienvenue", "appareil": identifiant})
        try:
            while True:
                message = json.loads(await socket.receive_text())
                file = appareil.files.get(str(message.get("id")))
                if file is not None and message.get("t") in ("sortie", "fin"):
                    await file.put(message)
        except (WebSocketDisconnect, RuntimeError, json.JSONDecodeError):
            pass
        finally:
            if connectes.get(organisation) is appareil:
                del connectes[organisation]
            await _fermer(appareil, "appareil déconnecté")

    async def _fermer(appareil: Appareil, raison: str) -> None:
        for file in list(appareil.files.values()):
            await file.put({"t": "perdu", "raison": raison})
        try:
            await appareil.socket.close()
        except Exception:
            pass

    @app.get("/interne/appareil/{organisation}")
    async def appareil_de(organisation: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        verifier_interne(authorization)
        appareil = connectes.get(organisation)
        if not appareil:
            raise HTTPException(status_code=503, detail="aucun appareil connecté pour cette organisation")
        return {"appareil": appareil.identifiant, "info": appareil.info}

    @app.post("/interne/exec")
    async def executer(demande: Demande, authorization: str | None = Header(default=None)) -> StreamingResponse:
        verifier_interne(authorization)
        appareil = connectes.get(demande.organisation)
        if not appareil:
            raise HTTPException(status_code=503, detail="aucun appareil connecté pour cette organisation")
        if len(demande.script) > limites["taille_script_max"]:
            raise HTTPException(status_code=413, detail="script trop volumineux")
        identifiant = uuid.uuid4().hex
        file: asyncio.Queue = asyncio.Queue(maxsize=limites["file_sortie_max"])
        appareil.files[identifiant] = file

        async def flux() -> AsyncIterator[bytes]:
            termine = False
            try:
                await appareil.envoyer({
                    "t": "exec", "id": identifiant, "script": demande.script, "login": demande.login,
                    "timeout": demande.timeout, "stdin": demande.stdin,
                })
                while True:
                    message = await file.get()
                    if message["t"] == "sortie":
                        yield (json.dumps({"d": message.get("d", "")}, ensure_ascii=False) + "\n").encode()
                    elif message["t"] == "fin":
                        termine = True
                        yield (json.dumps({"code": int(message.get("code", 1))}) + "\n").encode()
                        return
                    else:  # appareil perdu en cours de commande
                        termine = True
                        yield (json.dumps({"d": f"\n[Mains] {message.get('raison', 'connexion perdue')}\n"}, ensure_ascii=False) + "\n").encode()
                        yield (json.dumps({"code": 255, "perdu": True}) + "\n").encode()
                        return
            finally:
                appareil.files.pop(identifiant, None)
                if not termine:  # la cellule a coupé le flux : on annule sur le PC
                    try:
                        await appareil.envoyer({"t": "annuler", "id": identifiant})
                    except Exception:
                        pass

        return StreamingResponse(flux(), media_type="application/x-ndjson")

    return app
