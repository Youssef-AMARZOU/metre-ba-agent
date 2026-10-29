# -*- coding: utf-8 -*-
"""tests/test_history_delete.py -- Suppression d'un projet de l'historique."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.history import (HISTORY_NAME, add_entry, load_history,  # noqa: E402
                          remove_entry)


def _plan_data():
    return {"beton": {"total_m3": 12.5}, "surfaces": {"total_m2": 88.0}}


def test_suppression_retire_fiche_et_instantane(tmp_path):
    sortie = tmp_path / "output"
    sortie.mkdir()
    entry = add_entry("Plan A", "plan-a.pdf", beton=12.5, acier=980,
                      dossier=str(sortie), plan_data=_plan_data(),
                      output_dir=sortie)
    snap = Path(entry["snapshot"])
    assert snap.exists()
    assert len(load_history(sortie)) == 1

    assert remove_entry(entry["id"], output_dir=sortie) is True
    assert load_history(sortie) == []
    assert not snap.exists()
    assert not (sortie / "projets" / entry["id"]).exists()
    # L'historique reste un fichier JSON valide
    assert (sortie / HISTORY_NAME).exists()


def test_suppression_conserve_les_livrables(tmp_path):
    """Le dossier de sortie partage ne doit jamais etre efface."""
    sortie = tmp_path / "output"
    (sortie / "projets").mkdir(parents=True)
    livrable = sortie / "metre.xlsx"
    livrable.write_text("x", encoding="utf-8")
    entry = add_entry("Plan B", "plan-b.pdf", dossier=str(sortie),
                      plan_data=_plan_data(), output_dir=sortie)

    assert remove_entry(entry["id"], output_dir=sortie) is True
    assert livrable.exists()
    assert sortie.is_dir()


def test_suppression_id_inconnu(tmp_path):
    sortie = tmp_path / "output"
    sortie.mkdir()
    add_entry("Plan C", "plan-c.pdf", output_dir=sortie)
    assert remove_entry("inexistant", output_dir=sortie) is False
    assert len(load_history(sortie)) == 1


def test_suppression_sans_effacement_fichiers(tmp_path):
    sortie = tmp_path / "output"
    sortie.mkdir()
    entry = add_entry("Plan D", "plan-d.pdf", plan_data=_plan_data(),
                      output_dir=sortie)
    snap = Path(entry["snapshot"])
    assert remove_entry(entry["id"], output_dir=sortie,
                        delete_files=False) is True
    assert snap.exists()
    assert load_history(sortie) == []
