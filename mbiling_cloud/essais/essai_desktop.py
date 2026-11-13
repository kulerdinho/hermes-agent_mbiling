#!/usr/bin/env python3
"""Essai de bout en bout des Mains DANS Mbiling Desktop, sur une seule machine.

Lance le Relais en local, puis la vraie application empaquetée (Mbiling.exe) dans un bac à sable.
Le jeton d'appareil est enregistré par le pont de l'application (celui de la page « Ce PC ») ;
des commandes sont ensuite envoyées par le transport du plugin (le même code que la cellule).
À lancer depuis la racine du dépôt, après la construction du Desktop :

    python mbiling_cloud/essais/essai_desktop.py [chemin/vers/Mbiling.exe]

Prérequis : ceux de essai_mains.py, plus Mbiling Desktop construit (npm run pack ou builder).
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
import threading
import time
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
PORT = int(os.environ.get("MBILING_ESSAI_PORT", "8721"))
EXE_DEFAUT = RACINE / "apps/desktop/release/win-unpacked/Mbiling.exe"


def charger_transport():
    spec = importlib.util.spec_from_file_location("mbiling_transport", RACINE / "plugins/mbiling_mains/transport.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    for flux in (sys.stdout, sys.stderr):
        flux.reconfigure(encoding="utf-8")
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else EXE_DEFAUT
    if not exe.exists():
        print(f"Mbiling Desktop introuvable : {exe}")
        return 2
    travail = Path(tempfile.mkdtemp(prefix="mbiling-essai-desktop-"))
    donnees = travail / "donnees"
    dossier = travail / "Mbiling-Test"
    captures = Path(os.environ.get("MBILING_ESSAI_CAPTURES", str(travail / "captures")))
    donnees.mkdir()
    env = dict(os.environ)
    env["MBILING_RELAIS_JETON_INTERNE"] = secrets.token_urlsafe(32)
    env["MBILING_JETON_APPAREIL_PC_TEST_1"] = secrets.token_urlsafe(32)
    env["MBILING_RELAIS_CONFIG"] = str(travail / "relais.yaml")
    shutil.copy(RACINE / "mbiling_cloud/relais/config.exemple.yaml", travail / "relais.yaml")

    # Réglages de l'application : ceux livrés, avec le Relais local et le dossier de l'essai.
    reglages = json.loads((RACINE / "apps/desktop/config/mains.json").read_text(encoding="utf-8"))
    reglages["relais_url"] = f"ws://127.0.0.1:{PORT}/mains"
    reglages["actif"] = True  # l'utilisateur a autorisé l'agent à utiliser ce PC (case cochée)
    (donnees / "mains.json").write_text(json.dumps(reglages), encoding="utf-8")
    politique = json.loads((RACINE / "apps/desktop/config/mains-politique.json").read_text(encoding="utf-8"))
    politique["dossiers_autorises"] = [str(dossier)]
    (donnees / "mains-politique.json").write_text(json.dumps(politique), encoding="utf-8")

    relais = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mbiling_cloud.relais.app:creer_app", "--factory",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=RACINE, env=env)
    pilote = None
    reussi = True
    lignes: list[str] = []
    pret = threading.Event()

    def verifier(nom: str, condition: bool, detail: str = "") -> None:
        nonlocal reussi
        reussi = reussi and bool(condition)
        print(("OK    " if condition else "ÉCHEC ") + nom + (f"  ({detail})" if detail else ""))

    def lire_pilote() -> None:
        for ligne in pilote.stdout:
            lignes.append(ligne.rstrip("\n"))
            if ligne.startswith(("PRET", "ECHEC_CONNEXION", "ERREUR")):
                pret.set()

    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/sante", timeout=1).read()
                break
            except OSError:
                time.sleep(0.2)
        env_pilote = dict(env)
        env_pilote["MBILING_ESSAI_JETON"] = env["MBILING_JETON_APPAREIL_PC_TEST_1"]
        pilote = subprocess.Popen(
            ["node", str(Path(__file__).with_name("essai_desktop_pilote.cjs")), str(exe), str(donnees), str(captures)],
            env=env_pilote, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace")
        threading.Thread(target=lire_pilote, daemon=True).start()
        pret.wait(180)

        ecran = json.loads(next((l for l in lignes if l.startswith("PREMIER_LANCEMENT ")), "PREMIER_LANCEMENT {}")[18:] or "{}")
        verifier("le premier lancement n'affiche qu'un bouton « Se connecter », sans champ à remplir",
                 ecran.get("boutonConnexion") is True and ecran.get("champs") == 0, json.dumps(ecran))
        verifier("l'adresse du serveur vient du fichier de réglages", ecran.get("adresseParDefaut") == "https://api.mbiling.com")
        avant = next((l for l in lignes if l.startswith("ETAT_AVANT ")), "")
        verifier("sans jeton, les Mains attendent", '"jetonDefini":false' in avant and '"enMarche":false' in avant)
        verifier("après saisie du jeton, le Desktop se connecte au Relais", any(l == "PRET" for l in lignes),
                 next((l for l in lignes if l.startswith("ETAT ")), "")[:200])
        etat = next((l for l in lignes if l.startswith("ETAT ")), "")
        verifier("le bash livré avec l'application est utilisé", "resources\\\\git\\\\bin\\\\bash.exe" in etat, etat[:300])
        jeton_clair = env["MBILING_JETON_APPAREIL_PC_TEST_1"].encode()
        scelle = (donnees / "mains-jeton.bin")
        verifier("le jeton est conservé chiffré, jamais en clair", scelle.exists() and jeton_clair not in scelle.read_bytes())

        t = charger_transport()
        cfg = t.ConfigRelais(url=f"http://127.0.0.1:{PORT}", jeton=env["MBILING_RELAIS_JETON_INTERNE"], organisation="org-test")

        def lancer(script: str, stdin: str | None = None, timeout: int = 30):
            return t.CommandeDistante(cfg, script, login=False, timeout=timeout, stdin=stdin)

        c = lancer("echo bonjour depuis le Desktop; pwd")
        sortie = c.stdout.read()
        verifier("une commande de l'agent s'exécute sur le PC, dans le dossier autorisé",
                 "bonjour depuis le Desktop" in sortie and "Mbiling-Test" in sortie and c.wait(10) == 0, sortie.strip()[:400])

        c = lancer("date +%F > rapport.txt; cat rapport.txt")
        sortie = c.stdout.read()
        verifier("le fichier créé par l'agent apparaît sur le PC", (dossier / "rapport.txt").exists() and c.wait(10) == 0)

        c = lancer("echo test > /c/Windows/mbiling.txt")
        sortie = c.stdout.read()
        verifier("écriture dans C:\\Windows refusée par le PC", "refusée" in sortie and c.wait(10) == 126)
        journal = donnees / "logs" / "mains.log"
        verifier("le refus est inscrit dans le journal local", journal.exists() and "REFUS" in journal.read_text(encoding="utf-8"))

        # La page « Ce PC (Mains) » n'est pas vérifiée ici : tant qu'aucun serveur n'est connecté,
        # l'écran de connexion du premier lancement couvre l'application. Elle est couverte par
        # apps/desktop/src/app/settings/mains-settings.test.tsx.
    finally:
        if pilote and pilote.poll() is None:
            try:
                pilote.stdin.write("FIN\n")
                pilote.stdin.flush()
                pilote.wait(30)
            except Exception:
                pilote.kill()
        relais.terminate()
        relais.wait(10)
        if not reussi:
            # Diagnostic complet : sans lui, un échec rare ne peut pas être expliqué après coup.
            print("\n--- sortie du pilote ---")
            print("\n".join(lignes[-60:]))
            journal = donnees / "logs" / "mains.log"
            if journal.exists():
                print("--- journal des Mains (fin) ---")
                print("\n".join(journal.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]))
    print(f"\nCaptures : {captures}")
    print("RÉSULTAT : " + ("tout passe" if reussi else "au moins un échec"))
    return 0 if reussi else 1


if __name__ == "__main__":
    sys.exit(main())
