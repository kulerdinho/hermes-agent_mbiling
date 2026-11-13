"""Route d'inscription automatique d'un PC : /api/plugins/mbiling_mains/appareil.

Mbiling Desktop l'appelle juste après la connexion de l'utilisateur (avec sa session), quand il
autorise l'agent à utiliser son PC. La cellule demande alors au Relais un jeton d'appareil pour
SON organisation et le renvoie au PC, qui le garde chiffré : l'utilisateur ne saisit rien.

Configuration (environnement de la cellule, jamais le code) :
  MBILING_RELAIS_URL, MBILING_RELAIS_JETON, MBILING_ORGANISATION (voir transport.py)
  MBILING_RELAIS_URL_PUBLIQUE  adresse du Relais vue depuis les PC (ex. wss://api.mbiling.com/mains)
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from plugins.mbiling_mains.transport import ConfigRelais, MainsIndisponibles, inscrire_appareil

router = APIRouter()


class DemandeAppareil(BaseModel):
    nom: str = ""


@router.post("/appareil")
async def inscrire(demande: DemandeAppareil, request: Request) -> dict[str, Any]:
    session = getattr(request.state, "session", None)
    par_jeton = bool(getattr(request.state, "token_authenticated", False))
    if session is None and not par_jeton:
        raise HTTPException(status_code=401, detail="connexion requise")
    try:
        config = ConfigRelais.depuis_environnement()
    except MainsIndisponibles as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # Une cellule sert une seule organisation : un compte d'une autre organisation est refusé.
    organisation_session = getattr(session, "org_id", None) if session is not None else None
    if organisation_session and organisation_session != config.organisation:
        raise HTTPException(status_code=403, detail="ce compte n'appartient pas à l'organisation de ce serveur")
    try:
        inscrit = await asyncio.to_thread(inscrire_appareil, config, demande.nom.strip()[:120])
    except MainsIndisponibles as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "appareil": inscrit["appareil"],
        "jeton": inscrit["jeton"],
        "relais_url": os.environ.get("MBILING_RELAIS_URL_PUBLIQUE") or None,
    }
