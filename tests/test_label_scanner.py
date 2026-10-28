# -*- coding: utf-8 -*-
"""Tests de la couche GENERIQUE de lecture des etiquettes (label_scanner).

Objectif : prouver que la lecture ne depend ni d'un plan particulier ni d'un
format, et qu'aucune dimension n'est inventee.
"""
from __future__ import annotations

import pytest

from core.label_scanner import (
    LabelDims,
    parse_label,
    parse_reference,
    parse_section_values,
    register_label,
    rule_for,
    section_text,
)


# ---------------------------------------------------------------------------
# 1. Variantes d'ecriture d'une meme section
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,family,ref,cm", [
    ("S2(110x110x25)", "semelles", "S2", (110, 110, 25)),
    ("S2 110x110x25", "semelles", "S2", (110, 110, 25)),
    ("S2 : 110 x 110 x 25", "semelles", "S2", (110, 110, 25)),
    ("S2(1.10x1.10x0.25)", "semelles", "S2", (110, 110, 25)),
    ("P1(25x35)", "poteaux", "P1", (25, 35)),
    ("P1 25x35", "poteaux", "P1", (25, 35)),
    ("POT3(30x30)", "poteaux", "POT3", (30, 30)),
    ("PD2 30x30", "poteaux", "PD2", (30, 30)),
    ("Q1(25x30)", "poteaux", "Q1", (25, 30)),
    ("N1(25x40)", "poutres", "N1", (25, 40)),
    ("N1-(25X40)", "poutres", "N1", (25, 40)),
    ("N1_25x40", "poutres", "N1", (25, 40)),
    ("N1-25x40", "poutres", "N1", (25, 40)),
    ("BN25x30", "poutres", "BN", (25, 30)),
    ("BN_25x30", "poutres", "BN", (25, 30)),
    ("LG 25x35", "longrines", "LG", (25, 35)),
    ("CH1(20x15)", "chainages", "CH1", (20, 15)),
    ("M1(20x30)", "murs", "M1", (20, 30)),
    ("V2(15x250)", "voiles", "V2", (15, 250)),
    ("ESC1 20x30", "escaliers", "ESC1", (20, 30)),
])
def test_parse_label_variants(text, family, ref, cm):
    label = parse_label(text)
    assert label is not None, text
    assert label.family == family
    assert label.reference == ref
    assert label.values_cm == pytest.approx(cm)


# ---------------------------------------------------------------------------
# 2. Ce qui ne doit JAMAIS etre interprete comme une section
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "8T12", "4HA10", "e=15", "e=(10x9 symet)", "25x30", "(25x35)",
    "PLAN N3", "S2", "Escalier", "CAD+ETR", "1500", "R+2",
])
def test_parse_label_rejects_non_sections(text):
    assert parse_label(text) is None


def test_section_plausibility_bounds():
    # 5 cm n'est pas une section de beton arme ; 900 cm non plus.
    assert parse_section_values("5x5") is None
    assert parse_section_values("900x900") is None
    assert parse_section_values("25x30") == pytest.approx((25, 30))


# ---------------------------------------------------------------------------
# 3. Unites
# ---------------------------------------------------------------------------
def test_metres_decimales_converties():
    assert parse_section_values("0.25x0.35") == pytest.approx((25, 35))
    assert parse_section_values("1.10x1.10x0.25") == pytest.approx((110, 110, 25))
    label = parse_label("P1 0.25x0.35")
    assert label.as_metres() == {"a": 0.25, "b": 0.35}


def test_unite_explicite_mm():
    label = parse_label("S2(1100x1100x250mm)")
    assert label is not None
    assert label.as_metres() == {"a": 1.1, "b": 1.1, "h": 0.25}


# ---------------------------------------------------------------------------
# 4. Semantique des dimensions par famille
# ---------------------------------------------------------------------------
def test_poutre_b_h_pas_a():
    label = parse_label("N1(25x40)")
    assert label.as_metres() == {"b": 0.25, "h": 0.40}


def test_dalle_epaisseur():
    label = parse_label("D1 e=20")
    assert label.as_metres() == {"h": 0.20}
    assert section_text(label.values_cm, "dalles") == "e=20"


def test_semelle_filante_largeur_hauteur():
    label = parse_label("SF 45x20")
    assert label.as_metres() == {"a": 0.45, "h": 0.20}
    assert rule_for("SF", "semelles") == ("a", "h")


def test_mur_epaisseur_hauteur():
    assert parse_label("M1(20x250)").as_metres() == {"a": 0.20, "h": 2.50}


# ---------------------------------------------------------------------------
# 5. Reference seule (tableaux de nomenclature)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("S2", ("semelles", "S2")),
    ("BN1", ("poutres", "BN1")),
    ("POT12", ("poteaux", "POT12")),
    ("M3", ("murs", "M3")),
    ("LG", ("longrines", "LG")),
])
def test_parse_reference(text, expected):
    assert parse_reference(text) == expected


@pytest.mark.parametrize("text", ["25x30", "8T12", "PLAN", ""])
def test_parse_reference_rejects(text):
    assert parse_reference(text) is None


# ---------------------------------------------------------------------------
# 6. Enregistrement non destructif
# ---------------------------------------------------------------------------
def test_register_does_not_overwrite_read_values():
    cat: dict = {}
    register_label(cat, LabelDims("poteaux", "P1", (25.0, 35.0), ("a", "b")))
    register_label(cat, LabelDims("poteaux", "P1", (30.0, 30.0), ("a", "b")))
    # La premiere lecture est conservee ; la seconde ouvre une cle distincte.
    assert cat["poteaux"]["P1"]["a"] == 0.25
    assert "P1_30x30" in cat["poteaux"]


def test_register_merges_into_existing_composite_key():
    cat = {"poutres": {"N1_25x40": {"b": 0.25, "h": 0.40,
                                   "section_str": "25x40"}}}
    key = register_label(cat, LabelDims("poutres", "N1", (25.0, 40.0),
                                        ("b", "h")))
    assert key == "N1_25x40"
    assert "N1" not in cat["poutres"]
