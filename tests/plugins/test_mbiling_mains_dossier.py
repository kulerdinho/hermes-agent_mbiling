"""Les Mains n'envoient jamais au PC un dossier de la cellule (sinon « cd » échoue : code 126)."""
from __future__ import annotations

import pytest

from plugins.mbiling_mains import environnement as module

DEPART = "/c/Users/marie/Documents/Mbiling-Test"


@pytest.fixture
def env(tmp_path, monkeypatch):
    accueil = tmp_path / "data"
    accueil.mkdir()
    monkeypatch.setenv("MBILING_HOME", str(accueil))
    monkeypatch.chdir(accueil)
    monkeypatch.setattr(module, "info_appareil",
                        lambda _config: {"appareil": "pc-1", "info": {"dossier_depart": DEPART, "tmp": "/tmp"}})
    monkeypatch.setattr(module.EnvironnementMains, "init_session", lambda self: None)
    envoyees = []
    monkeypatch.setattr(module.BaseEnvironment, "execute",
                        lambda self, command, cwd="", **kw: envoyees.append(cwd or self.cwd) or {"output": "", "returncode": 0})
    return accueil, envoyees


def _creer(cwd):
    return module.EnvironnementMains(cwd=cwd, timeout=10, config=object())


def test_le_dossier_de_travail_de_la_cellule_devient_le_dossier_de_depart_du_pc(env):
    accueil, envoyees = env
    mains = _creer(str(accueil))
    assert mains.cwd == DEPART
    mains.execute("ls")
    mains.execute("ls", cwd=str(accueil / "profiles" / "assistant"))
    mains.execute("ls", cwd="/root")
    assert envoyees == [DEPART, DEPART, DEPART]


def test_un_dossier_du_pc_est_garde_tel_quel(env):
    _accueil, envoyees = env
    mains = _creer("~")
    assert mains.cwd == DEPART
    mains.execute("ls", cwd="/c/Users/marie/Desktop")
    assert envoyees == ["/c/Users/marie/Desktop"]


def test_la_racine_n_est_jamais_ecartee_en_bloc(env, monkeypatch):
    _accueil, envoyees = env
    monkeypatch.chdir("/")
    mains = _creer("/")
    mains.execute("ls", cwd="/c/Windows")
    assert envoyees == ["/c/Windows"]
