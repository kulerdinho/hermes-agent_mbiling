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
  Navigateur (lot 6) : messages « cdp* », décrits dans mbiling_mains_client/navigateur.cjs.

Tunnel du navigateur : la cellule utilise ``http://relais:<port>/cdp/<organisation>/<jeton CDP>``
comme adresse CDP. Le Relais transmet ``/json/...`` (HTTP) et ``/devtools/...`` (WebSocket) au
navigateur lancé par les Mains sur le PC de l'organisation, et réécrit les adresses
``ws://127.0.0.1:<port>/`` des réponses vers lui-même. Ces routes ne sont pas publiées par la
passerelle : seule la cellule les joint, par le réseau interne.

Les PC sont inscrits automatiquement : quand un utilisateur connecté autorise l'agent à utiliser
son PC, la cellule demande au Relais un jeton d'appareil (POST /interne/appareils). Le Relais ne
garde que l'EMPREINTE (SHA-256) de ce jeton, dans le fichier ``fichier_appareils`` ; le jeton
lui-même n'est rendu qu'une fois, puis conservé chiffré sur le PC.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
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


# Messages du PC rattachés à une demande en cours (commande ou canal du navigateur).
MESSAGES_DU_PC = frozenset({"sortie", "fin", "cdp_http_reponse", "cdp_erreur", "cdp_ouvert", "cdp", "cdp_ferme"})


def empreinte(jeton: str) -> str:
    return hashlib.sha256(jeton.encode("utf-8")).hexdigest()


