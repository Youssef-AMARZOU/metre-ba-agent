# -*- coding: utf-8 -*-
"""Tests d'extraction GENERIQUE : plusieurs plans aux conventions differentes.

Chaque plan est decrit par une simple liste de mots positionnes (comme le
ferait PyMuPDF, l'OCR ou le DXF), ce qui garantit que le comportement ne
depend d'aucun fichier de reference.
"""
from __future__ import annotations

import pytest

from core.local_extractor import VectorPlanExtractor


def words(items, page=1):
    """Construit des mots positionnes : [(texte, x, y), ...]."""
    return [{"text": t, "x": x, "y": y, "x1": x + 40, "y1": y + 10,
             "page": page} for t, x, y in items]


def sections(plan, family):
    return {ref: (spec.get("section_str"), spec.get("a"), spec.get("b"),
                  spec.get("h"))
            for ref, spec in plan["catalogue_types"].get(family, {}).items()}


# ---------------------------------------------------------------------------
# Plan A — conventions "P / S / N" avec parentheses
# ---------------------------------------------------------------------------
PLAN_A = [
    ("P1(25x35)", 100, 100), ("P2(40x40)", 300, 100),
    ("S1(100x100x25)", 100, 300), ("S2(110x110x25)", 300, 300),
    ("N1-(25X40)", 100, 500), ("N2(20X35)", 300, 500),
    ("M1(20x250)", 100, 700), ("V1(15x250)", 300, 700),
    ("D1 e=20", 100, 900), ("LG 25x35", 300, 900),
]


def test_plan_a_toutes_familles():
    plan = VectorPlanExtractor().extract_from_words(words(PLAN_A))
    cat = plan["catalogue_types"]

    assert cat["poteaux"]["P1"]["section_str"] == "25x35"
    assert cat["poteaux"]["P2"]["section_str"] == "40x40"

    assert cat["semelles"]["S1"]["section_str"] == "100x100"
    assert cat["semelles"]["S2"]["section_str"] == "110x110"
    assert cat["semelles"]["S2"]["h"] == pytest.approx(0.25)

    assert cat["poutres"]["N1_25x40"]["h"] == pytest.approx(0.40)
    assert cat["poutres"]["N2"]["b"] == pytest.approx(0.20)
    assert cat["poutres"]["N2"]["h"] == pytest.approx(0.35)

    assert cat["murs"]["M1"]["section_str"] == "20x250"
    assert cat["voiles"]["V1"]["h"] == pytest.approx(2.50)
    assert cat["dalles"]["D1"]["h"] == pytest.approx(0.20)
    assert cat["longrines"]["LG"]["section_str"] == "25x35"


# ---------------------------------------------------------------------------
# Plan B — conventions "POT / PD / Q", espaces et deux points
# ---------------------------------------------------------------------------
PLAN_B = [
    ("POT1 30x40", 100, 100), ("PD2 30x30", 300, 100),
    ("Q3(25x30)", 500, 100),
    ("SEM1 : 120 x 120 x 30", 100, 300),
    ("CH1 20x15", 300, 300),
    ("LT2(20x30)", 500, 300),
]


def test_plan_b_conventions_alternatives():
    plan = VectorPlanExtractor().extract_from_words(words(PLAN_B))
    cat = plan["catalogue_types"]

    assert cat["poteaux"]["POT1"]["section_str"] == "30x40"
    assert cat["poteaux"]["PD2"]["section_str"] == "30x30"
    assert cat["poteaux"]["Q3"]["section_str"] == "25x30"
    # SEM -> variante 'maison' de S (prefixe le plus long prioritaire)
    assert cat["semelles"]["SEM1"]["section_str"] == "120x120"
    assert cat["chainages"]["CH1"]["section_str"] == "20x15"
    assert cat["linteaux"]["LT2"]["section_str"] == "20x30"


# ---------------------------------------------------------------------------
# Plan C — etiquettes nues + tableau de nomenclature a deux cellules
# ---------------------------------------------------------------------------
def test_plan_c_tableau_generique():
    ext = VectorPlanExtractor()
    ext._reset()
    data = [
        ["Repere", "Section", "Observations"],
        ["S4", "150 x 150 x 40", ""],
        ["P4", "40x40", ""],
        ["M2", "20x250", ""],
        ["V3", "(15x300)", ""],
        ["D2", "e=25", ""],
        ["X9", "25x30", "inconnu"],       # prefixe inconnu -> ignore
        ["N9", "8T12", "ferraillage"],    # pas une section -> ignore
    ]
    assert ext._parse_generic_table(data, page_num=1) == 5
    cat = ext.global_catalogue

    assert cat["semelles"]["S4"]["section_str"] == "150x150"
    assert cat["semelles"]["S4"]["h"] == pytest.approx(0.40)
    assert cat["poteaux"]["P4"]["section_str"] == "40x40"
    assert cat["murs"]["M2"]["section_str"] == "20x250"
    assert cat["voiles"]["V3"]["h"] == pytest.approx(3.00)
    assert cat["dalles"]["D2"]["h"] == pytest.approx(0.25)
    assert "X9" not in {r for fam in cat.values() if isinstance(fam, dict)
                        for r in fam}
    assert "N9" not in cat.get("poutres", {})


# ---------------------------------------------------------------------------
# Plan D — entrees DXF : reference + dims_text separees
# ---------------------------------------------------------------------------
def test_plan_d_blocs_dxf():
    blocks = [
        {"reference": "S2", "dims_text": "110x110x25", "family": "SEMELLE",
         "x": 100, "y": 100},
        {"reference": "P1", "dims_text": "25x35", "family": "POTEAU",
         "x": 300, "y": 100},
        {"reference": "N1", "dims_text": "20x40", "family": "POUTRE",
         "x": 100, "y": 300},
        {"reference": "M1", "dims_text": "20x250", "family": "MUR",
         "x": 300, "y": 300},
    ]
    plan = VectorPlanExtractor().extract_from_text_blocks(blocks)
    cat = plan["catalogue_types"]

    assert cat["semelles"]["S2"]["section_str"] == "110x110"
    assert cat["poteaux"]["P1"]["section_str"] == "25x35"
    assert cat["poutres"]["N1"]["section_str"] == "20x40"
    assert cat["murs"]["M1"]["section_str"] == "20x250"


# ---------------------------------------------------------------------------
# Plan E — etiquettes nues sans aucune dimension : NON_RESOLU, jamais invente
# ---------------------------------------------------------------------------
def test_plan_e_non_resolu():
    plan = VectorPlanExtractor().extract_from_words(
        words([("S1", 100, 100), ("P1", 300, 100), ("LG", 500, 100)]))
    cat = plan["catalogue_types"]

    for fam, ref in (("semelles", "S1"), ("poteaux", "P1"),
                     ("longrines", "LG")):
        spec = cat.get(fam, {}).get(ref, {})
        assert not spec.get("section_str")
        assert not spec.get("a") or spec.get("a") == 0

    notes = " ".join(plan["_meta"].get("hypotheses", []))
    assert "NON_RESOLU" in notes


# ---------------------------------------------------------------------------
# Plan F — aucun doublon 'BN' / 'BN_25x30'
# ---------------------------------------------------------------------------
def test_plan_f_pas_de_doublon():
    plan = VectorPlanExtractor().extract_from_words(
        words([("BN_25x30", 100, 100), ("BN 25x30", 300, 100)]))
    cat = plan["catalogue_types"]
    matching = [(fam, k) for fam, elems in cat.items()
                if isinstance(elems, dict)
                for k, v in elems.items() if v.get("section_str") == "25x30"]
    assert len(matching) == 1, cat
