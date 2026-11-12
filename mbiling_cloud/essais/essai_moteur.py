#!/usr/bin/env python3
"""Essai du plugin ``mbiling_mains`` dans le vrai moteur (sans modèle d'IA).

Lance le Relais et le client des Mains en local, charge le plugin dans le moteur avec un
dossier de données temporaire, puis appelle les vrais outils ``terminal`` et fichiers.
À lancer depuis la racine du dépôt, avec l'interpréteur où le moteur est installé :

    python mbiling_cloud/essais/essai_moteur.py
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
PORT = int(os.environ.get("MBILING_ESSAI_PORT", "8720"))


def main() -> int:
    for flux in (sys.stdout, sys.stderr):
        flux.reconfigure(encoding="utf-8")
    travail = Path(tempfile.mkdtemp(prefix="mbiling-moteur-"))
    dossier = travail / "dossier-test"
    dossier.mkdir()
    foyer = travail / "donnees"
    foyer.mkdir()

    (foyer / "config.yaml").write_text(json.dumps({  # JSON est du YAML valide
        "plugins": {"enabled": ["mbiling-mains"], "isolation": "in_process"},
        "terminal": {"backend": "mbiling_mains", "timeout": 60},
        "approvals": {"mode": "manual"},
        "checkpoints": {"enabled": False},
    }), encoding="utf-8")

    jeton_interne = secrets.token_urlsafe(32)
    jeton_appareil = secrets.token_urlsafe(32)
    env = dict(os.environ, MBILING_RELAIS_JETON_INTERNE=jeton_interne, MBILING_JETON_APPAREIL_PC_TEST_1=jeton_appareil,
               MBILING_JETON_APPAREIL=jeton_appareil, MBILING_RELAIS_CONFIG=str(travail / "relais.yaml"))
    shutil.copy(RACINE / "mbiling_cloud/relais/config.exemple.yaml", travail / "relais.yaml")
    conf = json.loads((RACINE / "mbiling_mains_client/config.exemple.json").read_text(encoding="utf-8"))
    conf["relais_url"] = f"ws://127.0.0.1:{PORT}/mains"
    conf["politique"]["dossiers_autorises"] = [str(dossier)]
    (travail / "mains.json").write_text(json.dumps(conf), encoding="utf-8")

    # Ce que la cellule aura dans son environnement.
    os.environ.update(HERMES_HOME=str(foyer), TERMINAL_ENV="mbiling_mains",
                      MBILING_RELAIS_URL=f"http://127.0.0.1:{PORT}", MBILING_RELAIS_JETON=jeton_interne,
                      MBILING_ORGANISATION="org-test")
    sys.path.insert(0, str(RACINE))

    relais = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mbiling_cloud.relais.app:creer_app", "--factory",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"], cwd=RACINE, env=env)
    mains = None
    reussi = True

    def verifier(nom: str, condition: bool, detail: str = "") -> None:
        nonlocal reussi
        reussi = reussi and bool(condition)
        print(("OK    " if condition else "ÉCHEC ") + nom + (f"\n        {detail}" if detail and not condition else ""))

    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/sante", timeout=1).read()
                break
            except OSError:
                time.sleep(0.2)
        journal = open(travail / "mains.log", "w", encoding="utf-8")
        mains = subprocess.Popen(["node", str(RACINE / "mbiling_mains_client/mains.cjs"), str(travail / "mains.json")],
                                 env=env, stdout=journal, stderr=subprocess.STDOUT)
        time.sleep(2)

        from hermes_cli.plugins import discover_plugins, get_plugin_manager

        discover_plugins(force=True)
        from agent.terminal_env_registry import plugin_backend_names

        verifier("le moteur charge le plugin", "mbiling_mains" in plugin_backend_names(), str(plugin_backend_names()))
        etat = getattr(get_plugin_manager(), "_plugins", {}).get("mbiling-mains")
        if etat is not None and getattr(etat, "error", None):
            print("        erreur du plugin :", etat.error)

        from tools.terminal_tool import terminal_tool

        def depuis(resultat):
            return json.loads(resultat) if isinstance(resultat, str) else resultat

        r = depuis(terminal_tool(command="echo bonjour-du-pc && pwd && uname -s", task_id="essai"))
        verifier("outil terminal : la commande s'exécute sur le PC",
                 "bonjour-du-pc" in str(r.get("output", "")) and r.get("exit_code") == 0, json.dumps(r, ensure_ascii=False)[:600])

        from tools.file_tools import read_file_tool, search_tool, write_file_tool

        chemin = "note.txt"
        r = depuis(write_file_tool(path=chemin, content="première ligne\ndeuxième ligne : Mbiling\n", task_id="essai"))
        verifier("outil d'écriture de fichier", not r.get("error"), json.dumps(r, ensure_ascii=False)[:600])
        sur_disque = dossier / "note.txt"
        verifier("le fichier est bien sur le PC, avec le bon contenu",
                 sur_disque.exists() and "deuxième ligne : Mbiling" in sur_disque.read_text(encoding="utf-8"))

        r = depuis(read_file_tool(path=chemin, task_id="essai"))
        verifier("outil de lecture de fichier", "Mbiling" in json.dumps(r, ensure_ascii=False), json.dumps(r, ensure_ascii=False)[:600])

        r = depuis(search_tool(pattern="Mbiling", path=".", task_id="essai"))
        verifier("outil de recherche dans les fichiers", "note.txt" in json.dumps(r, ensure_ascii=False), json.dumps(r, ensure_ascii=False)[:600])

        r = depuis(terminal_tool(command="echo x > /c/Windows/mbiling-essai.txt", task_id="essai"))
        texte = json.dumps(r, ensure_ascii=False)
        verifier("écriture dans C:\\Windows : bloquée (par le moteur ou par le PC)",
                 not Path("C:/Windows/mbiling-essai.txt").exists() and r.get("exit_code") != 0, texte[:600])
        print("        bloquée par :", "le PC (politique locale)" if "refusée" in texte else "le moteur (approbation)")
    finally:
        for p in (mains, relais):
            if p is not None:
                p.terminate()
        shutil.rmtree(travail, ignore_errors=True)
    print("\nRÉSULTAT :", "tout passe" if reussi else "au moins un échec")
    return 0 if reussi else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
