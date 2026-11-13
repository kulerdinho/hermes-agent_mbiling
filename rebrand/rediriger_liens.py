#!/usr/bin/env python3
"""Remplace les adresses du site de Nous Research par celles du site Mbiling.

  python rebrand/rediriger_liens.py --simulation   compte, ne modifie rien
  python rebrand/rediriger_liens.py                applique, puis écrit rebrand/liens-mbiling.md

Le domaine et les règles sont dans rebrand/regles.yaml (section « liens »).
liens-mbiling.md liste chaque adresse Mbiling utilisée : ce sont les pages et
fichiers que le site devra servir. Relançable sans effet de bord.
"""
from __future__ import annotations

import argparse
import collections
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parent.parent
LIENS = yaml.safe_load((RACINE / "rebrand" / "regles.yaml").read_text(encoding="utf-8"))["liens"]
DOMAINE = LIENS["domaine"]
REGLES = [
    (re.compile(a.encode("utf-8"), re.IGNORECASE), b.replace("{domaine}", DOMAINE).encode("utf-8"))
    for a, b in LIENS["redirections"]
]
URL_MBILING = re.compile(
    rb"(?:https?://)?(?:[a-z0-9-]+\.)?" + re.escape(DOMAINE.encode()) + rb"(?:/[^\s\"'<>\)\]}`|^,;]*)?", re.IGNORECASE
)


def exclu(chemin: str) -> bool:
    return any(fnmatch.fnmatch(chemin, m) or fnmatch.fnmatch(chemin, m.replace("/**", "/*")) for m in LIENS.get("exclusions", []))


def fichiers_suivis() -> list[str]:
    out = subprocess.run(
        ["git", "-c", f"safe.directory={RACINE.as_posix()}", "-c", "core.quotepath=off", "ls-files", "-z"],
        cwd=RACINE, capture_output=True, check=True,
    ).stdout
    return [p.decode("utf-8") for p in out.split(b"\0") if p]


def nature(url: str) -> str:
    chemin = re.sub(r"^(?:https?://)?", "", url)
    hote, _, reste = chemin.partition("/")
    if hote.startswith("assets."):
        return "Téléchargements (assets." + DOMAINE + ")"
    if hote.startswith("setup."):
        return "Service d'inscription des bots Telegram (setup." + DOMAINE + ")"
    if re.search(r"\.(sh|ps1|cmd)$", reste):
        return "Scripts d'installation"
    if re.search(r"\.(json|txt|xml|yaml|yml)$", reste):
        return "Fichiers de données lus par l'application"
    return "Pages du site"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--simulation", action="store_true")
    args = p.parse_args()
    nb_f = nb_r = 0
    inventaire: dict[str, list] = {}
    for chemin in fichiers_suivis():
        if exclu(chemin):
            continue
        f = RACINE / chemin
        if f.is_symlink() or not f.is_file():
            continue
        d = f.read_bytes()
        if b"\0" in d[:8192]:
            continue
        n_total = 0
        for motif, neuf in REGLES:
            d, n = motif.subn(neuf, d)
            n_total += n
        if n_total:
            nb_f += 1
            nb_r += n_total
            if not args.simulation:
                f.write_bytes(d)
        essai = chemin.startswith(("tests/", "tests-js/")) or ".test." in chemin or ".spec." in chemin or "/e2e/" in chemin
        if essai:
            continue
        for m in URL_MBILING.finditer(d):
            u = m.group(0).decode("utf-8", "replace").rstrip(".:")
            u = re.sub(r"^(?:https?://)?", "https://", u)
            if "${" in u or "{" in u:
                continue
            e = inventaire.setdefault(u, [0, chemin])
            e[0] += 1
    print(f"{nb_f} fichiers {'à modifier' if args.simulation else 'modifiés'}, {nb_r} adresses remplacées")
    if args.simulation:
        return 0
    groupes: dict[str, list] = collections.defaultdict(list)
    for u, (n, ex) in sorted(inventaire.items()):
        groupes[nature(u)].append((u, n, ex))
    lignes = [
        "# Adresses du site Mbiling utilisées par l'application",
        "",
        f"Généré par `rebrand/rediriger_liens.py` (domaine : `{DOMAINE}`, réglé dans `rebrand/regles.yaml`).",
        "Ces adresses remplacent celles de l'ancien éditeur. Tant que le site n'existe pas, elles",
        "doivent afficher une page « Site en cours de développement ». Chaque ligne indique combien de",
        "fois l'adresse apparaît et un fichier où elle est utilisée (hors tests).",
        "",
    ]
    for g in sorted(groupes):
        lignes += [f"## {g} ({len(groupes[g])})", "", "| Adresse | Occurrences | Exemple d'utilisation |", "|---|---|---|"]
        lignes += [f"| {u} | {n} | `{ex}` |" for u, n, ex in groupes[g]]
        lignes.append("")
    (RACINE / "rebrand" / "liens-mbiling.md").write_text("\n".join(lignes), encoding="utf-8", newline="\n")
    print(f"liste écrite : rebrand/liens-mbiling.md ({len(inventaire)} adresses)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
