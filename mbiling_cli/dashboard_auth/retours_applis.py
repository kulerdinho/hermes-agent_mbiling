"""Adresses de retour des applications Mbiling (Mobile) pour la connexion native.

La connexion native (RFC 8252, ``/auth/native/authorize``) n'accepte au départ qu'une adresse de
retour en boucle locale (``http://127.0.0.1:<port>/…``) : c'est ce que font le Desktop et la CLI.
Une application de téléphone ne peut pas écouter sur la boucle locale ; elle reçoit le retour par
un schéma d'URI qui lui est propre (RFC 8252 § 7.1), par exemple ``mbiling://connexion``.

Ce module tient la liste **fermée** de ces adresses : ``dashboard.oauth.retours_applis`` dans
``config.yaml``. Vide par défaut : sans réglage, rien ne change. Une adresse n'est acceptée que si
elle figure **exactement** dans la liste. Le code PKCE lié à la demande reste exigé à l'échange
(``/auth/native/token``) : une autre application qui intercepterait le retour ne pourrait pas s'en
servir sans le vérificateur, qui ne quitte jamais l'application d'origine.
"""

from __future__ import annotations

import logging
import re
from typing import Any

_log = logging.getLogger(__name__)

# Schéma d'URI (RFC 3986 § 3.1). Les schémas du Web et les schémas dangereux sont exclus : une
# adresse https ne doit jamais devenir une destination de jetons par ce réglage.
_SCHEMA = re.compile(r"^[a-z][a-z0-9+.\-]*$")
_SCHEMAS_INTERDITS = frozenset({"http", "https", "javascript", "data", "file", "blob", "about", "vbscript"})


def adresse_valide(adresse: str) -> bool:
    """Vrai si ``adresse`` peut servir de retour d'application : schéma propre à l'application,
    ASCII sans espace ni caractère de contrôle, sans fragment ni identifiants."""
    if not adresse or not adresse.isascii() or "#" in adresse or "\\" in adresse:
        return False
    if any(ord(c) <= 0x20 or ord(c) == 0x7F for c in adresse):
        return False
    schema, sep, reste = adresse.partition(":")
    if not sep or not reste or not _SCHEMA.match(schema) or schema in _SCHEMAS_INTERDITS:
        return False
    autorite = reste[2:].split("/", 1)[0] if reste.startswith("//") else ""
    return "@" not in autorite


def _valeur_reglee() -> Any:
    try:
        from mbiling_cli.config import load_config_readonly
        oauth = ((load_config_readonly().get("dashboard") or {}).get("oauth") or {})
    except Exception as exc:  # configuration absente ou illisible : aucune adresse
        _log.debug("retours_applis : configuration illisible (%s)", exc)
        return []
    return oauth.get("retours_applis") if isinstance(oauth, dict) else []


def adresses_autorisees(valeur: Any = None) -> frozenset[str]:
    """La liste réglée (``valeur`` sinon ``config.yaml``), sans les adresses invalides, qui sont
    signalées dans le journal."""
    if valeur is None:
        valeur = _valeur_reglee()
    if not isinstance(valeur, list):
        return frozenset()
    retenues = set()
    for brute in valeur:
        adresse = str(brute).strip()
        if not adresse:
            continue
        if adresse_valide(adresse):
            retenues.add(adresse)
        else:
            _log.warning("retours_applis : adresse de retour ignorée (invalide) : %r", adresse)
    return frozenset(retenues)


def est_autorisee(adresse: str) -> bool:
    """Vrai si ``adresse`` figure exactement dans la liste réglée par la cellule."""
    return bool(adresse) and adresse in adresses_autorisees()
