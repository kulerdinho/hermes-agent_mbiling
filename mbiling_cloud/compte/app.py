"""Mbiling Compte : l'espace de chaque utilisateur (phase 1, lot 1, C6 « Mes appareils »).

Servi par la passerelle sous ``/compte``. Tout compte du serveur de comptes y entre (aucun rôle
requis) et n'y voit que ce qui le concerne :

- **Mes appareils** : ses connexions ouvertes (sessions du serveur de comptes), avec, pour chacune,
  le PC dont les Mains ont été inscrites pendant cette session (nom, système, dernière connexion),
  et les PC encore inscrits dont la session est terminée. Révoquer ferme la session (le serveur de
  comptes prévient aussitôt la cellule, qui refuse ses jetons) et révoque ses PC auprès du Relais
  (Mains coupées, jeton d'appareil refusé). « Déconnecter tous mes appareils » fait de même pour tout.

Configuration (variables d'environnement, posées par ``serveur/compose.yaml``) :
  MBILING_COMPTE_URL               adresse publique, ex. https://domaine/compte
  MBILING_COMPTE_EMETTEUR          émetteur public des jetons, ex. https://domaine/comptes/realms/mbiling
  MBILING_COMPTE_COMPTES_INTERNE   serveur de comptes sur le réseau interne, ex. http://comptes:8080/comptes
  MBILING_COMPTE_ROYAUME           ex. mbiling
  MBILING_COMPTE_CLIENT_SECRET     secret du client « compte » (connexion + compte de service)
  MBILING_COMPTE_CLE               clé (≥ 32 caractères) qui chiffre les cookies de session
  MBILING_RELAIS_URL               Relais des Mains sur le réseau interne, ex. http://relais:8000
  MBILING_RELAIS_JETON             jeton interne du Relais
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from mbiling_cloud.management.keycloak import ErreurComptes, Keycloak

ICI = Path(__file__).parent
P = "/compte"
COOKIE_SESSION = "mbiling_compte"
COOKIE_ETAT = "mbiling_compte_etat"
DUREE_SESSION = 8 * 3600
# Les applications Mbiling, telles que l'utilisateur les connaît (identifiants des clients du royaume).
APPLICATIONS = {"cellule": "Mbiling (Desktop ou navigateur)", "compte": "Mon compte", "management": "Management"}

journal = logging.getLogger("mbiling.compte")


@dataclass(frozen=True)
class Config:
    url_publique: str
    emetteur: str
    comptes_interne: str
    royaume: str
    client_secret: str
    cle: str
    relais_url: str
    relais_jeton: str
    client_id: str = "compte"

    @classmethod
    def depuis_env(cls) -> "Config":
        def lire(nom: str) -> str:
            valeur = os.environ.get(nom, "").strip()
            if not valeur:
                raise RuntimeError(f"{nom} manquant (voir serveur/compose.yaml)")
            return valeur
        return cls(url_publique=lire("MBILING_COMPTE_URL").rstrip("/"),
                   emetteur=lire("MBILING_COMPTE_EMETTEUR").rstrip("/"),
                   comptes_interne=lire("MBILING_COMPTE_COMPTES_INTERNE"),
                   royaume=lire("MBILING_COMPTE_ROYAUME"),
                   client_secret=lire("MBILING_COMPTE_CLIENT_SECRET"),
                   cle=lire("MBILING_COMPTE_CLE"),
                   relais_url=lire("MBILING_RELAIS_URL").rstrip("/"),
                   relais_jeton=lire("MBILING_RELAIS_JETON"))


@dataclass
class Session:
    sub: str
    email: str
    nom: str
    sid: str
    csrf: str
    id_token: str


class Relais:
    """Routes internes du Relais des Mains : PC d'un compte, et leur révocation."""

    def __init__(self, url: str, jeton: str, transport: httpx.BaseTransport | None) -> None:
        self._url = url
        self._http = httpx.Client(timeout=10, transport=transport, headers={"Authorization": f"Bearer {jeton}"})

    def appareils(self, utilisateur: str) -> list[dict[str, Any]]:
        rep = self._http.get(f"{self._url}/interne/appareils", params={"utilisateur": utilisateur})
        rep.raise_for_status()
        return rep.json()["appareils"]

    def revoquer(self, utilisateur: str, *, session: str = "", identifiant: str = "") -> list[str]:
        rep = self._http.post(f"{self._url}/interne/appareils/revoquer",
                              json={"utilisateur": utilisateur, "session": session, "identifiant": identifiant})
        rep.raise_for_status()
        return rep.json()["revoques"]


