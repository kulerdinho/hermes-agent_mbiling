"""Mains Mbiling : exécute le terminal et les fichiers de l'agent sur le PC du client.

Activer dans la configuration de la cellule :
    terminal:
      backend: mbiling_mains
    plugins:
      isolation: in_process
"""
from __future__ import annotations

from typing import Any, Optional

from agent.terminal_env_provider import TerminalEnvironmentProvider

from plugins.mbiling_mains.transport import ConfigRelais, MainsIndisponibles, info_appareil


class FournisseurMains(TerminalEnvironmentProvider):
    is_remote = True
    # Ce n'est pas un bac à sable jetable : c'est le vrai PC du client.
    is_container = False

    @property
    def name(self) -> str:
        return "mbiling_mains"

    @property
    def display_name(self) -> str:
        return "Mains Mbiling"

    @property
    def description(self) -> str:
        return "Exécute les commandes sur l'ordinateur du client, via l'application Mbiling."

    @property
    def skip_container_guards(self) -> bool:
        return False  # toutes les approbations restent actives

    @property
    def env_description(self) -> str:
        return ("the user's own computer, reached through the Mbiling app (bash; on Windows this is Git Bash, "
                "so use /c/Users/... style paths)")

    def _config(self) -> Optional[ConfigRelais]:
        try:
            return ConfigRelais.depuis_environnement()
        except MainsIndisponibles:
            return None

    def is_available(self) -> bool:
        return self._config() is not None  # pas d'appel réseau ici

    def probe(self) -> tuple[str, str]:
        config = self._config()
        if config is None:
            return ("needs_setup", "Mains Mbiling : configuration du Relais absente.")
        try:
            info = info_appareil(config)
        except MainsIndisponibles as exc:
            return ("unavailable", str(exc))
        return ("ready", f"Appareil connecté : {info.get('appareil', '?')}")

    def create_environment(
        self, *, cwd: str, timeout: int, task_id: str = "default", image: Optional[str] = None,
        container_config: Optional[dict[str, Any]] = None, **kwargs: Any,
    ):
        from plugins.mbiling_mains.environnement import EnvironnementMains

        return EnvironnementMains(cwd=cwd, timeout=timeout)


def register(ctx) -> None:
    ctx.register_terminal_environment_provider(FournisseurMains())
