"""Transport entre la cellule et le Relais des Mains (bibliothèque standard uniquement).

Ce module ne dépend pas du reste du moteur : il peut être testé seul.
La configuration vient de l'environnement de la cellule, jamais du code :
  MBILING_RELAIS_URL    adresse interne du Relais (ex. http://relais:8700)
  MBILING_RELAIS_JETON  jeton interne partagé avec le Relais
  MBILING_ORGANISATION  identifiant de l'organisation servie par cette cellule
"""
from __future__ import annotations

import http.client
import json
import os
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit


class MainsIndisponibles(RuntimeError):
    """Aucun appareil joignable, ou Relais injoignable."""


@dataclass(frozen=True)
class ConfigRelais:
    url: str
    jeton: str
    organisation: str
    delai_connexion_s: float = 10.0

    @classmethod
    def depuis_environnement(cls) -> ConfigRelais:
        manquantes = [n for n in ("MBILING_RELAIS_URL", "MBILING_RELAIS_JETON", "MBILING_ORGANISATION") if not os.environ.get(n)]
        if manquantes:
            raise MainsIndisponibles("Configuration des Mains incomplète : " + ", ".join(manquantes))
        return cls(
            url=os.environ["MBILING_RELAIS_URL"].rstrip("/"),
            jeton=os.environ["MBILING_RELAIS_JETON"],
            organisation=os.environ["MBILING_ORGANISATION"],
            delai_connexion_s=float(os.environ.get("MBILING_RELAIS_DELAI_S", "10")),
        )

    def est_complete(self) -> bool:
        return bool(self.url and self.jeton and self.organisation)


def _connexion(config: ConfigRelais, delai: float | None) -> tuple[http.client.HTTPConnection, str]:
    u = urlsplit(config.url)
    classe = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    return classe(u.hostname, u.port, timeout=delai), (u.path or "")


def inscrire_appareil(config: ConfigRelais, nom: str) -> dict:
    """Inscrit un PC de l'organisation auprès du Relais : renvoie {"appareil", "jeton"} (jeton rendu une fois)."""
    conn, base = _connexion(config, config.delai_connexion_s)
    try:
        corps = json.dumps({"organisation": config.organisation, "nom": nom}).encode("utf-8")
        conn.request("POST", f"{base}/interne/appareils", body=corps, headers={
            "Authorization": f"Bearer {config.jeton}", "Content-Type": "application/json"})
        rep = conn.getresponse()
        reponse = rep.read()
        if rep.status != 200:
            raise MainsIndisponibles(f"Relais : {rep.status} {reponse[:200].decode('utf-8', 'replace')}")
        return json.loads(reponse)
    except OSError as exc:
        raise MainsIndisponibles(f"Relais injoignable : {exc}") from exc
    finally:
        conn.close()


def info_appareil(config: ConfigRelais) -> dict:
    """Informations annoncées par le PC connecté (dossier de départ, système...)."""
    conn, base = _connexion(config, config.delai_connexion_s)
    try:
        conn.request("GET", f"{base}/interne/appareil/{config.organisation}", headers={"Authorization": f"Bearer {config.jeton}"})
        rep = conn.getresponse()
        corps = rep.read()
        if rep.status != 200:
            raise MainsIndisponibles(f"Relais : {rep.status} {corps[:200].decode('utf-8', 'replace')}")
        return json.loads(corps)
    except OSError as exc:
        raise MainsIndisponibles(f"Relais injoignable : {exc}") from exc
    finally:
        conn.close()


class CommandeDistante:
    """Poignée de processus (même forme que ``subprocess.Popen``) pour une commande exécutée
    sur le PC. La sortie arrive au fil de l'eau dans ``stdout`` ; ``kill()`` coupe le flux,
    ce qui fait annuler la commande sur le PC par le Relais."""

    def __init__(self, config: ConfigRelais, script: str, *, login: bool, timeout: int, stdin: str | None):
        self._returncode: int | None = None
        self._fini = threading.Event()
        self._conn: http.client.HTTPConnection | None = None
        lecture, ecriture = os.pipe()
        self._stdout = os.fdopen(lecture, "r", encoding="utf-8", errors="replace")
        self._ecriture = ecriture
        self._corps = json.dumps({
            "organisation": config.organisation, "script": script, "login": login,
            "timeout": timeout, "stdin": stdin,
        }).encode("utf-8")
        self._config = config
        threading.Thread(target=self._travail, daemon=True, name="mbiling-mains").start()

    def _ecrire(self, texte: str) -> None:
        try:
            os.write(self._ecriture, texte.encode("utf-8", errors="replace"))
        except OSError:
            pass

    def _travail(self) -> None:
        code = 1
        try:
            conn, base = _connexion(self._config, None)
            self._conn = conn
            conn.request("POST", f"{base}/interne/exec", body=self._corps, headers={
                "Authorization": f"Bearer {self._config.jeton}", "Content-Type": "application/json",
            })
            rep = conn.getresponse()
            if rep.status != 200:
                detail = rep.read()[:300].decode("utf-8", "replace")
                self._ecrire(f"[Mains] indisponibles ({rep.status}) : {detail}\n")
                code = 255
            else:
                while True:
                    ligne = rep.readline()
                    if not ligne:
                        code = 255 if self._returncode is None else code
                        break
                    message = json.loads(ligne)
                    if "d" in message:
                        self._ecrire(message["d"])
                    if "code" in message:
                        code = int(message["code"])
                        break
        except Exception as exc:  # coupure réseau, annulation (kill) ...
            if self._returncode is None:
                self._ecrire(f"[Mains] connexion interrompue : {exc}\n")
                code = 255
        finally:
            if self._returncode is None:
                self._returncode = code
            try:
                os.close(self._ecriture)
            except OSError:
                pass
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
            self._fini.set()

    @property
    def stdout(self):
        return self._stdout

    @property
    def returncode(self) -> int | None:
        return self._returncode if self._fini.is_set() else None

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        if self._fini.is_set():
            return
        self._returncode = 130
        conn = self._conn
        if conn is not None and conn.sock is not None:
            try:
                conn.sock.shutdown(2)
            except OSError:
                pass

    def wait(self, timeout: float | None = None) -> int | None:
        self._fini.wait(timeout=timeout)
        return self.returncode
