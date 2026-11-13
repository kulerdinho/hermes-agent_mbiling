"""Corrige « Mbiling' » (ancien possessif « Hermes' ») en « Mbiling's ».

Possessif = « Mbiling' » suivi d'une espace puis d'une lettre, sans guillemet juste avant le mot
(sinon c'est la fin d'une chaîne : 'Mbiling', 'Updating Mbiling' : ...). Les cas où le nombre de
guillemets simples avant le mot sur la ligne est impair (chaîne peut-être ouverte) sont listés
à part et NON modifiés, pour vérification à la main.

À relancer après rebrand/appliquer_profond.py, à chaque import d'une nouvelle version :
    python rebrand/possessifs.py               simulation
    python rebrand/possessifs.py --appliquer
"""
import re
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
APPLIQUER = "--appliquer" in sys.argv
MOTIF = re.compile(r"(?<![\w'])(Mbiling|MBILING)'(?= [A-Za-z])")
EXCLUS = ("rebrand/", "contributors/", "LICENSE", "NOTICE")

sys.stdout.reconfigure(encoding="utf-8")
fichiers = subprocess.run(["git", "-c", "core.quotepath=off", "ls-files", "-z"], cwd=RACINE,
                          capture_output=True, check=True).stdout.split(b"\0")
total = douteux = 0
for brut in fichiers:
    chemin = brut.decode("utf-8")
    if not chemin or chemin.startswith(EXCLUS):
        continue
    f = RACINE / chemin
    try:
        donnees = f.read_bytes()
    except OSError:
        continue
    if b"\0" in donnees[:8192] or b"Mbiling' " not in donnees and b"MBILING' " not in donnees:
        continue
    texte = donnees.decode("utf-8", "replace")
    lignes = texte.split("\n")
    change = False
    for i, ligne in enumerate(lignes):
        def remplace(m):
            global total, douteux
            avant = ligne[:m.start()]
            # Apostrophes typographiques des mots (n't, l'…) mises à part, compter les guillemets ouvrants.
            nb = len(re.findall(r"(?<![A-Za-z])'|'(?![A-Za-z])", avant))
            # Citation courte : 'Hey Mbiling', 'Reopen Mbiling', 'not part of Mbiling' (le guillemet
            # ouvrant est à quelques mots du nom, sans ponctuation entre les deux).
            citation = re.search(r"(?<![A-Za-z])'[A-Za-z][A-Za-z ]{0,30}$", avant)
            if nb % 2 == 1 and citation:
                douteux += 1
                print(f"DOUTEUX {chemin}:{i + 1}: {ligne.strip()[:150]}")
                return m.group(0)
            total += 1
            return m.group(1) + "'s"
        nouvelle = MOTIF.sub(remplace, ligne)
        if nouvelle != ligne:
            lignes[i] = nouvelle
            change = True
    if change and APPLIQUER:
        f.write_bytes("\n".join(lignes).encode("utf-8"))
print(f"{total} possessifs {'corrigés' if APPLIQUER else 'à corriger'}, {douteux} cas douteux laissés tels quels")
