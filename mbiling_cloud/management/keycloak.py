"""Appels de Management au serveur de comptes (Keycloak), par le réseau interne du serveur.

Deux usages :
- la connexion des administrateurs (échange du code, clés de signature des jetons) ;
- l'API d'administration, avec le compte de service du client ``management-api`` (rôles
  ``manage-realm`` et ``view-users`` du royaume) : réglages d'emails, double authentification.

Les jetons portent l'adresse PUBLIQUE du serveur de comptes (``emetteur``) ; les appels passent par
son adresse INTERNE (``interne``), sans faire le tour par Internet.
"""
from __future__ import annotations

import time
from typing import Any

import httpx
import jwt


class ErreurComptes(RuntimeError):
    pass


class Keycloak:
    def __init__(self, *, interne: str, royaume: str, emetteur: str, client_id: str, client_secret: str,
                 api_client_id: str, api_client_secret: str, transport: httpx.BaseTransport | None = None) -> None:
        self.emetteur = emetteur.rstrip("/")
        self._oidc = f"{interne.rstrip('/')}/realms/{royaume}/protocol/openid-connect"
        self._admin = f"{interne.rstrip('/')}/admin/realms/{royaume}"
        self._client = (client_id, client_secret)
        self._api = (api_client_id, api_client_secret)
        self._http = httpx.Client(timeout=15, transport=transport)
        self._cles: dict[str, Any] = {}
        self._cles_le = 0.0
        self._jeton_api: tuple[str, float] | None = None

    # --- Connexion des administrateurs ----------------------------------------------------------

    @property
    def page_connexion(self) -> str:
        return f"{self.emetteur}/protocol/openid-connect/auth"

    @property
    def page_deconnexion(self) -> str:
        return f"{self.emetteur}/protocol/openid-connect/logout"

    def echanger_code(self, code: str, redirect_uri: str, verificateur: str) -> dict[str, Any]:
        rep = self._http.post(f"{self._oidc}/token", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": self._client[0], "client_secret": self._client[1], "code_verifier": verificateur})
        if rep.status_code != 200:
            raise ErreurComptes(f"échange du code refusé ({rep.status_code})")
        return rep.json()

    def _cle(self, kid: str) -> Any:
        # Les clés changent rarement : relues au plus toutes les 10 min, ou si une clé inconnue arrive.
        if kid not in self._cles or time.time() - self._cles_le > 600:
            rep = self._http.get(f"{self._oidc}/certs")
            rep.raise_for_status()
            self._cles = {k.key_id: k for k in jwt.PyJWKSet.from_dict(rep.json()).keys}
            self._cles_le = time.time()
        if kid not in self._cles:
            raise ErreurComptes("clé de signature inconnue")
        return self._cles[kid].key

    def verifier(self, jeton: str, *, audience: str | None) -> dict[str, Any]:
        """Vérifie signature, émetteur, dates (et audience si donnée) ; rend le contenu du jeton."""
        try:
            entete = jwt.get_unverified_header(jeton)
            return jwt.decode(jeton, self._cle(entete.get("kid", "")), algorithms=["RS256", "ES256"],
                              issuer=self.emetteur, audience=audience, leeway=60,
                              options={"verify_aud": audience is not None})
        except jwt.PyJWTError as erreur:
            raise ErreurComptes(f"jeton refusé : {erreur}") from erreur

    # --- API d'administration -------------------------------------------------------------------

    def _entetes_api(self) -> dict[str, str]:
        if self._jeton_api is None or self._jeton_api[1] < time.time() + 30:
            rep = self._http.post(f"{self._oidc}/token", data={
                "grant_type": "client_credentials", "client_id": self._api[0], "client_secret": self._api[1]})
            if rep.status_code != 200:
                raise ErreurComptes(f"compte de service refusé ({rep.status_code})")
            corps = rep.json()
            self._jeton_api = (corps["access_token"], time.time() + int(corps.get("expires_in", 60)))
        return {"Authorization": f"Bearer {self._jeton_api[0]}"}

    def a_double_authentification(self, utilisateur: str) -> bool:
        rep = self._http.get(f"{self._admin}/users/{utilisateur}/credentials", headers=self._entetes_api())
        if rep.status_code != 200:
            raise ErreurComptes(f"lecture des facteurs de connexion impossible ({rep.status_code})")
        return any(c.get("type") == "otp" for c in rep.json())

    def chercher(self, texte: str, maximum: int) -> list[dict[str, Any]]:
        """Comptes dont l'email, le nom ou l'identifiant contient ``texte`` (sans ``*``, le serveur de
        comptes ne cherche qu'en début de mot : « gmail » ne trouverait pas marie@gmail.com)."""
        rep = self._http.get(f"{self._admin}/users", headers=self._entetes_api(),
                             params={"search": f"*{texte.strip('*')}*", "max": maximum, "briefRepresentation": "true"})
        if rep.status_code != 200:
            raise ErreurComptes(f"recherche des comptes impossible ({rep.status_code})")
        return rep.json()

    def utilisateur(self, utilisateur: str) -> dict[str, Any] | None:
        rep = self._http.get(f"{self._admin}/users/{utilisateur}", headers=self._entetes_api())
        if rep.status_code == 404:
            return None
        if rep.status_code != 200:
            raise ErreurComptes(f"lecture du compte impossible ({rep.status_code})")
        return rep.json()

    def roles_royaume(self, utilisateur: str) -> list[str]:
        rep = self._http.get(f"{self._admin}/users/{utilisateur}/role-mappings/realm", headers=self._entetes_api())
        if rep.status_code != 200:
            raise ErreurComptes(f"lecture des rôles impossible ({rep.status_code})")
        return [r["name"] for r in rep.json()]

    def activer(self, utilisateur: str, actif: bool) -> None:
        """Suspend (connexion impossible, données gardées) ou réactive un compte."""
        rep = self._http.put(f"{self._admin}/users/{utilisateur}", headers=self._entetes_api(), json={"enabled": actif})
        if rep.status_code != 204:
            raise ErreurComptes(f"le serveur de comptes a refusé le changement ({rep.status_code})")

    def sessions(self, utilisateur: str) -> list[dict[str, Any]]:
        """Sessions ouvertes du compte : id (le ``sid`` des jetons), ipAddress, start, lastAccess (ms),
        clients {id: clientId}."""
        rep = self._http.get(f"{self._admin}/users/{utilisateur}/sessions", headers=self._entetes_api())
        if rep.status_code != 200:
            raise ErreurComptes(f"lecture des sessions impossible ({rep.status_code})")
        return rep.json()

    def fermer_session(self, session: str) -> None:
        """Ferme une session ; le serveur de comptes prévient aussitôt les cellules (déconnexion poussée)."""
        rep = self._http.delete(f"{self._admin}/sessions/{session}", headers=self._entetes_api())
        if rep.status_code not in (204, 404):
            raise ErreurComptes(f"fermeture de la session impossible ({rep.status_code})")

    def deconnecter_partout(self, utilisateur: str) -> None:
        """Ferme toutes les sessions du compte (« Déconnecter tous mes appareils »)."""
        rep = self._http.post(f"{self._admin}/users/{utilisateur}/logout", headers=self._entetes_api())
        if rep.status_code != 204:
            raise ErreurComptes(f"déconnexion du compte impossible ({rep.status_code})")

    def appliquer_smtp(self, smtp: dict[str, Any], mot_de_passe: str | None) -> None:
        """Pose les réglages d'envoi du royaume, et active la vérification des adresses."""
        serveur = {
            "host": smtp["serveur"], "port": str(smtp["port"]), "from": smtp["expediteur"],
            "fromDisplayName": smtp.get("nom_affiche") or "Mbiling",
            "ssl": "true" if int(smtp["port"]) == 465 else "false",
            "starttls": "true" if smtp.get("starttls", True) and int(smtp["port"]) != 465 else "false",
            "auth": "true" if smtp.get("identifiant") else "false",
            "user": smtp.get("identifiant") or "",
            "password": mot_de_passe or ""}
        rep = self._http.put(self._admin, headers=self._entetes_api(),
                             json={"smtpServer": serveur, "verifyEmail": True})
        if rep.status_code not in (200, 204):
            raise ErreurComptes(f"le serveur de comptes a refusé les réglages d'emails ({rep.status_code})")
