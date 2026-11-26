"""Sessions fermées par le serveur d'identité (OpenID Connect Back-Channel Logout 1.0).

Le jeton d'identité que la cellule vérifie à chaque requête reste valable jusqu'à son ``exp``, même
quand le serveur d'identité a fermé la session (appareil révoqué, « déconnecter tous mes
appareils », mot de passe changé). Le serveur d'identité prévient alors la cellule par un
« logout token » signé ; on retient la session fermée (``sid``), ou, sans ``sid``, l'utilisateur
(``sub``) avec l'heure de fermeture, jusqu'à ce que les jetons concernés aient tous expiré.

Le registre est persisté (écriture atomique) dans ``<MBILING_HOME>/auth/sessions-fermees.json`` : un
redémarrage de la cellule ne rend pas leur validité aux jetons d'une session fermée, et chaque
processus qui vérifie des jetons relit le fichier quand il change.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

EVENEMENT = "http://schemas.openid.net/event/backchannel-logout"

_verrou = threading.Lock()
_cache: dict[str, Any] = {"cle": None, "donnees": {"sessions": {}, "utilisateurs": {}}}


def _fichier() -> Path:
    from mbiling_constants import get_mbiling_home
    return get_mbiling_home() / "auth" / "sessions-fermees.json"


def _lire(chemin: Path) -> dict[str, dict[str, float]]:
    try:
        stat = chemin.stat()
    except FileNotFoundError:
        return {"sessions": {}, "utilisateurs": {}}
    cle = (str(chemin), stat.st_mtime_ns, stat.st_size)
    if _cache["cle"] != cle:
        try:
            brut = json.loads(chemin.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            brut = {}
        _cache["donnees"] = {"sessions": dict(brut.get("sessions") or {}),
                             "utilisateurs": dict(brut.get("utilisateurs") or {})}
        _cache["cle"] = cle
    return _cache["donnees"]


def _ecrire(chemin: Path, donnees: dict[str, dict[str, float]]) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    provisoire = chemin.with_suffix(".tmp")
    provisoire.write_text(json.dumps(donnees), encoding="utf-8")
    os.replace(provisoire, chemin)


def fermer(*, sid: str = "", sub: str = "", garder_jusqua: float, maintenant: float | None = None) -> None:
    """Retient une session (``sid``) ou toutes les sessions d'un utilisateur ouvertes avant
    maintenant (``sub``), jusqu'à ``garder_jusqua`` (au-delà, leurs jetons ont expiré)."""
    maintenant = time.time() if maintenant is None else maintenant
    chemin = _fichier()
    with _verrou:
        actuel = _lire(chemin)
        donnees = {
            "sessions": {s: t for s, t in actuel["sessions"].items() if t > maintenant},
            "utilisateurs": {u: v for u, v in actuel["utilisateurs"].items() if v[1] > maintenant},
        }
        if sid:
            donnees["sessions"][sid] = max(garder_jusqua, donnees["sessions"].get(sid, 0))
        if sub:
            donnees["utilisateurs"][sub] = [maintenant, garder_jusqua]
        _ecrire(chemin, donnees)


def est_fermee(claims: dict[str, Any]) -> bool:
    """Vrai si le jeton (déjà vérifié) appartient à une session fermée."""
    with _verrou:
        donnees = _lire(_fichier())
    sid = str(claims.get("sid") or "")
    if sid and sid in donnees["sessions"]:
        return True
    ferme_utilisateur = donnees["utilisateurs"].get(str(claims.get("sub") or ""))
    return bool(ferme_utilisateur) and float(claims.get("iat") or 0) <= ferme_utilisateur[0]


def verifier_logout_token(claims: dict[str, Any]) -> tuple[str, str]:
    """Contrôles de la spécification (§ 2.6) sur un logout token dont la signature, l'émetteur et
    l'audience sont déjà vérifiés. Rend ``(sid, sub)`` ; lève ``ValueError`` sinon."""
    evenements = claims.get("events")
    if not isinstance(evenements, dict) or EVENEMENT not in evenements:
        raise ValueError("événement de déconnexion absent")
    if "nonce" in claims:
        raise ValueError("un logout token ne porte pas de nonce")
    sid, sub = str(claims.get("sid") or ""), str(claims.get("sub") or "")
    if not sid and not sub:
        raise ValueError("ni sid ni sub")
    return sid, sub
