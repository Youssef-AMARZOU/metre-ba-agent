# -*- coding: utf-8 -*-
"""Garde-fous RG-6 / RG-7 : aucune dimension inventée, vérifiabilité honnête."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.guardrails import (  # noqa: E402
    A_RENSEIGNER, NON_VERIFIABLE, PARTIEL, VERIFIABLE,
    annoter_verifiabilite, evaluer_verifiabilite, ou_a_renseigner, valeur_lue)


def test_valeur_lue_ne_retourne_jamais_de_defaut():
    assert valeur_lue({}, "a") is None
    assert valeur_lue({"a": 0}, "a") is None          # 0 = non lu
    assert valeur_lue({"a": -1}, "a") is None
    assert valeur_lue({"a": "abc"}, "a") is None
    assert valeur_lue({"a": True}, "a") is None
    assert valeur_lue({"a": "0,25"}, "a") == 0.25
    assert valeur_lue({"b": 0, "a": 0.3}, "b", "a") == 0.3
    assert valeur_lue(None, "a") is None


def test_ou_a_renseigner():
    assert ou_a_renseigner(None) == A_RENSEIGNER
    assert ou_a_renseigner(0.4) == 0.4


def test_zero_deguise_detecte():
    plan = {"catalogue_types": {"semelles": {
        "S1": {"a": 0, "b": 0, "h": 0, "dimensions_manquantes": True}}}}
    bilan = evaluer_verifiabilite(plan)
    el = bilan["elements"][0]
    assert el["manquants"] == ["a", "b", "h"]
    assert el["zeros_deguises"] == ["a", "b", "h"]
    assert bilan["statut"] == NON_VERIFIABLE


def test_plan_complet_verifiable():
    plan = {"catalogue_types": {
        "semelles": {"S1": {"a": 1.5, "b": 1.1, "h": 0.4}},
        "poteaux": {"P1": {"a": 0.3, "b": 0.3}},
        "poutres": {"N4": {"b": 0.25, "h": 0.4}}}}
    bilan = evaluer_verifiabilite(plan)
    assert bilan["statut"] == VERIFIABLE
    assert bilan["taux_dimensions_lues"] == 1.0


def test_hors_bornes_empeche_verifiable():
    # poutre de 25 m de haut : lecture aberrante, signalée jamais corrigée
    plan = {"catalogue_types": {"poutres": {"N1": {"b": 0.25, "h": 25.0}}}}
    bilan = evaluer_verifiabilite(plan)
    assert bilan["nb_hors_bornes"] == 1
    assert bilan["statut"] == PARTIEL
    assert plan["catalogue_types"]["poutres"]["N1"]["h"] == 25.0


def test_plan_vide_non_verifiable():
    assert evaluer_verifiabilite({})["statut"] == NON_VERIFIABLE
    assert evaluer_verifiabilite({"catalogue_types": {}})["statut"] == NON_VERIFIABLE


def test_annotation_additive_et_idempotente():
    plan = {"catalogue_types": {"poteaux": {"P1": {"a": 0.3, "b": 0.5}}},
            "_meta": {"avertissements": ["existant"]}}
    annoter_verifiabilite(plan)
    annoter_verifiabilite(plan)
    av = plan["_meta"]["avertissements"]
    assert av[0] == "existant"
    assert sum(1 for a in av if a.startswith("Vérifiabilité :")) == 1
    assert plan["_meta"]["verifiabilite"]["statut"] == VERIFIABLE
    assert plan["catalogue_types"]["poteaux"]["P1"] == {"a": 0.3, "b": 0.5}
