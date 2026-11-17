# -*- coding: utf-8 -*-
"""C5 : benchmark de généralisation sur plans réels.

* test_comparer_* : logique de comparaison, sans corpus (toujours exécutés).
* test_corpus_* : exécutés seulement si PLANBA_CORPUS_DIR pointe vers les
  plans du manifeste ET si la vérité terrain est « valide ». Sinon : SKIP
  avec la raison exacte — jamais un faux vert.
"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.corpus.bench import charger_manifest, comparer  # noqa: E402

GT = {"familles": {
    "semelles": {"S1": {"a": 1.5, "b": 1.1, "h": 0.4}},
    "poteaux": {"P1": {"a": 0.3, "b": 0.3}, "P2": {"a": 0.3, "b": 0.3}},
    "poutres": {"N4": {"b": 0.25, "h": 0.4}}}}


def test_comparer_valeur_fausse_detectee():
    plan = {"catalogue_types": {
        "semelles": {"S1": {"a": 0.3, "b": 0.5, "h": None}},
        "poteaux": {"P1": {"a": 0.3, "b": 0.5}},
        "poutres": {"N4_25x40": {"b": 0.25, "h": 0.4}}}}
    r = comparer(plan, GT)["totaux"]
    assert r["valeurs_fausses"] == 3      # S1.a, S1.b, P1.b
    assert r["valeurs_manquantes"] == 1   # S1.h
    assert r["rappel"] == 0.75            # P2 non trouvé


def test_comparer_orientation_poteau_indifferente():
    plan = {"catalogue_types": {"poteaux": {"P1": {"a": 0.5, "b": 0.3}}}}
    gt = {"familles": {"poteaux": {"P1": {"a": 0.3, "b": 0.5}}}}
    assert comparer(plan, gt)["totaux"]["valeurs_fausses"] == 0


def test_manifeste_coherent():
    plans = charger_manifest()
    ids = [p["id"] for p in plans]
    assert len(ids) == len(set(ids))
    assert any(p["role"] == "calibrage" for p in plans)
    assert sum(p["role"] == "evaluation" for p in plans) >= 3
    for p in plans:
        if p.get("verite_terrain"):
            assert (ROOT / "corpus" / "verite_terrain" / p["verite_terrain"]).exists()


def _plans_mesurables():
    d = os.environ.get("PLANBA_CORPUS_DIR")
    out = []
    for p in charger_manifest():
        if p["role"] != "evaluation" or not p.get("verite_terrain"):
            continue
        gt = json.loads((ROOT / "corpus" / "verite_terrain" /
                         p["verite_terrain"]).read_text(encoding="utf-8"))
        out.append((p, gt, d))
    return out


@pytest.mark.parametrize("plan,gt,corpus_dir", _plans_mesurables(),
                         ids=lambda x: x["id"] if isinstance(x, dict) and "id" in x else "")
def test_corpus_aucune_valeur_fausse_et_rappel(plan, gt, corpus_dir):
    if not corpus_dir:
        pytest.skip("PLANBA_CORPUS_DIR non défini : corpus réel indisponible")
    if gt.get("statut") != "valide":
        pytest.skip(f"vérité terrain {plan['verite_terrain']} en statut "
                    f"'{gt.get('statut')}' : à relire et valider")
    chemin = Path(corpus_dir) / plan["fichier"]
    if not chemin.exists():
        pytest.skip(f"plan absent : {chemin}")
    from core.local_extractor import extract_plan_auto
    r = comparer(extract_plan_auto(str(chemin)), gt)["totaux"]
    assert r["valeurs_fausses"] == 0, f"valeurs fausses : {r}"
    assert r["rappel"] is not None and r["rappel"] >= 0.80, f"rappel < 80 % : {r}"
