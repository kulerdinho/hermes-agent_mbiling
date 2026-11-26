"""Routes internes du Relais des Mains, pour les services du Cloud (« Mon compte », Management) :
PC inscrits par un compte, et leur révocation (jeton d'appareil refusé, Mains coupées)."""
from __future__ import annotations

from typing import Any

import httpx


class Relais:
    def __init__(self, url: str, jeton: str, transport: httpx.BaseTransport | None = None) -> None:
        self._url = url.rstrip("/")
        self._http = httpx.Client(timeout=10, transport=transport, headers={"Authorization": f"Bearer {jeton}"})

    def appareils(self, utilisateur: str) -> list[dict[str, Any]]:
        rep = self._http.get(f"{self._url}/interne/appareils", params={"utilisateur": utilisateur})
        rep.raise_for_status()
        return rep.json()["appareils"]

    def revoquer(self, utilisateur: str, *, session: str = "", identifiant: str = "") -> list[str]:
        """Révoque les PC de ``utilisateur`` : tous, ceux d'une session, ou un seul."""
        rep = self._http.post(f"{self._url}/interne/appareils/revoquer",
                              json={"utilisateur": utilisateur, "session": session, "identifiant": identifiant})
        rep.raise_for_status()
        return rep.json()["revoques"]
