"""Données de Management : réglages (secrets chiffrés) et journal d'audit inaltérable.

SQLite, dans le volume de données du service. Le journal ne peut qu'être complété : des déclencheurs
refusent toute modification ou suppression, et chaque ligne porte l'empreinte de la précédente
(chaîne SHA-256), si bien qu'une altération faite hors de l'application se voit à la vérification.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reglages (
    cle TEXT PRIMARY KEY,
    valeur TEXT NOT NULL,
    secret BLOB,
    modifie_par TEXT NOT NULL,
    modifie_le REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quand REAL NOT NULL,
    qui TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL,
    ip TEXT,
    precedent TEXT NOT NULL,
    empreinte TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS journal_sans_modification BEFORE UPDATE ON journal
BEGIN SELECT RAISE(ABORT, 'journal inaltérable'); END;
CREATE TRIGGER IF NOT EXISTS journal_sans_suppression BEFORE DELETE ON journal
BEGIN SELECT RAISE(ABORT, 'journal inaltérable'); END;
"""

_ORIGINE = "0" * 64


def _empreinte(precedent: str, quand: float, qui: str, action: str, detail: str, ip: str | None) -> str:
    contenu = json.dumps([precedent, quand, qui, action, detail, ip], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(contenu.encode("utf-8")).hexdigest()


class Stockage:
    def __init__(self, chemin: Path, fernet: Fernet) -> None:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        self._fernet = fernet
        self._verrou = threading.Lock()
        self._db = sqlite3.connect(str(chemin), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    # --- Réglages -------------------------------------------------------------------------------

    def lire(self, cle: str) -> dict[str, Any] | None:
        ligne = self._db.execute("SELECT valeur, modifie_par, modifie_le FROM reglages WHERE cle = ?", (cle,)).fetchone()
        if ligne is None:
            return None
        return {**json.loads(ligne[0]), "_modifie_par": ligne[1], "_modifie_le": ligne[2]}

    def secret(self, cle: str) -> str | None:
        ligne = self._db.execute("SELECT secret FROM reglages WHERE cle = ?", (cle,)).fetchone()
        if ligne is None or ligne[0] is None:
            return None
        return self._fernet.decrypt(ligne[0]).decode("utf-8")

    def ecrire(self, cle: str, valeur: dict[str, Any], par: str, secret: str | None = None) -> None:
        """Enregistre ``valeur`` ; ``secret`` remplace le secret chiffré, ``None`` garde l'ancien."""
        chiffre = self._fernet.encrypt(secret.encode("utf-8")) if secret is not None else None
        with self._verrou:
            self._db.execute(
                "INSERT INTO reglages (cle, valeur, secret, modifie_par, modifie_le) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur, "
                "secret = COALESCE(excluded.secret, reglages.secret), "
                "modifie_par = excluded.modifie_par, modifie_le = excluded.modifie_le",
                (cle, json.dumps(valeur, ensure_ascii=False), chiffre, par, time.time()))

    # --- Journal --------------------------------------------------------------------------------

    def journaliser(self, qui: str, action: str, detail: dict[str, Any] | None = None, ip: str | None = None) -> None:
        texte = json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)
        with self._verrou:
            dernier = self._db.execute("SELECT empreinte FROM journal ORDER BY id DESC LIMIT 1").fetchone()
            precedent = dernier[0] if dernier else _ORIGINE
            quand = time.time()
            self._db.execute(
                "INSERT INTO journal (quand, qui, action, detail, ip, precedent, empreinte) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (quand, qui, action, texte, ip, precedent, _empreinte(precedent, quand, qui, action, texte, ip)))

    def journal(self, limite: int) -> list[dict[str, Any]]:
        lignes = self._db.execute(
            "SELECT id, quand, qui, action, detail, ip FROM journal ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
        return [{"id": l[0], "quand": l[1], "qui": l[2], "action": l[3], "detail": json.loads(l[4]), "ip": l[5]}
                for l in lignes]

    def journal_intact(self) -> bool:
        precedent = _ORIGINE
        for quand, qui, action, detail, ip, prec, emp in self._db.execute(
                "SELECT quand, qui, action, detail, ip, precedent, empreinte FROM journal ORDER BY id"):
            if prec != precedent or emp != _empreinte(prec, quand, qui, action, detail, ip):
                return False
            precedent = emp
        return True