def _fernet(cle: str) -> Fernet:
    if len(cle) < 32:
        raise RuntimeError("MBILING_COMPTE_CLE doit faire au moins 32 caractères")
    derivee = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"mbiling-compte/sessions").derive(cle.encode())
    return Fernet(base64.urlsafe_b64encode(derivee))


def _ip(request: Request) -> str | None:
    return (request.headers.get("cf-connecting-ip")
            or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
            or (request.client.host if request.client else None))


def lignes_appareils(sessions: list[dict[str, Any]], appareils: list[dict[str, Any]], sid_courant: str) -> list[dict[str, Any]]:
    """Une ligne par connexion ouverte (avec les PC inscrits pendant celle-ci), puis une ligne par PC
    encore inscrit dont la session est terminée. Les plus récentes d'abord."""
    par_session: dict[str, list[dict[str, Any]]] = {}
    for a in appareils:
        par_session.setdefault(a.get("session") or "", []).append(a)
    lignes = []
    for s in sessions:
        pcs = par_session.pop(s["id"], [])
        lignes.append({
            "session": s["id"], "pcs": pcs, "courante": s["id"] == sid_courant,
            "applications": sorted({APPLICATIONS.get(c, c) for c in (s.get("clients") or {}).values()}),
            "ip": s.get("ipAddress") or "", "debut": (s.get("start") or 0) / 1000,
            "activite": (s.get("lastAccess") or 0) / 1000})
    for pcs in par_session.values():
        for a in pcs:
            lignes.append({"session": "", "pcs": [a], "courante": False, "applications": [], "ip": "",
                           "debut": a.get("cree") or 0, "activite": a.get("derniere_connexion") or a.get("cree") or 0})
    return sorted(lignes, key=lambda l: (not l["courante"], -l["activite"]))


