#!/usr/bin/env python3
"""Essai de bout en bout des Mains, sur une seule machine.

Lance le Relais et le client des Mains en local, puis envoie de vraies commandes par le
transport du plugin (le même code que la cellule). À lancer depuis la racine du dépôt :

    python mbiling_cloud/essais/essai_mains.py

Prérequis : Python (fastapi, uvicorn, websockets, pyyaml), Node.js 22 ou plus, bash.
"""
from __future__ import annotations

import importlib.util
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
PORT = int(os.environ.get("MBILING_ESSAI_PORT", "8719"))


def charger_transport():
    spec = importlib.util.spec_from_file_location("mbiling_transport", RACINE / "plugins/mbiling_mains/transport.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def processus_sleep() -> int:
    if os.name != "nt":
        return 0
    sortie = subprocess.run(["tasklist"], capture_output=True, text=True, errors="replace").stdout
    return sum(1 for ligne in sortie.splitlines() if ligne.lower().startswith("sleep.exe"))


def main() -> int:
    for flux in (sys.stdout, sys.stderr):
        flux.reconfigure(encoding="utf-8")
    travail = Path(tempfile.mkdtemp(prefix="mbiling-essai-"))
    dossier = travail / "dossier-test"
    dossier.mkdir()
    env = dict(os.environ)
    env["MBILING_RELAIS_JETON_INTERNE"] = secrets.token_urlsafe(32)
    env["MBILING_JETON_APPAREIL_PC_TEST_1"] = secrets.token_urlsafe(32)
    env["MBILING_JETON_APPAREIL"] = env["MBILING_JETON_APPAREIL_PC_TEST_1"]
    env["MBILING_RELAIS_CONFIG"] = str(travail / "relais.yaml")
    shutil.copy(RACINE / "mbiling_cloud/relais/config.exemple.yaml", travail / "relais.yaml")
    conf = json.loads((RACINE / "mbiling_mains_client/config.exemple.json").read_text(encoding="utf-8"))
    conf["relais_url"] = f"ws://127.0.0.1:{PORT}/mains"
    conf["politique"]["dossiers_autorises"] = [str(dossier)]
    (travail / "mains.json").write_text(json.dumps(conf), encoding="utf-8")

    relais = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mbiling_cloud.relais.app:creer_app", "--factory",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=RACINE, env=env)
    mains = None
    reussi = True

    def verifier(nom: str, condition: bool, detail: str = "") -> None:
        nonlocal reussi
        reussi = reussi and bool(condition)
        print(("OK    " if condition else "ÉCHEC ") + nom + (f"  ({detail})" if detail else ""))

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
        t = charger_transport()
        cfg = t.ConfigRelais(url=f"http://127.0.0.1:{PORT}", jeton=env["MBILING_RELAIS_JETON_INTERNE"], organisation="org-test")
        info = None
        for _ in range(50):
            try:
                info = t.info_appareil(cfg)
                break
            except t.MainsIndisponibles:
                time.sleep(0.2)
        verifier("le PC se connecte et annonce son dossier de départ", bool(info and info["info"].get("dossier_depart")))

        def lancer(script: str, stdin: str | None = None, timeout: int = 30):
            return t.CommandeDistante(cfg, script, login=False, timeout=timeout, stdin=stdin)

        c = lancer("echo bonjour; pwd")
        sortie = c.stdout.read()
        verifier("commande simple, dans le dossier autorisé", "bonjour" in sortie and "dossier-test" in sortie and c.wait(5) == 0)

        c = lancer("cat > rapport.txt; cat rapport.txt; exit 3", stdin="ligne écrite à distance\n")
        sortie = c.stdout.read()
        verifier("entrée standard et code de retour", "écrite à distance" in sortie and c.wait(5) == 3)
        verifier("le fichier existe sur le PC", (dossier / "rapport.txt").exists())

        # Le script vit dans le dossier de travail du client : il l'efface lui-même, comme Windows.
        c = lancer('rm -rf "$(dirname "$0")"')
        c.stdout.read()
        c.wait(5)
        c = lancer("echo encore")
        sortie = c.stdout.read()
        verifier("le dossier temporaire du PC effacé par Windows : la commande passe quand même",
                 "encore" in sortie and c.wait(5) == 0)

        gros = "x=0\n" + "x=$((x+1))\n" * 6000 + "echo total=$x\n"
        c = lancer(gros)
        sortie = c.stdout.read()
        verifier("script de plus de 32 Ko", "total=6000" in sortie and c.wait(5) == 0, f"{len(gros)} caractères")

        c = lancer("echo test > /c/Windows/mbiling.txt")
        sortie = c.stdout.read()
        verifier("écriture dans C:\\Windows refusée par le PC", "refusée" in sortie and c.wait(5) == 77)

        c = lancer("for i in 1 2 3; do echo tic $i; sleep 1; done")
        debut = time.time()
        premiere = c.stdout.readline()
        verifier("la sortie arrive en direct", time.time() - debut < 1.5 and "tic 1" in premiere, f"{time.time() - debut:.2f} s")
        c.stdout.read()
        c.wait(10)

        avant = processus_sleep()
        c = lancer("echo debut; sleep 60; echo jamais")
        c.stdout.readline()
        c.kill()
        verifier("annulation côté cellule", c.wait(10) == 130)
        time.sleep(2)
        verifier("aucun processus orphelin sur le PC après annulation", processus_sleep() == avant)
        journal.flush()
        verifier("le PC a bien terminé la commande annulée", "ANNULER" in (travail / "mains.log").read_text(encoding="utf-8"))

        mauvais = t.ConfigRelais(url=cfg.url, jeton="x" * 30, organisation="org-test")
        try:
            t.info_appareil(mauvais)
            verifier("mauvais jeton interne refusé", False)
        except t.MainsIndisponibles as exc:
            verifier("mauvais jeton interne refusé", "401" in str(exc))

        autre = t.ConfigRelais(url=cfg.url, jeton=cfg.jeton, organisation="autre-org")
        c = t.CommandeDistante(autre, "echo x", login=False, timeout=5, stdin=None)
        sortie = c.stdout.read()
        verifier("organisation sans appareil : erreur propre", c.wait(5) == 255 and "indisponibles" in sortie)
    finally:
        for p in (mains, relais):
            if p is not None:
                p.terminate()
        shutil.rmtree(travail, ignore_errors=True)
    print("\nRÉSULTAT :", "tout passe" if reussi else "au moins un échec")
    return 0 if reussi else 1


if __name__ == "__main__":
    sys.exit(main())