class RegistreAppareils:
    """PC inscrits : empreinte du jeton -> fiche. Persisté dans un fichier JSON (écriture atomique)."""

    def __init__(self, chemin: Path | None) -> None:
        self.chemin = chemin
        self.verrou = threading.Lock()
        self.fiches: dict[str, dict[str, Any]] = {}
        if chemin and chemin.exists():
            self.fiches = json.loads(chemin.read_text(encoding="utf-8") or "{}")

    def _sauver(self) -> None:
        if not self.chemin:
            return
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        provisoire = self.chemin.with_suffix(self.chemin.suffix + ".tmp")
        provisoire.write_text(json.dumps(self.fiches, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(provisoire, self.chemin)

    def inscrire(self, organisation: str, nom: str) -> tuple[str, str]:
        jeton = secrets.token_urlsafe(32)
        identifiant = f"pc-{uuid.uuid4().hex[:12]}"
        with self.verrou:
            self.fiches[empreinte(jeton)] = {
                "identifiant": identifiant, "organisation": organisation, "nom": nom[:120],
                "cree": int(time.time()), "revoque": False,
            }
            self._sauver()
        return identifiant, jeton

    def trouver(self, jeton: str) -> tuple[str, str] | None:
        fiche = self.fiches.get(empreinte(jeton))
        if not fiche or fiche.get("revoque"):
            return None
        return fiche["identifiant"], fiche["organisation"]

    def revoquer(self, identifiant: str) -> bool:
        with self.verrou:
            trouve = False
            for fiche in self.fiches.values():
                if fiche["identifiant"] == identifiant and not fiche.get("revoque"):
                    fiche["revoque"] = True
                    trouve = True
            if trouve:
                self._sauver()
        return trouve


class Demande(BaseModel):
    organisation: str
    script: str
    login: bool = False
    timeout: int = 120
    stdin: str | None = None


class Inscription(BaseModel):
    organisation: str
    nom: str = ""


def creer_app() -> FastAPI:
    config = charger_config()
    limites = config["limites"]
    jeton_interne = secret(config["jeton_interne_env"])
    # Appareils déclarés à la main dans la configuration (essais) : empreinte -> (identifiant, organisation).
    appareils_fixes = {
        empreinte(secret(a["jeton_env"])): (a["identifiant"], a["organisation"]) for a in config.get("appareils") or []
    }
    # Tunnel du navigateur : désactivé tant que la configuration ne nomme pas son jeton.
    jeton_cdp = secret(config["jeton_cdp_env"]) if config.get("jeton_cdp_env") else None
    fichier = config.get("fichier_appareils")
    registre = RegistreAppareils(Path(fichier) if fichier else None)
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
        if premier.get("t") == "bonjour":
            jeton = str(premier.get("jeton", ""))
            # Recherche par empreinte : le Relais ne connaît jamais les jetons en clair.
            trouve = appareils_fixes.get(empreinte(jeton)) or registre.trouver(jeton)
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
                if file is not None and message.get("t") in MESSAGES_DU_PC:
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

    @app.post("/interne/appareils")
    async def inscrire(demande: Inscription, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Inscrit un PC pour une organisation. Le jeton n'est rendu qu'ici, une seule fois."""
        verifier_interne(authorization)
        if not demande.organisation.strip():
            raise HTTPException(status_code=400, detail="organisation requise")
        identifiant, jeton = registre.inscrire(demande.organisation.strip(), demande.nom.strip())
        return {"appareil": identifiant, "jeton": jeton}

    @app.delete("/interne/appareils/{identifiant}")
    async def revoquer(identifiant: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Révoque un PC : son jeton est refusé, et sa connexion en cours est fermée."""
        verifier_interne(authorization)
        if not registre.revoquer(identifiant):
            raise HTTPException(status_code=404, detail="appareil inconnu")
        for organisation, appareil in list(connectes.items()):
            if appareil.identifiant == identifiant:
                del connectes[organisation]
                await _fermer(appareil, "appareil révoqué")
        return {"revoque": identifiant}

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

    def cdp_autorise(jeton: str) -> bool:
        return jeton_cdp is not None and hmac.compare_digest(jeton.encode(), jeton_cdp.encode())

    @app.get("/cdp/{organisation}/{jeton}/json{suite:path}")
    async def cdp_http(organisation: str, jeton: str, suite: str, request: Request) -> Response:
        """Découverte CDP (``/json/version``, ``/json/list``…) du navigateur du PC."""
        if not cdp_autorise(jeton):
            raise HTTPException(status_code=401, detail="jeton CDP invalide")
        appareil = connectes.get(organisation)
        if not appareil:
            raise HTTPException(status_code=503, detail="aucun appareil connecté pour cette organisation")
        identifiant = uuid.uuid4().hex
        file: asyncio.Queue = asyncio.Queue()
        appareil.files[identifiant] = file
        try:
            await appareil.envoyer({"t": "cdp_http", "id": identifiant, "chemin": f"/json{suite}"})
            message = await asyncio.wait_for(file.get(), limites["delai_cdp_s"])
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="le navigateur du PC ne répond pas") from None
        finally:
            appareil.files.pop(identifiant, None)
        if message["t"] != "cdp_http_reponse":
            raise HTTPException(status_code=502, detail=str(message.get("raison", "navigateur indisponible")))
        # Les adresses WebSocket du navigateur désignent le PC : on les fait passer par le Relais.
        vers_relais = f"ws://{request.headers['host']}/cdp/{organisation}/{jeton}/"
        corps = str(message.get("corps", ""))
        for hote in ("127.0.0.1", "localhost"):
            corps = corps.replace(f"ws://{hote}:{message.get('port')}/", vers_relais)
        return Response(corps, status_code=int(message.get("statut", 200)), media_type="application/json")

    @app.websocket("/cdp/{organisation}/{jeton}/devtools/{suite:path}")
    async def cdp_canal(socket: WebSocket, organisation: str, jeton: str, suite: str) -> None:
        """Canal CDP : chaque trame passe telle quelle entre la cellule et le navigateur du PC."""
        appareil = connectes.get(organisation)
        if not cdp_autorise(jeton) or not appareil:
            await socket.close(code=4401 if not cdp_autorise(jeton) else 4503)
            return
        await socket.accept()
        identifiant = uuid.uuid4().hex
        file: asyncio.Queue = asyncio.Queue()
        appareil.files[identifiant] = file

        async def vers_cellule() -> None:
            while True:
                message = await file.get()
                if message["t"] == "cdp":
                    await socket.send_text(message.get("d", ""))
                elif message["t"] != "cdp_ouvert":  # fermé, erreur ou PC perdu
                    break
            await socket.close()

        tache = asyncio.create_task(vers_cellule())
        try:
            await appareil.envoyer({"t": "cdp_ouvrir", "id": identifiant, "chemin": f"/devtools/{suite}"})
            while True:
                await appareil.envoyer({"t": "cdp", "id": identifiant, "d": await socket.receive_text()})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            tache.cancel()
            appareil.files.pop(identifiant, None)
            try:
                await appareil.envoyer({"t": "cdp_fermer", "id": identifiant})
            except Exception:
                pass

    return app