def creer_app(config: Config | None = None, *, transport: httpx.BaseTransport | None = None,
              transport_relais: httpx.BaseTransport | None = None) -> FastAPI:
    config = config or Config.depuis_env()
    jetons = _fernet(config.cle)
    comptes = Keycloak(interne=config.comptes_interne, royaume=config.royaume, emetteur=config.emetteur,
                       client_id=config.client_id, client_secret=config.client_secret,
                       api_client_id=config.client_id, api_client_secret=config.client_secret, transport=transport)
    relais = Relais(config.relais_url, config.relais_jeton, transport_relais)
    gabarits = Environment(loader=FileSystemLoader(ICI / "gabarits"), autoescape=select_autoescape(["html"]))
    gabarits.filters["date"] = lambda t: time.strftime("%d/%m/%Y %H:%M", time.gmtime(t)) + " UTC" if t else "—"
    retour = f"{config.url_publique}/retour"

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def entetes_de_securite(request: Request, suivant):
        reponse = await suivant(request)
        reponse.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'self'; img-src 'self' data:; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'")
        reponse.headers["X-Content-Type-Options"] = "nosniff"
        reponse.headers["Referrer-Policy"] = "no-referrer"
        # no-transform : Cloudflare ne réécrit pas les pages (adresses email masquées par un script).
        reponse.headers["Cache-Control"] = "no-store, no-transform"
        return reponse

    def page(nom: str, session: Session | None, statut: int = 200, **contexte: Any) -> HTMLResponse:
        contexte.setdefault("message", None)
        return HTMLResponse(gabarits.get_template(nom).render(session=session, P=P, **contexte), status_code=statut)

    def session_de(request: Request) -> Session | None:
        brut = request.cookies.get(COOKIE_SESSION)
        if not brut:
            return None
        try:
            return Session(**json.loads(jetons.decrypt(brut.encode(), ttl=DUREE_SESSION)))
        except (InvalidToken, ValueError, TypeError):
            return None

    def vers_connexion() -> Response:
        reponse = RedirectResponse(f"{P}/connexion", status_code=303)
        reponse.delete_cookie(COOKIE_SESSION, path=P)
        return reponse

    async def action(request: Request) -> tuple[Session | None, Any, Response | None]:
        session = session_de(request)
        if session is None:
            return None, None, vers_connexion()
        formulaire = await request.form()
        if not secrets.compare_digest(str(formulaire.get("csrf", "")), session.csrf):
            return session, formulaire, page("message.html", session, 403, titre="Action refusée",
                                             texte="Jeton de formulaire invalide. Rechargez la page.")
        return session, formulaire, None

    # --- Connexion -----------------------------------------------------------------------------

    @app.get(f"{P}/sante")
    def sante() -> Response:
        return Response("ok", media_type="text/plain")

    @app.get(f"{P}/style.css")
    def style() -> Response:
        # Même identité visuelle que Management.
        return FileResponse(ICI.parent / "management" / "gabarits" / "style.css", media_type="text/css")

    @app.get(f"{P}/connexion")
    def connexion() -> Response:
        etat, nonce, verificateur = (secrets.token_urlsafe(32) for _ in range(3))
        defi = base64.urlsafe_b64encode(hashlib.sha256(verificateur.encode()).digest()).rstrip(b"=").decode()
        cible = comptes.page_connexion + "?" + urlencode({
            "client_id": config.client_id, "response_type": "code", "scope": "openid email profile",
            "redirect_uri": retour, "state": etat, "nonce": nonce,
            "code_challenge": defi, "code_challenge_method": "S256"})
        reponse = RedirectResponse(cible, status_code=303)
        reponse.set_cookie(COOKIE_ETAT, jetons.encrypt(json.dumps([etat, nonce, verificateur]).encode()).decode(),
                           max_age=600, path=P, secure=True, httponly=True, samesite="lax")
        return reponse

    @app.get(f"{P}/retour")
    def retour_connexion(request: Request, code: str = "", state: str = "") -> Response:
        try:
            etat, nonce, verificateur = json.loads(jetons.decrypt(request.cookies.get(COOKIE_ETAT, "").encode(), ttl=600))
        except (InvalidToken, ValueError):
            return page("message.html", None, 400, titre="Connexion expirée", texte="Recommencez la connexion.",
                        lien=f"{P}/connexion")
        if not code or not secrets.compare_digest(state, etat):
            return page("message.html", None, 400, titre="Connexion refusée", texte="Réponse de connexion invalide.",
                        lien=f"{P}/connexion")
        try:
            reponse_jetons = comptes.echanger_code(code, retour, verificateur)
            identite = comptes.verifier(reponse_jetons["id_token"], audience=config.client_id)
            if identite.get("nonce") != nonce:
                raise ErreurComptes("jeton émis pour une autre connexion")
        except (ErreurComptes, KeyError, httpx.HTTPError) as erreur:
            journal.warning("connexion refusée : %s", erreur)
            return page("message.html", None, 502, titre="Connexion impossible",
                        texte="Le serveur de comptes n'a pas confirmé la connexion.", lien=f"{P}/connexion")
        session = {"sub": identite["sub"], "email": identite.get("email", ""),
                   "nom": identite.get("name") or identite.get("email", ""), "sid": identite.get("sid", ""),
                   "csrf": secrets.token_urlsafe(32), "id_token": reponse_jetons["id_token"]}
        reponse = RedirectResponse(f"{P}/appareils", status_code=303)
        reponse.set_cookie(COOKIE_SESSION, jetons.encrypt(json.dumps(session).encode()).decode(),
                           max_age=DUREE_SESSION, path=P, secure=True, httponly=True, samesite="lax")
        reponse.delete_cookie(COOKIE_ETAT, path=P)
        return reponse

    @app.post(f"{P}/deconnexion")
    async def deconnexion(request: Request) -> Response:
        session, _, refus = await action(request)
        if refus is not None:
            return refus
        cible = comptes.page_deconnexion + "?" + urlencode({
            "client_id": config.client_id, "id_token_hint": session.id_token,
            "post_logout_redirect_uri": f"{config.url_publique}/"})
        reponse = RedirectResponse(cible, status_code=303)
        reponse.delete_cookie(COOKIE_SESSION, path=P)
        return reponse

    # --- Mes appareils -------------------------------------------------------------------------

    @app.get(P)
    @app.get(f"{P}/")
    def accueil() -> Response:
        return RedirectResponse(f"{P}/appareils", status_code=303)

    def afficher_appareils(session: Session, statut: int = 200, **contexte: Any) -> Response:
        try:
            ouvertes = comptes.sessions(session.sub)
            appareils = relais.appareils(session.sub)
        except (ErreurComptes, httpx.HTTPError) as erreur:
            journal.error("liste des appareils de %s impossible : %s", session.sub, erreur)
            return page("message.html", session, 502, titre="Appareils indisponibles",
                        texte="La liste de vos appareils est momentanément indisponible. Réessayez dans un instant.")
        if not any(s["id"] == session.sid for s in ouvertes):
            # Cette session a été fermée (depuis un autre appareil, ou par l'équipe Mbiling).
            return vers_connexion()
        return page("appareils.html", session, statut, lignes=lignes_appareils(ouvertes, appareils, session.sid), **contexte)

    @app.get(f"{P}/appareils")
    def appareils(request: Request) -> Response:
        session = session_de(request)
        if session is None:
            return vers_connexion()
        return afficher_appareils(session)

    @app.post(f"{P}/appareils/revoquer")
    async def revoquer(request: Request) -> Response:
        session, formulaire, refus = await action(request)
        if refus is not None:
            return refus
        cible_session = str(formulaire.get("session", ""))
        cible_pc = str(formulaire.get("appareil", ""))
        try:
            if cible_session:
                # Uniquement une session de CE compte : l'identifiant vient d'un formulaire.
                if not any(s["id"] == cible_session for s in comptes.sessions(session.sub)):
                    return afficher_appareils(session, 404, erreurs=["Cette connexion n'existe plus."])
                comptes.fermer_session(cible_session)
                revoques = relais.revoquer(session.sub, session=cible_session)
            elif cible_pc:
                revoques = relais.revoquer(session.sub, identifiant=cible_pc)
            else:
                return afficher_appareils(session, 400, erreurs=["Aucun appareil choisi."])
        except (ErreurComptes, httpx.HTTPError) as erreur:
            journal.error("révocation pour %s impossible : %s", session.sub, erreur)
            return afficher_appareils(session, 502, erreurs=["La révocation n'a pas abouti. Réessayez."])
        journal.info("révocation par %s (%s) : session=%s pc=%s", session.sub, _ip(request), cible_session, revoques)
        if cible_session and cible_session == session.sid:
            return vers_connexion()
        return afficher_appareils(session, message="Appareil révoqué : il revient à la page de connexion.")

    @app.post(f"{P}/appareils/tout")
    async def tout_deconnecter(request: Request) -> Response:
        session, _, refus = await action(request)
        if refus is not None:
            return refus
        try:
            comptes.deconnecter_partout(session.sub)
            revoques = relais.revoquer(session.sub)
        except (ErreurComptes, httpx.HTTPError) as erreur:
            journal.error("déconnexion complète de %s impossible : %s", session.sub, erreur)
            return afficher_appareils(session, 502, erreurs=["La déconnexion n'a pas abouti. Réessayez."])
        journal.info("tous les appareils de %s déconnectés (%s) : pc=%s", session.sub, _ip(request), revoques)
        return vers_connexion()

    return app
