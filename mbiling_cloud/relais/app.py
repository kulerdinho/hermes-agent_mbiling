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

Les PC sont inscrits automatiquement : quand un utilisateur connecté autorise l'agent à utiliser
son PC, la cellule demande au Relais un jeton d'appareil (POST /interne/appareils). Le Relais ne
garde que l'EMPREINTE (SHA-256) de ce jeton, dans le fichier ``fichier_appareils`` ; le jeton
lui-même n'est rendu qu'une fois, puis conservé chiffré sur le PC.

Navigateur du PC (tunnel CDP, lot 6) : la cellule règle ``browser.cdp_url`` sur
``http://<relais>/cdp/<jeton interne>/<organisation>``. Le Relais répond aux requêtes de
découverte ``/json/...`` (en remplaçant l'adresse locale du navigateur par la sienne) et relaie
chaque WebSocket ``/devtools/...`` vers les Mains, qui le passent au navigateur du PC :
  Relais -> PC : {"t": "cdp_http", "id", "methode", "chemin"}   PC -> Relais : {"t": "cdp_http_rep", "id", "statut", "corps"}
  Relais -> PC : {"t": "cdp_ouvrir", "canal", "chemin"}          PC -> Relais : {"t": "cdp_ouvert"|"cdp_erreur", "canal", ...}
  dans les deux sens : {"t": "cdp", "canal", "d"} puis {"t": "cdp_fermer", "canal"}
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable

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
    # Tunnel du navigateur : réponses HTTP attendues (par id) et canaux WebSocket ouverts (par canal).
    http_attente: dict[str, asyncio.Future] = field(default_factory=dict)
    canaux: dict[str, asyncio.Queue] = field(default_factory=dict)

    async def envoyer(self, message: dict[str, Any]) -> None:
        async with self.envoi:
            await self.socket.send_text(json.dumps(message, ensure_ascii=False))


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

    def inscrire(self, organisation: str, nom: str, utilisateur: str = "", session: str = "") -> tuple[str, str]:
        """``utilisateur`` (sub) et ``session`` (sid) : le compte qui a inscrit le PC et sa session de
        connexion, pour la page « Mes appareils » et la révocation."""
        jeton = secrets.token_urlsafe(32)
        identifiant = f"pc-{uuid.uuid4().hex[:12]}"
        with self.verrou:
            self.fiches[empreinte(jeton)] = {
                "identifiant": identifiant, "organisation": organisation, "nom": nom[:120],
                "utilisateur": utilisateur, "session": session,
                "cree": int(time.time()), "revoque": False,
            }
            self._sauver()
        return identifiant, jeton

    def vu(self, identifiant: str, systeme: str) -> None:
        """Note la dernière connexion des Mains de ce PC, et son système."""
        with self.verrou:
            for fiche in self.fiches.values():
                if fiche["identifiant"] == identifiant:
                    fiche["derniere_connexion"] = int(time.time())
                    if systeme:
                        fiche["systeme"] = systeme[:120]
            self._sauver()

    def lister(self, utilisateur: str) -> list[dict[str, Any]]:
        """PC non révoqués inscrits par ce compte (jamais l'empreinte du jeton)."""
        return [dict(f) for f in self.fiches.values()
                if utilisateur and f.get("utilisateur") == utilisateur and not f.get("revoque")]

    def trouver(self, jeton: str) -> tuple[str, str] | None:
        fiche = self.fiches.get(empreinte(jeton))
        if not fiche or fiche.get("revoque"):
            return None
        return fiche["identifiant"], fiche["organisation"]

    def revoquer(self, identifiant: str) -> bool:
        return bool(self.revoquer_si(lambda f: f["identifiant"] == identifiant))

    def revoquer_si(self, critere: Callable[[dict[str, Any]], bool]) -> list[str]:
        """Révoque les PC qui répondent au critère ; rend leurs identifiants."""
        with self.verrou:
            revoques = []
            for fiche in self.fiches.values():
                if not fiche.get("revoque") and critere(fiche):
                    fiche["revoque"] = True
                    revoques.append(fiche["identifiant"])
            if revoques:
                self._sauver()
        return revoques


class Demande(BaseModel):
    organisation: str
    script: str
    login: bool = False
    timeout: int = 120
    stdin: str | None = None


class Inscription(BaseModel):
    organisation: str
    nom: str = ""
    utilisateur: str = ""
    session: str = ""


class Revocation(BaseModel):
    """Révoque les PC de ``utilisateur`` : tous, ceux d'une session, ou un seul."""
    utilisateur: str
    session: str = ""
    identifiant: str = ""


def creer_app() -> FastAPI:
    config = charger_config()
    limites = config["limites"]
    jeton_interne = secret(config["jeton_interne_env"])
    # Appareils déclarés à la main dans la configuration (essais) : empreinte -> (identifiant, organisation).
    appareils_fixes = {
        empreinte(secret(a["jeton_env"])): (a["identifiant"], a["organisation"]) for a in config.get("appareils") or []
    }
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
        registre.vu(identifiant, str(appareil.info.get("systeme") or ""))
        ancien = connectes.get(organisation)
        connectes[organisation] = appareil
        if ancien:
            await _fermer(ancien, "remplacé par une nouvelle connexion")
        await appareil.envoyer({"t": "bienvenue", "appareil": identifiant})
        try:
            while True:
                message = json.loads(await socket.receive_text())
                genre = message.get("t")
                if genre in ("sortie", "fin"):
                    file = appareil.files.get(str(message.get("id")))
                    if file is not None:
                        await file.put(message)
                elif genre == "cdp_http_rep":
                    attente = appareil.http_attente.pop(str(message.get("id")), None)
                    if attente is not None and not attente.done():
                        attente.set_result(message)
                elif genre in ("cdp_ouvert", "cdp_erreur", "cdp", "cdp_fermer"):
                    canal = appareil.canaux.get(str(message.get("canal")))
                    if canal is not None:
                        await canal.put(message)
        except (WebSocketDisconnect, RuntimeError, json.JSONDecodeError):
            pass
        finally:
            if connectes.get(organisation) is appareil:
                del connectes[organisation]
            await _fermer(appareil, "appareil déconnecté")

    async def _fermer(appareil: Appareil, raison: str) -> None:
        for file in list(appareil.files.values()):
            await file.put({"t": "perdu", "raison": raison})
        for attente in list(appareil.http_attente.values()):
            if not attente.done():
                attente.set_result({"statut": 503, "corps": json.dumps({"erreur": raison})})
        for canal in list(appareil.canaux.values()):
            await canal.put({"t": "cdp_fermer", "raison": raison})
        try:
            await appareil.socket.close()
        except Exception:
            pass

    # --- Navigateur du PC (tunnel CDP) -------------------------------------------------------

    def verifier_cdp(jeton: str) -> None:
        if not hmac.compare_digest(jeton.encode(), jeton_interne.encode()):
            raise HTTPException(status_code=401, detail="jeton invalide")

    delai_cdp = float(limites.get("delai_navigateur_s", 30))
    adresse_locale = re.compile(r"(ws|http)://(127\.0\.0\.1|localhost|\[::1\]):\d+")

    @app.api_route("/cdp/{jeton}/{organisation}/json{reste:path}", methods=["GET", "PUT"])
    async def cdp_http(jeton: str, organisation: str, reste: str, request: Request) -> Response:
        """Découverte CDP (/json/version, /json/list...) répondue par le navigateur du PC."""
        verifier_cdp(jeton)
        appareil = connectes.get(organisation)
        if not appareil:
            raise HTTPException(status_code=503, detail="aucun appareil connecté pour cette organisation")
        identifiant = uuid.uuid4().hex
        attente = asyncio.get_running_loop().create_future()
        appareil.http_attente[identifiant] = attente
        chemin = "/json" + reste + (f"?{request.url.query}" if request.url.query else "")
        await appareil.envoyer({"t": "cdp_http", "id": identifiant, "methode": request.method, "chemin": chemin})
        try:
            reponse = await asyncio.wait_for(attente, delai_cdp)
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="le navigateur du PC ne répond pas") from exc
        finally:
            appareil.http_attente.pop(identifiant, None)
        # L'agent doit joindre le navigateur À TRAVERS le Relais : on remplace son adresse locale.
        schema_ws = "wss" if request.url.scheme == "https" else "ws"
        base = f"{request.url.netloc}/cdp/{jeton}/{organisation}"
        corps = adresse_locale.sub(
            lambda m: f"{schema_ws if m.group(1) == 'ws' else request.url.scheme}://{base}",
            str(reponse.get("corps", "")))
        return Response(content=corps, status_code=int(reponse.get("statut", 502)), media_type="application/json")

    @app.websocket("/cdp/{jeton}/{organisation}/devtools/{reste:path}")
    async def cdp_websocket(socket: WebSocket, jeton: str, organisation: str, reste: str) -> None:
        """Une connexion CDP de l'agent, relayée jusqu'au navigateur du PC."""
        if not hmac.compare_digest(jeton.encode(), jeton_interne.encode()):
            await socket.close(code=4401)
            return
        appareil = connectes.get(organisation)
        if not appareil:
            await socket.close(code=4503)
            return
        canal = uuid.uuid4().hex
        file: asyncio.Queue = asyncio.Queue(maxsize=limites["file_sortie_max"])
        appareil.canaux[canal] = file
        try:
            await appareil.envoyer({"t": "cdp_ouvrir", "canal": canal, "chemin": f"/devtools/{reste}"})
            try:
                premier = await asyncio.wait_for(file.get(), delai_cdp)
            except TimeoutError:
                await socket.close(code=4504)
                return
            if premier.get("t") != "cdp_ouvert":
                await socket.close(code=4502, reason=str(premier.get("raison", ""))[:120])
                return
            await socket.accept()

            async def vers_pc() -> None:
                try:
                    while True:
                        texte = await socket.receive_text()
                        await appareil.envoyer({"t": "cdp", "canal": canal, "d": texte})
                except (WebSocketDisconnect, RuntimeError):
                    return  # l'agent a fermé sa connexion : fin normale du canal

            async def vers_agent() -> None:
                try:
                    while True:
                        message = await file.get()
                        if message.get("t") == "cdp":
                            await socket.send_text(str(message.get("d", "")))
                        elif message.get("t") == "cdp_fermer":
                            return
                except (WebSocketDisconnect, RuntimeError):
                    return

            taches = [asyncio.create_task(vers_pc()), asyncio.create_task(vers_agent())]
            _fini, en_cours = await asyncio.wait(taches, return_when=asyncio.FIRST_COMPLETED)
            for tache in en_cours:
                tache.cancel()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            appareil.canaux.pop(canal, None)
            try:
                await appareil.envoyer({"t": "cdp_fermer", "canal": canal})
            except Exception:
                pass
            try:
                await socket.close()
            except Exception:
                pass

    @app.post("/interne/appareils")
    async def inscrire(demande: Inscription, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Inscrit un PC pour une organisation. Le jeton n'est rendu qu'ici, une seule fois."""
        verifier_interne(authorization)
        if not demande.organisation.strip():
            raise HTTPException(status_code=400, detail="organisation requise")
        identifiant, jeton = registre.inscrire(demande.organisation.strip(), demande.nom.strip(),
                                               demande.utilisateur.strip(), demande.session.strip())
        return {"appareil": identifiant, "jeton": jeton}

    async def _deconnecter(identifiants: list[str], raison: str) -> None:
        for organisation, appareil in list(connectes.items()):
            if appareil.identifiant in identifiants:
                del connectes[organisation]
                await _fermer(appareil, raison)

    @app.get("/interne/appareils")
    async def lister(utilisateur: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """PC d'un compte, avec leur état de connexion (page « Mes appareils »)."""
        verifier_interne(authorization)
        en_ligne = {a.identifiant for a in connectes.values()}
        return {"appareils": [{
            "identifiant": f["identifiant"], "nom": f.get("nom", ""), "systeme": f.get("systeme", ""),
            "session": f.get("session", ""), "cree": f.get("cree"),
            "derniere_connexion": f.get("derniere_connexion"), "connecte": f["identifiant"] in en_ligne,
        } for f in registre.lister(utilisateur.strip())]}

    @app.post("/interne/appareils/revoquer")
    async def revoquer_pour(demande: Revocation, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Révoque des PC d'un compte, jamais ceux d'un autre : jetons refusés, Mains coupées."""
        verifier_interne(authorization)
        utilisateur = demande.utilisateur.strip()
        if not utilisateur:
            raise HTTPException(status_code=400, detail="utilisateur requis")
        revoques = registre.revoquer_si(lambda f: f.get("utilisateur") == utilisateur
                                        and (not demande.session or f.get("session") == demande.session)
                                        and (not demande.identifiant or f["identifiant"] == demande.identifiant))
        await _deconnecter(revoques, "appareil révoqué")
        return {"revoques": revoques}

    @app.delete("/interne/appareils/{identifiant}")
    async def revoquer(identifiant: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Révoque un PC : son jeton est refusé, et sa connexion en cours est fermée."""
        verifier_interne(authorization)
        if not registre.revoquer(identifiant):
            raise HTTPException(status_code=404, detail="appareil inconnu")
        await _deconnecter([identifiant], "appareil révoqué")
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

    return app
