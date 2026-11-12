#!/usr/bin/env python3
"""Renommage Hermes -> Mbiling, niveau 2 (profond) : contenus ET chemins.

  python rebrand/appliquer_profond.py --simulation   compte, ne modifie rien
  python rebrand/appliquer_profond.py                applique
  python rebrand/appliquer_profond.py --verifier     échoue s'il reste une mention à renommer

Règles dans rebrand/regles.yaml (section « profond »). Relançable sans effet de bord.

Principe : tout « hermes » est renommé, SAUF ce qui appartient à quelqu'un d'autre et
casserait en changeant de nom :
  - les adresses (URL) de sites externes sont laissées telles quelles, octet pour octet ;
  - les identifiants externes listés dans « proteges » (paquets tiers, modèles d'IA,
    identifiants enregistrés auprès de services, indicateurs de sécurité) ;
  - les fichiers listés dans « exclusions » (mentions légales, attribution).
Les adresses du dépôt d'origine sont redirigées vers le dépôt Mbiling (« speciaux »).
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parent.parent
FICHIER_REGLES = Path(os.environ.get("MBILING_REBRAND_REGLES") or (RACINE / "rebrand" / "regles.yaml"))
REGLES = yaml.safe_load(FICHIER_REGLES.read_text(encoding="utf-8"))["profond"]


def _motifs(cle: str, source: dict = REGLES) -> list[re.Pattern[bytes]]:
    return [re.compile(str(m).encode("utf-8"), re.IGNORECASE) for m in (source.get(cle) or [])]


SPECIAUX = [(re.compile(str(a).encode("utf-8"), re.IGNORECASE), str(b).encode("utf-8")) for a, b in REGLES.get("speciaux", [])]
PROTEGES = "|".join(f"(?:{p})" for p in REGLES.get("proteges", []))
MOTIF = re.compile((f"(?P<garde>{PROTEGES})|hermes" if PROTEGES else "hermes").encode("utf-8"), re.IGNORECASE)
CASSE = {b"hermes": b"mbiling", b"Hermes": b"Mbiling", b"HERMES": b"MBILING"}
GLYPHES = [(str(a).encode("utf-8"), str(b).encode("utf-8")) for a, b in REGLES.get("glyphes", [])]

_URLS = REGLES.get("urls") or {}
SCHEMAS_INTERNES = _motifs("schemas_internes", _URLS)
HOTES_INTERNES = _motifs("hotes_internes", _URLS)
AMONT = _motifs("amont", _URLS)
_SANS_SCHEMA = "|".join(f"(?:{h})" for h in (_URLS.get("hotes_sans_schema") or []))
_FIN_URL = r"[^\s\"'<>\\)\]}`|^]"
URL = re.compile(
    (
        r"(?P<schema>[A-Za-z][A-Za-z0-9+.-]*)://(?P<reste>" + _FIN_URL + r"+)"
        + (r"|(?<![A-Za-z0-9.@/_-])(?P<nu>(?:" + _SANS_SCHEMA + r")/" + _FIN_URL + r"*)" if _SANS_SCHEMA else "")
    ).encode("utf-8"),
    re.IGNORECASE,
)


def _un(m: re.Match[bytes]) -> bytes:
    if m.groupdict().get("garde"):
        return m.group(0)
    return CASSE.get(m.group(0), b"mbiling")


def _texte(donnees: bytes) -> tuple[bytes, int]:
    """Renomme un morceau de texte qui n'est pas une adresse externe."""
    total = 0
    for motif, neuf in SPECIAUX:
        donnees, n = motif.subn(neuf, donnees)
        total += n
    compte = 0

    def f(m: re.Match[bytes]) -> bytes:
        nonlocal compte
        r = _un(m)
        if r != m.group(0):
            compte += 1
        return r

    donnees = MOTIF.sub(f, donnees)
    return donnees, total + compte


