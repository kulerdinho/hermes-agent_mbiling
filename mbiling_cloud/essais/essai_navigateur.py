#!/usr/bin/env python3
"""Essai de bout en bout du navigateur des Mains (lot 6), sur une seule machine.

Lance le Relais, le client des Mains et une petite page web locale, puis joue l'agent : il se
connecte au navigateur du PC À TRAVERS le Relais (l'adresse que la cellule met dans
``browser.cdp_url``), ouvre la page, clique, tape, lit, et mesure les délais. Vérifie aussi que
le moteur trouve ce navigateur (``resolve_cdp_override``) et qu'un site interdit est refusé par
le PC. À lancer depuis la racine du dépôt :

    python mbiling_cloud/essais/essai_navigateur.py [--visible]

Prérequis : ceux de essai_mains.py, plus Edge ou Chrome, et les dépendances de apps/desktop
(Playwright). Par défaut le navigateur est lancé sans fenêtre ; --visible montre la fenêtre.
"""
from __future__ import annotations

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
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
PORT = int(os.environ.get("MBILING_ESSAI_PORT", "8722"))

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Mbiling - essai du navigateur</title></head>
<body><h1>Page de l'essai</h1><p id="etat">avant</p>
<button id="bouton" onclick="document.getElementById('etat').textContent='cliqué'">Cliquer</button>
<input id="champ"></body></html>"""


def main() -> int:
    for flux in (sys.stdout, sys.stderr):
        flux.reconfigure(encoding="utf-8")
    visible = "--visible" in sys.argv
    travail = Path(tempfile.mkdtemp(prefix="mbiling-essai-nav-"))
    (travail / "site").mkdir()
    (travail / "site" / "index.html").write_text(PAGE, encoding="utf-8")
    serveur_page = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(travail / "site")))
    threading.Thread(target=serveur_page.serve_forever, daemon=True).start()
    page = f"http://127.0.0.1:{serveur_page.server_address[1]}/index.html"

    env = dict(os.environ)
    env["MBILING_RELAIS_JETON_INTERNE"] = secrets.token_urlsafe(32)
    env["MBILING_JETON_APPAREIL_PC_TEST_1"] = secrets.token_urlsafe(32)
    env["MBILING_JETON_APPAREIL"] = env["MBILING_JETON_APPAREIL_PC_TEST_1"]
    env["MBILING_RELAIS_CONFIG"] = str(travail / "relais.yaml")
    relais_conf = (RACINE / "mbiling_cloud/relais/config.exemple.yaml").read_text(encoding="utf-8")
    relais_conf = relais_conf.replace("/srv/mbiling/relais/appareils.json", str(travail / "appareils.json").replace("\\", "/"))
    (travail / "relais.yaml").write_text(relais_conf, encoding="utf-8")

    conf = json.loads((RACINE / "mbiling_mains_client/config.exemple.json").read_text(encoding="utf-8"))
    conf["relais_url"] = f"ws://127.0.0.1:{PORT}/mains"
    conf["politique"]["dossiers_autorises"] = [str(travail / "dossier-test")]
    conf["politique"]["sites_interdits"] = [r"^https?://([^/]*\.)?interdit\.test([/:?#]|$)"]
    conf["navigateur"] = {"profil": str(travail / "navigateur"), "arguments": [] if visible else ["--headless=new"]}
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
        for _ in range(50):
            sante = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/sante", timeout=2).read())
            if sante.get("appareils_connectes"):
                break
            time.sleep(0.2)

        cdp = f"http://127.0.0.1:{PORT}/cdp/{env['MBILING_RELAIS_JETON_INTERNE']}/org-test"

        # 1. La découverte vue par le moteur : la vraie fonction de la cellule.
        sys.path.insert(0, str(RACINE))
        from tools.browser_session_cdp import resolve_cdp_override
        debut = time.perf_counter()
        ws = resolve_cdp_override(cdp)
        lancement_ms = round((time.perf_counter() - debut) * 1000)
        verifier("le moteur trouve le navigateur du PC à travers le Relais",
                 ws.startswith(f"ws://127.0.0.1:{PORT}/cdp/") and "/devtools/browser/" in ws, ws.split("/cdp/")[0] + "/cdp/…")
        verifier("l'adresse donnée au moteur ne révèle pas le port local du navigateur", "127.0.0.1:" + str(PORT) in ws)

        mauvais = cdp.replace(env["MBILING_RELAIS_JETON_INTERNE"], "x" * 40)
        try:
            urllib.request.urlopen(mauvais + "/json/version", timeout=5)
            verifier("un mauvais jeton est refusé par le Relais", False)
        except urllib.error.HTTPError as exc:
            verifier("un mauvais jeton est refusé par le Relais", exc.code == 401)

        # 2. L'agent pilote le navigateur : ouvrir, lire, cliquer, taper.
        agent = subprocess.run(
            ["node", str(Path(__file__).with_name("essai_navigateur_agent.cjs")), cdp, page, "http://interdit.test/"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, check=False)
        sortie = (agent.stdout.strip().splitlines() or ["{}"])[-1]
        r = json.loads(sortie) if sortie.startswith("{") else {"erreur": agent.stdout + agent.stderr}
        verifier("l'agent ouvre la page et lit son titre", r.get("titre") == "Mbiling - essai du navigateur", r.get("titre") or r.get("erreur", ""))
        verifier("l'agent clique sur un bouton de la page", r.get("apres_clic") == "cliqué")
        verifier("l'agent tape dans un champ", r.get("texte_tape") == "Bonjour depuis le Cloud")
        verifier("un site interdit est refusé par le PC", "interdit" in str(r.get("interdit", "")).lower(), r.get("interdit", ""))
        journal.flush()
        verifier("le refus est inscrit dans le journal local", "REFUS" in (travail / "mains.log").read_text(encoding="utf-8"))

        mesures = r.get("mesures_ms", {})
        print("\nDélais mesurés (ms) :")
        print(f"  lancement du navigateur + découverte : {lancement_ms}")
        for nom, valeur in mesures.items():
            print(f"  {nom} : {valeur}")
    finally:
        if mains:
            mains.terminate()
            mains.wait(10)
        relais.terminate()
        relais.wait(10)
        serveur_page.shutdown()
        time.sleep(1)
        shutil.rmtree(travail, ignore_errors=True)
    print("\nRÉSULTAT : " + ("tout passe" if reussi else "au moins un échec"))
    return 0 if reussi else 1


if __name__ == "__main__":
    sys.exit(main())
