"""Environnement d'exécution « Mains Mbiling » : les commandes de l'agent s'exécutent
sur le PC du client, à travers le Relais."""
from __future__ import annotations

import logging
import os

from tools.environments.base import BaseEnvironment, EnvironmentConnectionError

from plugins.mbiling_mains.transport import CommandeDistante, ConfigRelais, MainsIndisponibles, info_appareil

logger = logging.getLogger(__name__)


def _dossiers_de_la_cellule() -> set[str]:
    """Les dossiers propres à la cellule, que le moteur prend par défaut comme dossier de travail."""
    from mbiling_constants import get_mbiling_home

    dossiers = {"/root", os.getcwd(), str(get_mbiling_home())}
    # « / » couvrirait tous les chemins du PC : on ne l'écarte jamais en bloc.
    propres = (d.replace("\\", "/").rstrip("/") for d in dossiers if d)
    return {d for d in propres if d}


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
        super().__init__(cwd=self._sur_le_pc(cwd) or self._depart, timeout=timeout)
        self.init_session()

    def _sur_le_pc(self, cwd: str) -> str:
        """Un dossier de la cellule (son dossier de travail, son MBILING_HOME) n'existe pas sur le PC :
        on part alors du dossier de départ du PC. Sinon « cd » échoue et chaque commande renvoie 126."""
        if not cwd or cwd == "~":
            return self._depart
        propre = cwd.replace("\\", "/").rstrip("/") or "/"
        for serveur in _dossiers_de_la_cellule():
            if propre == serveur or propre.startswith(serveur + "/"):
                return self._depart
        return cwd

    def execute(self, command: str, cwd: str = "", **kwargs):
        if cwd:
            cwd = self._sur_le_pc(cwd)
        return super().execute(command, cwd, **kwargs)

    def get_temp_dir(self) -> str:
        return self._temp

    def _run_bash(self, cmd_string: str, *, login: bool = False, timeout: int = 120, stdin_data: str | None = None):
        return CommandeDistante(self._config, cmd_string, login=login, timeout=timeout, stdin=stdin_data)

    def cleanup(self):
        """Rien à libérer côté cellule : chaque commande a sa propre connexion."""
