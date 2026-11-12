"""Environnement d'exécution « Mains Mbiling » : les commandes de l'agent s'exécutent
sur le PC du client, à travers le Relais."""
from __future__ import annotations

import logging

from tools.environments.base import BaseEnvironment, EnvironmentConnectionError

from plugins.mbiling_mains.transport import CommandeDistante, ConfigRelais, MainsIndisponibles, info_appareil

logger = logging.getLogger(__name__)


class EnvironnementMains(BaseEnvironment):
    """Une commande = une demande au Relais, exécutée par bash sur le PC.

    * Aucune synchronisation de fichiers (rien du dossier de la cellule ne part vers le PC).
    * L'entrée standard voyage avec la demande (``payload``).
    * La politique locale du PC reste le dernier verrou : elle peut refuser ce que la cellule a accepté.
    """

    _stdin_mode = "payload"

    def __init__(self, cwd: str, timeout: int, config: ConfigRelais | None = None):
        self._config = config or ConfigRelais.depuis_environnement()
        try:
            self._appareil = info_appareil(self._config)
        except MainsIndisponibles as exc:
            raise EnvironmentConnectionError(str(exc)) from exc
        info = self._appareil.get("info") or {}
        self._temp = (info.get("tmp") or "/tmp").rstrip("/") or "/tmp"
        # « ~ » désigne le premier dossier autorisé par le PC, pas le dossier personnel de Windows.
        self._depart = info.get("dossier_depart") or "~"
        if not cwd or cwd in ("~", "/root"):
            cwd = self._depart
        super().__init__(cwd=cwd, timeout=timeout)
        self.init_session()

    def execute(self, command: str, cwd: str = "", **kwargs):
        if cwd == "~":
            cwd = self._depart
        return super().execute(command, cwd, **kwargs)

    def get_temp_dir(self) -> str:
        return self._temp

    def _run_bash(self, cmd_string: str, *, login: bool = False, timeout: int = 120, stdin_data: str | None = None):
        return CommandeDistante(self._config, cmd_string, login=login, timeout=timeout, stdin=stdin_data)

    def cleanup(self):
        """Rien à libérer côté cellule : chaque commande a sa propre connexion."""