def classe_url(schema: bytes | None, reste: bytes) -> str:
    """« interne » (renommée), « amont » (redirigée vers le dépôt Mbiling) ou « externe » (intacte)."""
    if schema is not None and any(s.fullmatch(schema) for s in SCHEMAS_INTERNES):
        return "interne"
    hote = re.split(rb"[/?#]", reste, maxsplit=1)[0].rsplit(b"@", 1)[-1]
    hote = re.sub(rb":\d+$", b"", hote)
    if any(h.fullmatch(hote) for h in HOTES_INTERNES):
        return "interne"
    if any(a.match(reste) for a in AMONT):
        return "amont"
    return "externe"


def remplacer(donnees: bytes) -> tuple[bytes, int]:
    if b"hermes" not in donnees.lower() and not any(a in donnees for a, _ in GLYPHES):
        return donnees, 0
    morceaux: list[bytes] = []
    total = 0
    pos = 0
    for m in URL.finditer(donnees):
        url = m.group(0)
        if b"hermes" not in url.lower():
            continue
        avant, n = _texte(donnees[pos:m.start()])
        morceaux.append(avant)
        total += n
        if m.group("schema") is not None:
            classe = classe_url(m.group("schema"), m.group("reste"))
        else:
            classe = classe_url(None, m.group("nu"))
        if classe == "externe":
            morceaux.append(url)
        else:
            neuf, n = _texte(url)
            morceaux.append(neuf)
            total += n
        pos = m.end()
    fin, n = _texte(donnees[pos:])
    morceaux.append(fin)
    total += n
    resultat = b"".join(morceaux)
    for ancien, neuf in GLYPHES:
        if ancien in resultat:
            total += resultat.count(ancien)
            resultat = resultat.replace(ancien, neuf)
    return resultat, total


def remplacer_chemin(chemin: str) -> str:
    return _texte(chemin.encode("utf-8"))[0].decode("utf-8")


def exclu(chemin: str) -> bool:
    for m in REGLES.get("exclusions", []):
        if fnmatch.fnmatch(chemin, m) or fnmatch.fnmatch(chemin, m.replace("/**", "/*")):
            return True
    return False


def fichiers_suivis() -> list[str]:
    out = subprocess.run(
        ["git", "-c", f"safe.directory={RACINE.as_posix()}", "-c", "core.quotepath=off", "ls-files", "-z"],
        cwd=RACINE, capture_output=True, check=True,
    ).stdout
    return [p.decode("utf-8") for p in out.split(b"\0") if p]


def main() -> int:
    for flux in (sys.stdout, sys.stderr):
        flux.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--simulation", action="store_true")
    p.add_argument("--verifier", action="store_true")
    args = p.parse_args()
    lecture_seule = args.simulation or args.verifier

    nb_fichiers = nb_rempl = nb_chemins = 0
    restes: list[str] = []
    anciens_dossiers: set[Path] = set()
    for chemin in fichiers_suivis():
        if exclu(chemin):
            continue
        fichier = RACINE / chemin
        if fichier.is_symlink() or not fichier.is_file():
            continue
        donnees = fichier.read_bytes()
        if b"\0" not in donnees[:8192]:
            nouveau, n = remplacer(donnees)
            if n:
                nb_fichiers += 1
                nb_rempl += n
                if args.verifier:
                    restes.append(f"contenu : {chemin} ({n})")
                if not lecture_seule:
                    fichier.write_bytes(nouveau)
        nouveau_chemin = remplacer_chemin(chemin)
        if nouveau_chemin != chemin:
            nb_chemins += 1
            if args.verifier:
                restes.append(f"chemin  : {chemin}")
            if not lecture_seule:
                cible = RACINE / nouveau_chemin
                cible.parent.mkdir(parents=True, exist_ok=True)
                os.replace(fichier, cible)
                anciens_dossiers.add(fichier.parent)
    # Supprime les anciens dossiers devenus vides.
    for d in sorted(anciens_dossiers, key=lambda x: -len(x.parts)):
        while d != RACINE and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent
    if args.verifier:
        for r in restes[:200]:
            print(r)
        print(f"{len(restes)} reste(s)")
        return 1 if restes else 0
    mot = "à modifier" if args.simulation else "modifiés"
    print(f"contenus : {nb_fichiers} fichiers {mot}, {nb_rempl} remplacements")
    print(f"chemins  : {nb_chemins} fichiers {'à renommer' if args.simulation else 'renommés'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
