"""Management Mbiling : l'espace d'administration du Cloud (phase 1, lot 1).

Servi par la passerelle sous ``/management``. Seuls les comptes du serveur de comptes qui ont un rôle
``management-*`` (voir ``droits.json``) et une double authentification configurée y entrent ; le
serveur de comptes impose en plus l'OTP à chaque connexion à ce client (flux « navigateur-management »).

Premier lot : réglages d'envoi des emails (SMTP, mot de passe chiffré, jamais réaffiché, email de
test), poussés vers le serveur de comptes, et journal d'audit inaltérable.

Configuration (variables d'environnement, posées par ``serveur/compose.yaml``) :
  MBILING_MANAGEMENT_URL               adresse publique, ex. https://domaine/management
  MBILING_MANAGEMENT_EMETTEUR          émetteur public des jetons, ex. https://domaine/comptes/realms/mbiling
  MBILING_MANAGEMENT_COMPTES_INTERNE   serveur de comptes sur le réseau interne, ex. http://comptes:8080/comptes
  MBILING_MANAGEMENT_ROYAUME           ex. mbiling
  MBILING_MANAGEMENT_CLIENT_SECRET     secret du client « management »
  MBILING_MANAGEMENT_API_SECRET        secret du compte de service « management-api »
  MBILING_MANAGEMENT_CLE               clé maîtresse (≥ 32 caractères) : chiffre secrets et sessions
  MBILING_MANAGEMENT_BASE              fichier SQLite, ex. /opt/data/management.db
  MBILING_EMAIL_*                      réglages d'emails de .env, importés une seule fois (base vide)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .emails import envoyer_essai
from .keycloak import ErreurComptes, Keycloak
from .stockage import Stockage

ICI = Path(__file__).parent
P = "/management"
COOKIE_SESSION = "mbiling_management"
COOKIE_ETAT = "mbiling_management_etat"
SMTP = "smtp"
_HOTE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class Config:
    url_publique: str
    emetteur: str
    comptes_interne: str
    royaume: str
    client_secret: str
    api_secret: str
    cle: str
    base: Path
    client_id: str = "management"
    api_client_id: str = "management-api"
    droits: Path = ICI / "droits.json"

    @classmethod
    def depuis_env(cls) -> "Config":
        def lire(nom: str) -> str:
            valeur = os.environ.get(nom, "").strip()
            if not valeur:
                raise RuntimeError(f"{nom} manquant (voir serveur/compose.yaml)")
            return valeur
        return cls(url_publique=lire("MBILING_MANAGEMENT_URL").rstrip("/"),
                   emetteur=lire("MBILING_MANAGEMENT_EMETTEUR").rstrip("/"),
                   comptes_interne=lire("MBILING_MANAGEMENT_COMPTES_INTERNE"),
                   royaume=lire("MBILING_MANAGEMENT_ROYAUME"),
                   client_secret=lire("MBILING_MANAGEMENT_CLIENT_SECRET"),
                   api_secret=lire("MBILING_MANAGEMENT_API_SECRET"),
                   cle=lire("MBILING_MANAGEMENT_CLE"),
                   base=Path(lire("MBILING_MANAGEMENT_BASE")))


def _fernet(cle: str, usage: bytes) -> Fernet:
    if len(cle) < 32:
        raise RuntimeError("MBILING_MANAGEMENT_CLE doit faire au moins 32 caractères")
    derivee = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"mbiling-management/" + usage).derive(cle.encode())
    return Fernet(base64.urlsafe_b64encode(derivee))


@dataclass
class Session:
    sub: str
    email: str
    nom: str
    roles: list[str]
    csrf: str
    id_token: str = ""
    permissions: set[str] = field(default_factory=set)

    def peut(self, permission: str) -> bool:
        return "*" in self.permissions or permission in self.permissions


def _ip(request: Request) -> str | None:
    return (request.headers.get("cf-connecting-ip")
            or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
            or (request.client.host if request.client else None))


def creer_app(config: Config | None = None, *, transport: httpx.BaseTransport | None = None,
              envoyeur: Callable[[dict[str, Any], str | None, str], None] = envoyer_essai) -> FastAPI:
    config = config or Config.depuis_env()
    droits = json.loads(config.droits.read_text(encoding="utf-8"))
    roles_connus: dict[str, dict[str, Any]] = droits["roles"]
    duree_session = int(droits.get("session_minutes", 480)) * 60
    coffre = _fernet(config.cle, b"secrets")
    jetons = _fernet(config.cle, b"sessions")
    stockage = Stockage(config.base, coffre)
    comptes = Keycloak(interne=config.comptes_interne, royaume=config.royaume, emetteur=config.emetteur,
                       client_id=config.client_id, client_secret=config.client_secret,
                       api_client_id=config.api_client_id, api_client_secret=config.api_secret, transport=transport)
    gabarits = Environment(loader=FileSystemLoader(ICI / "gabarits"), autoescape=select_autoescape(["html"]))
    gabarits.filters["date"] = lambda t: time.strftime("%d/%m/%Y %H:%M:%S", time.gmtime(t)) + " UTC"
    retour = f"{config.url_publique}/retour"

    _importer_smtp_de_env(stockage)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def entetes_de_securite(request: Request, suivant):
        reponse = await suivant(request)
        reponse.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'self'; img-src 'self' data:; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'")
        reponse.headers["X-Content-Type-Options"] = "nosniff"
        reponse.headers["Referrer-Policy"] = "no-referrer"
        reponse.headers["Cache-Control"] = "no-store"
        return reponse

    def page(nom: str, session: Session | None, statut: int = 200, **contexte: Any) -> HTMLResponse:
        contexte.setdefault("message", None)
        return HTMLResponse(gabarits.get_template(nom).render(
            session=session, P=P, roles_connus=roles_connus, **contexte), status_code=statut)

    def session_de(request: Request) -> Session | None:
        brut = request.cookies.get(COOKIE_SESSION)
        if not brut:
            return None
        try:
            d = json.loads(jetons.decrypt(brut.encode(), ttl=duree_session))
        except (InvalidToken, ValueError):
            return None
        s = Session(**d)
        for role in s.roles:
            s.permissions.update(roles_connus.get(role, {}).get("permissions", []))
        return s

    def exiger(request: Request, permission: str) -> Session | Response:
        session = session_de(request)
        if session is None:
            return RedirectResponse(f"{P}/connexion", status_code=303)
        if not session.peut(permission):
            return page("message.html", session, 403, titre="Accès refusé",
                        texte="Votre rôle ne donne pas accès à cette page.")
        return session

    def csrf_valide(session: Session, formulaire: Any) -> bool:
        return secrets.compare_digest(str(formulaire.get("csrf", "")), session.csrf)

    # --- Connexion -----------------------------------------------------------------------------

    @app.get(f"{P}/sante")
    def sante() -> Response:
        return Response("ok", media_type="text/plain")

    @app.get(f"{P}/style.css")
    def style() -> Response:
        return FileResponse(ICI / "gabarits" / "style.css", media_type="text/css")

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
            acces = comptes.verifier(reponse_jetons["access_token"], audience=None)
            if identite.get("nonce") != nonce or acces.get("azp") != config.client_id:
                raise ErreurComptes("jeton émis pour une autre connexion")
            roles = sorted(r for r in (acces.get("realm_access") or {}).get("roles", []) if r in roles_connus)
            qui = identite.get("email") or identite["sub"]
            if not roles:
                stockage.journaliser(qui, "connexion refusée", {"raison": "aucun rôle Management"}, _ip(request))
                return page("message.html", None, 403, titre="Accès refusé",
                            texte="Ce compte n'a pas accès à Management.")
            if not comptes.a_double_authentification(identite["sub"]):
                stockage.journaliser(qui, "connexion refusée", {"raison": "double authentification absente"}, _ip(request))
                return page("message.html", None, 403, titre="Double authentification requise",
                            texte="Configurez la double authentification de votre compte, puis reconnectez-vous.",
                            lien=f"{P}/connexion")
        except (ErreurComptes, KeyError, httpx.HTTPError) as erreur:
            stockage.journaliser("inconnu", "connexion refusée", {"raison": str(erreur)}, _ip(request))
            return page("message.html", None, 502, titre="Connexion impossible",
                        texte="Le serveur de comptes n'a pas confirmé la connexion.", lien=f"{P}/connexion")
        session = {"sub": identite["sub"], "email": identite.get("email", ""), "roles": roles,
                   "nom": identite.get("name") or identite.get("email", ""), "csrf": secrets.token_urlsafe(32),
                   "id_token": reponse_jetons["id_token"]}
        stockage.journaliser(qui, "connexion", {"roles": roles}, _ip(request))
        reponse = RedirectResponse(f"{P}/", status_code=303)
        reponse.set_cookie(COOKIE_SESSION, jetons.encrypt(json.dumps(session).encode()).decode(),
                           max_age=duree_session, path=P, secure=True, httponly=True, samesite="lax")
        reponse.delete_cookie(COOKIE_ETAT, path=P)
        return reponse

    @app.post(f"{P}/deconnexion")
    async def deconnexion(request: Request) -> Response:
        session = session_de(request)
        if session is None:
            return RedirectResponse(f"{P}/connexion", status_code=303)
        if not csrf_valide(session, await request.form()):
            return page("message.html", session, 403, titre="Action refusée", texte="Jeton de formulaire invalide.")
        stockage.journaliser(session.email, "déconnexion", None, _ip(request))
        cible = comptes.page_deconnexion + "?" + urlencode({
            "client_id": config.client_id, "id_token_hint": session.id_token,
            "post_logout_redirect_uri": f"{config.url_publique}/"})
        reponse = RedirectResponse(cible, status_code=303)
        reponse.delete_cookie(COOKIE_SESSION, path=P)
        return reponse

    # --- Pages ---------------------------------------------------------------------------------

    @app.get(P)
    def sans_barre() -> Response:
        return RedirectResponse(f"{P}/", status_code=307)

    @app.get(f"{P}/")
    def accueil(request: Request) -> Response:
        session = exiger(request, "accueil.voir")
        if not isinstance(session, Session):
            return session
        return page("accueil.html", session, smtp=stockage.lire(SMTP))

    @app.get(f"{P}/email")
    def email(request: Request) -> Response:
        session = exiger(request, "email.voir")
        if not isinstance(session, Session):
            return session
        return page("email.html", session, smtp=stockage.lire(SMTP),
                    secret_present=stockage.secret(SMTP) is not None)

    @app.post(f"{P}/email")
    async def email_enregistrer(request: Request) -> Response:
        session = exiger(request, "email.modifier")
        if not isinstance(session, Session):
            return session
        f = await request.form()
        if not csrf_valide(session, f):
            return page("message.html", session, 403, titre="Action refusée", texte="Jeton de formulaire invalide.")
        avant = stockage.lire(SMTP)
        nouveau = {"serveur": str(f.get("serveur", "")).strip(), "expediteur": str(f.get("expediteur", "")).strip(),
                   "identifiant": str(f.get("identifiant", "")).strip(), "starttls": f.get("starttls") == "on",
                   "nom_affiche": str(f.get("nom_affiche", "")).strip() or "Mbiling"}
        mot_de_passe = str(f.get("mot_de_passe", "")) or None
        erreurs = []
        try:
            nouveau["port"] = int(str(f.get("port", "")).strip())
            if not 1 <= nouveau["port"] <= 65535:
                raise ValueError
        except ValueError:
            erreurs.append("Le port doit être un nombre entre 1 et 65535.")
        if not _HOTE.match(nouveau["serveur"]):
            erreurs.append("Le serveur SMTP doit être un nom d'hôte (ex. smtp.exemple.com).")
        if not _EMAIL.match(nouveau["expediteur"]):
            erreurs.append("L'adresse d'expéditeur n'est pas une adresse email.")
        if erreurs:
            return page("email.html", session, 400, smtp={**(avant or {}), **nouveau},
                        secret_present=stockage.secret(SMTP) is not None, erreurs=erreurs)
        secret_effectif = mot_de_passe if mot_de_passe is not None else stockage.secret(SMTP)
        try:
            comptes.appliquer_smtp(nouveau, secret_effectif)
        except (ErreurComptes, httpx.HTTPError) as erreur:
            return page("email.html", session, 502, smtp={**(avant or {}), **nouveau},
                        secret_present=stockage.secret(SMTP) is not None,
                        erreurs=[f"Réglages non enregistrés : {erreur}."])
        stockage.ecrire(SMTP, nouveau, session.email, mot_de_passe)
        propre = lambda r: {k: v for k, v in (r or {}).items() if not k.startswith("_")}  # noqa: E731
        stockage.journaliser(session.email, "réglages d'emails modifiés", {
            "avant": propre(avant), "apres": nouveau,
            "mot_de_passe": "remplacé" if mot_de_passe is not None else "inchangé"}, _ip(request))
        return page("email.html", session, smtp=stockage.lire(SMTP), secret_present=stockage.secret(SMTP) is not None,
                    message="Réglages enregistrés et appliqués au serveur de comptes.")

    @app.post(f"{P}/email/essai")
    async def email_essai(request: Request) -> Response:
        session = exiger(request, "email.modifier")
        if not isinstance(session, Session):
            return session
        if not csrf_valide(session, await request.form()):
            return page("message.html", session, 403, titre="Action refusée", texte="Jeton de formulaire invalide.")
        smtp = stockage.lire(SMTP)
        contexte = {"smtp": smtp, "secret_present": stockage.secret(SMTP) is not None}
        if smtp is None or not session.email:
            return page("email.html", session, 400, erreurs=["Enregistrez d'abord les réglages."], **contexte)
        try:
            envoyeur(smtp, stockage.secret(SMTP), session.email)
        except Exception as erreur:  # smtplib, réseau, TLS : le message est montré à l'administrateur
            stockage.journaliser(session.email, "email de test en échec", {"erreur": str(erreur)}, _ip(request))
            return page("email.html", session, 502, erreurs=[f"Échec de l'envoi : {erreur}"], **contexte)
        stockage.journaliser(session.email, "email de test envoyé", {"a": session.email}, _ip(request))
        return page("email.html", session, message=f"Email de test envoyé à {session.email}.", **contexte)

    @app.get(f"{P}/journal")
    def journal(request: Request) -> Response:
        session = exiger(request, "journal.voir")
        if not isinstance(session, Session):
            return session
        return page("journal.html", session, lignes=stockage.journal(int(droits.get("journal_lignes", 200))),
                    intact=stockage.journal_intact())

    return app


def _importer_smtp_de_env(stockage: Stockage) -> None:
    """Base vide : reprend une seule fois les réglages d'emails de serveur/.env (installation de la phase 1)."""
    serveur = os.environ.get("MBILING_EMAIL_SERVEUR", "").strip()
    if stockage.lire(SMTP) is not None or not serveur:
        return
    smtp = {"serveur": serveur, "port": int(os.environ.get("MBILING_EMAIL_PORT") or 587),
            "expediteur": os.environ.get("MBILING_EMAIL_EXPEDITEUR", "").strip(),
            "identifiant": os.environ.get("MBILING_EMAIL_IDENTIFIANT", "").strip(),
            "starttls": True, "nom_affiche": "Mbiling"}
    stockage.ecrire(SMTP, smtp, "système", os.environ.get("MBILING_EMAIL_MOT_DE_PASSE") or None)
    stockage.journaliser("système", "réglages d'emails importés depuis .env", {"apres": smtp})
