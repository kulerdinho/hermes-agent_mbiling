#!/usr/bin/env python3
"""Renommage Hermes -> Mbiling, niveau 1 (visible). Relançable sans effet de bord.

  python rebrand/appliquer.py --simulation   liste les changements, ne modifie rien
  python rebrand/appliquer.py                applique
  python rebrand/appliquer.py --verifier     échoue s'il reste une mention visible

Toutes les règles sont dans rebrand/regles.yaml. Dépendances : PyYAML, Pillow.
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parent.parent
REGLES = yaml.safe_load((RACINE / "rebrand" / "regles.yaml").read_text(encoding="utf-8"))


def fichiers_suivis() -> list[str]:
    out = subprocess.run(
        ["git", "-c", f"safe.directory={RACINE.as_posix()}", "ls-files"],
        cwd=RACINE, capture_output=True, text=True, check=True,
    ).stdout
    return out.splitlines()


def correspond(chemin: str, motifs: list[str]) -> bool:
    for m in motifs:
        if fnmatch.fnmatch(chemin, m) or fnmatch.fnmatch(chemin, m.replace("**/", "")):
            return True
    return False


def construire_motif() -> re.Pattern[str]:
    m = REGLES["marque"]
    avant = re.escape(m["caracteres_interdits_avant"])
    apres = re.escape(m["caracteres_interdits_apres"])
    mots = "|".join(re.escape(a) for a, _ in m["remplacements"])
    return re.compile(rf"(?<![\w{avant}])(?:{mots})(?![\w{apres}])")


MOTIF = construire_motif()
TABLE = {a: b for a, b in REGLES["marque"]["remplacements"]}
GUILLEMETS = ("'", '"', "`")
CHAINE = re.compile(
    r"'(?:\\.|[^'\\\n])*'"
    r'|"(?:\\.|[^"\\\n])*"'
    r"|`(?:\\.|[^`\\])*`",
    re.S,
)
JSX_TEXTE = re.compile(r">([^<>{}\n]*)<")
COMMENTAIRE = re.compile(r"^\s*(//|\*|/\*)")


def remplacer(texte: str) -> tuple[str, int]:
    return MOTIF.subn(lambda m: TABLE[m.group(0)], texte)


def traiter_texte(contenu: str) -> tuple[str, int]:
    return remplacer(contenu)


def traiter_chaines(contenu: str) -> tuple[str, int]:
    """Ne touche que l'intérieur des chaînes et le texte JSX, hors lignes de commentaire."""
    total = 0

    def dans_chaine(m: re.Match[str]) -> str:
        nonlocal total
        debut = contenu.rfind("\n", 0, m.start()) + 1
        if COMMENTAIRE.match(contenu[debut:m.start() + 1]):
            return m.group(0)
        nouveau, n = remplacer(m.group(0))
        total += n
        return nouveau

    contenu2 = CHAINE.sub(dans_chaine, contenu)

    def dans_jsx(m: re.Match[str]) -> str:
        nonlocal total
        nouveau, n = remplacer(m.group(1))
        total += n
        return ">" + nouveau + "<"

    lignes = []
    for ligne in contenu2.split("\n"):
        if "Hermes" in ligne and not COMMENTAIRE.match(ligne):
            ligne = JSX_TEXTE.sub(dans_jsx, ligne)
        lignes.append(ligne)
    return "\n".join(lignes), total


TRAITEMENTS = {"texte": traiter_texte, "chaines": traiter_chaines}


def restes_visibles(contenu: str, mode: str) -> list[tuple[int, str]]:
    exceptions = [re.compile(e) for e in REGLES.get("exceptions_verification", [])]
    apres, _ = TRAITEMENTS[mode](contenu)
    if apres == contenu:
        return []
    trouve = []
    for i, (a, b) in enumerate(zip(contenu.split("\n"), apres.split("\n")), 1):
        if a != b and not any(e.search(a) for e in exceptions):
            trouve.append((i, a.strip()[:140]))
    return trouve


def _poser(logo, taille, marge):
    from PIL import Image

    largeur, hauteur = taille
    cote = max(1, int(min(largeur, hauteur) * (1 - 2 * marge)))
    copie = logo.copy()
    copie.thumbnail((cote, cote), Image.LANCZOS)
    fond = Image.new("RGBA", (largeur, hauteur), (0, 0, 0, 0))
    fond.alpha_composite(copie, ((largeur - copie.width) // 2, (hauteur - copie.height) // 2))
    return fond


def traiter_images(simulation: bool, suivis: list[str]) -> int:
    conf = REGLES.get("images")
    if not conf:
        return 0
    from PIL import Image

    logo = Image.open(RACINE / conf["source"]).convert("RGBA")
    marge = float(conf.get("marge", 0))
    n = 0
    for motif in conf["cibles"]:
        for chemin in [f for f in suivis if fnmatch.fnmatch(f, motif)]:
            cible = RACINE / chemin
            n += 1
            if simulation:
                continue
            if cible.suffix == ".ico":
                _poser(logo, (256, 256), marge).save(
                    cible, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
                )
            else:
                with Image.open(cible) as actuelle:
                    taille = actuelle.size
                _poser(logo, taille, marge).save(cible)
    return n


def traiter_exacts(simulation: bool) -> int:
    n = 0
    for chemin, paires in (REGLES.get("exacts") or {}).items():
        fichier = RACINE / chemin
        contenu = fichier.read_bytes().decode("utf-8")
        nouveau = contenu
        for ancien, neuf in paires:
            n += nouveau.count(ancien)
            nouveau = nouveau.replace(ancien, neuf)
        if nouveau != contenu and not simulation:
            fichier.write_bytes(nouveau.encode("utf-8"))
    return n


def main() -> int:
    for flux in (sys.stdout, sys.stderr):
        flux.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--simulation", action="store_true")
    p.add_argument("--verifier", action="store_true")
    p.add_argument("--details", action="store_true", help="affiche chaque fichier concerné")
    args = p.parse_args()

    exclus = REGLES.get("exclusions", [])
    suivis = fichiers_suivis()
    bilan = {"texte": [0, 0], "chaines": [0, 0]}
    restes = 0
    for chemin in suivis:
        if correspond(chemin, exclus):
            continue
        if correspond(chemin, REGLES["texte"]):
            mode = "texte"
        elif correspond(chemin, REGLES["chaines"]):
            mode = "chaines"
        else:
            continue
        fichier = RACINE / chemin
        try:
            contenu = fichier.read_bytes().decode("utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        if "Hermes" not in contenu:
            continue
        if args.verifier:
            for no, ligne in restes_visibles(contenu, mode):
                restes += 1
                print(f"{chemin}:{no}: {ligne}")
            continue
        nouveau, n = TRAITEMENTS[mode](contenu)
        if n:
            bilan[mode][0] += 1
            bilan[mode][1] += n
            if args.details:
                print(f"{n:5d}  {chemin}")
            if not args.simulation:
                fichier.write_bytes(nouveau.encode("utf-8"))
    if args.verifier:
        print(f"{restes} mention(s) visible(s) restante(s)")
        return 1 if restes else 0
    exacts = traiter_exacts(args.simulation)
    images = traiter_images(args.simulation, suivis)
    mot = "à modifier" if args.simulation else "modifiés"
    for mode, (f, n) in bilan.items():
        print(f"{mode:8s}: {f} fichiers {mot}, {n} remplacements")
    print(f"exacts  : {exacts} remplacements")
    print(f"images  : {images} fichiers {mot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
