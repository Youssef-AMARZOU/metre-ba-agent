#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/moroccan_bet_engine.py -- Moteur deterministe d'Avant-Metre BET Maroc.

Zero hallucination : toutes les formules geometriques et d'acier sont codees
en dur (DGA / CPS-type Maroc / BAEL 91 mod. 99 / RPS 2000 v2011 /
NM 01.4.097). L'OCR et le VLM n'alimentent JAMAIS un volume ou un poids :
ils fournissent des `ExtractedPlanRecord` qui passent les 4 portes du
`PlanBAGatekeeper` avant d'entrer dans `MoroccanBETEngine`.

Portes :
  1. Normalisation automatique des unites (mm / cm -> m)
  2. Deduplication par cle (level, label) + union des files d'axes
  3. Decodage deterministe des textes d'armatures -> nomenclature
  4. Controle croise : qty coffrage == qty nomenclature + ratio kg/m3
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Tuple

from core.bet_contracts import (
    RE_BAR_GROUP,
    RE_EXPLICIT_LENGTH,
    RE_LONG_BAR,
    RE_STIRRUP,
    AuditAnomaly,
    AvantMetreLine,
    ExtractedPlanRecord,
    RebarScheduleRow,
)

__all__ = [
    "MOROCCAN_REBAR_CATALOG",
    "MOROCCAN_STEEL_RATIO_BOUNDS",
    "AuditAnomaly",
    "AvantMetreLine",
    "RebarScheduleRow",
    "ExtractedPlanRecord",
    "MoroccanBETEngine",
    "PlanBAGatekeeper",
]

# ============================================================================
# Constantes normatives Maroc
# ============================================================================

# Diametres nominaux FeE500 (NM 01.4.097) et masses lineiques (kg/m)
MOROCCAN_REBAR_CATALOG: Dict[int, float] = {
    6: 0.222,
    8: 0.395,
    10: 0.617,
    12: 0.888,
    14: 1.208,
    16: 1.578,
    20: 2.466,
    25: 3.853,
    32: 6.313,
    40: 9.864,
}

# Plages de ratios d'acier admissibles (kg/m3) en zone sismique RPS 2000 v2011
MOROCCAN_STEEL_RATIO_BOUNDS: Dict[str, Tuple[float, float]] = {
    "semelle": (40.0, 95.0),
    "poteau": (100.0, 210.0),
    "poutre": (95.0, 185.0),
    "longrine": (75.0, 140.0),
    "chainage": (65.0, 125.0),
    "voile": (65.0, 140.0),
    "dalle_pleine": (75.0, 145.0),
}

PRIX_CODES: Dict[str, str] = {
    "beton_proprete": "B.01",
    "semelle": "B.02",
    "longrine": "B.04",
    "chainage": "B.04",
    "poteau": "B.05",
    "poutre": "B.06",
    "voile": "B.07",
    "dalle_pleine": "B.08",
    "acier": "A.01",
}


# ============================================================================
# Moteur de calcul
# ============================================================================

class MoroccanBETEngine:
    """Calculs exacts beton / coffrage / acier + controleur de coherence."""

    def __init__(
        self,
        enrobage_fondation_m: float = 0.05,
        enrobage_elevation_m: float = 0.025,
        debord_proprete_m: float = 0.10,
        epaisseur_proprete_m: float = 0.10,
        longueur_barre_commerciale_m: float = 12.00,
        lap_factor: int = 50,
        hook_factor: int = 15,
    ):
        self.c_fond = enrobage_fondation_m
        self.c_elev = enrobage_elevation_m
        self.debord_bp = debord_proprete_m
        self.ep_bp = epaisseur_proprete_m
        self.max_bar_len = longueur_barre_commerciale_m
        self.lap_factor = lap_factor
        self.hook_factor = hook_factor
        self.anomalies: List[AuditAnomaly] = []

    def reset(self) -> None:
        self.anomalies = []

    # ------------------------------------------------------------------
    # 1. Beton & coffrage (sans double comptage)
    # ------------------------------------------------------------------

    def compute_semelle_isolee(
        self,
        label: str,
        qty: int,
        A: float,
        B: float,
        h_patin: float,
        h_glacis: float = 0.0,
        a_fut: float = 0.30,
        b_fut: float = 0.30,
    ) -> Tuple[AvantMetreLine, AvantMetreLine]:
        """Beton de proprete (B.01) + beton arme de semelle (B.02).

        V_BP = (A + 2d)(B + 2d) * e_BP
        V_BA = A*B*h1 + (h2/6)[AB + ab + (A+a)(B+b)]  (tronc de pyramide)
        """
        self._validate_dimensions(
            label, {"A": A, "B": B, "H": h_patin + h_glacis},
            min_val=0.15, max_val=6.0)

        L_bp = round(A + 2 * self.debord_bp, 3)
        l_bp = round(B + 2 * self.debord_bp, 3)
        vol_bp_unit = L_bp * l_bp * self.ep_bp
        line_bp = AvantMetreLine(
            prix_code=PRIX_CODES["beton_proprete"],
            category="beton_proprete",
            designation=f"Béton de propreté sous semelle {label} "
                        f"(débord {int(self.debord_bp*100)} cm)",
            unite="m3",
            nombre=qty,
            longueur=L_bp,
            largeur=l_bp,
            hauteur=self.ep_bp,
            quantite_partielle=round(qty * vol_bp_unit, 3),
            label=label,
        )

        vol_patin = A * B * h_patin
        if h_glacis > 0.001:
            vol_glacis = (h_glacis / 6.0) * (
                A * B + a_fut * b_fut + (A + a_fut) * (B + b_fut))
        else:
            vol_glacis = 0.0
        vol_ba_unit = vol_patin + vol_glacis
        h_equivalente = round(vol_ba_unit / max(A * B, 1e-9), 3)

        line_ba = AvantMetreLine(
            prix_code=PRIX_CODES["semelle"],
            category="semelle",
            designation=f"Béton armé en fondation pour semelle {label} "
                        f"({int(round(A*100))}x{int(round(B*100))}x"
                        f"{int(round((h_patin + h_glacis)*100))})",
            unite="m3",
            nombre=qty,
            longueur=round(A, 3),
            largeur=round(B, 3),
            hauteur=h_equivalente,
            quantite_partielle=round(qty * vol_ba_unit, 3),
            label=label,
        )
        return line_bp, line_ba

    def compute_poutre_entre_nus(
        self,
        label: str,
        qty: int,
        portee_axe_axe: float,
        largeur_poteau_gauche: float,
        largeur_poteau_droite: float,
        b_poutre: float,
        h_totale: float,
        type_plancher: str = "hourdis",
        epaisseur_dalle: float = 0.0,
        category: str = "poutre",
    ) -> AvantMetreLine:
        """Portee strictement entre nus : L = L_axe - a_g/2 - a_d/2.
        Hourdis -> hauteur totale ; dalle pleine -> retombee (h - e_dalle)."""
        L_entre_nus = round(
            portee_axe_axe - largeur_poteau_gauche / 2.0
            - largeur_poteau_droite / 2.0, 3)
        if L_entre_nus <= 0:
            self.anomalies.append(AuditAnomaly(
                severity="BLOQUANT",
                element_label=label,
                rule_code="GEOM_SPAN_NEGATIVE",
                message=f"Portée entre nus invalide ({L_entre_nus} m) pour "
                        f"{label} (axe-axe {portee_axe_axe} m).",
                suggested_fix="Vérifier la portée axe-axe et les largeurs "
                              "d'appuis sur le plan de coffrage.",
            ))
            L_entre_nus = 0.0

        if type_plancher == "dalle_pleine" and epaisseur_dalle > 0:
            h_calcul = round(max(0.0, h_totale - epaisseur_dalle), 3)
        else:
            h_calcul = round(h_totale, 3)

        self._validate_dimensions(
            label, {"b": b_poutre, "h": h_totale}, min_val=0.10, max_val=2.50)

        vol_total = round(qty * L_entre_nus * b_poutre * h_calcul, 3)
        prix = PRIX_CODES.get(category, PRIX_CODES["poutre"])
        return AvantMetreLine(
            prix_code=prix,
            category=category,
            designation=f"{category.capitalize()} BA {label} "
                        f"({int(round(b_poutre*100))}x{int(round(h_totale*100))}) "
                        f"— portée entre nus {L_entre_nus:.2f} m",
            unite="m3",
            nombre=qty,
            longueur=L_entre_nus,
            largeur=round(b_poutre, 3),
            hauteur=h_calcul,
            quantite_partielle=vol_total,
            label=label,
        )

    def compute_poteau(self, label: str, qty: int, b: float, h: float,
                       hauteur: float, category: str = "poteau"
                       ) -> AvantMetreLine:
        """Poteau (B.05) ou voile (B.07) : V = n * L * b * h."""
        self._validate_dimensions(
            label, {"b": b, "h": h}, min_val=0.10, max_val=3.00)
        self._validate_dimensions(
            label, {"L": hauteur}, min_val=0.50, max_val=12.0)
        vol = round(qty * hauteur * b * h, 3)
        return AvantMetreLine(
            prix_code=PRIX_CODES.get(category, PRIX_CODES["poteau"]),
            category=category,
            designation=f"{category.capitalize()} BA {label} "
                        f"({int(round(b*100))}x{int(round(h*100))}) "
                        f"h={hauteur:.2f} m",
            unite="m3",
            nombre=qty,
            longueur=round(hauteur, 3),
            largeur=round(b, 3),
            hauteur=round(h, 3),
            quantite_partielle=vol,
            label=label,
        )

    # ------------------------------------------------------------------
    # 2. Aciers (RPS 2000 v2011 / BAEL)
    # ------------------------------------------------------------------

    def validate_and_get_unit_mass(self, element_label: str,
                                   diameter_mm: int) -> float:
        """Diametre hors NM 01.4.097 (ex. HA18, HA15) -> anomalie BLOQUANTE
        + masse du diametre normalise le plus proche."""
        if diameter_mm not in MOROCCAN_REBAR_CATALOG:
            closest = min(MOROCCAN_REBAR_CATALOG.keys(),
                          key=lambda k: (abs(k - diameter_mm), k))
            self.anomalies.append(AuditAnomaly(
                severity="BLOQUANT",
                element_label=element_label,
                rule_code="INVALID_DIAMETER_OCR",
                message=f"Diamètre HA{diameter_mm} inexistant dans le "
                        f"catalogue NM 01.4.097 (erreur OCR probable).",
                suggested_fix=f"Remplacer HA{diameter_mm} par HA{closest}.",
            ))
            return MOROCCAN_REBAR_CATALOG[closest]
        return MOROCCAN_REBAR_CATALOG[diameter_mm]

    def developed_length_longitudinal(
        self, diameter_mm: int, clear_length_m: float,
        add_end_hooks: bool = True, explicit_length: bool = False,
    ) -> float:
        """L_dev = L + 2*15phi (si crosses) + nb_laps * 50phi,
        nb_laps = max(0, ceil(L_dev / 12) - 1)."""
        phi_m = diameter_mm / 1000.0
        ancrage = (2 * self.hook_factor * phi_m
                   if (add_end_hooks and not explicit_length) else 0.0)
        base_dev = clear_length_m + ancrage
        nb_laps = max(0, math.ceil(base_dev / self.max_bar_len) - 1)
        return round(base_dev + nb_laps * (self.lap_factor * phi_m), 3)

    def compute_longitudinal_bars_weight(
        self,
        element_label: str,
        nb_elements: int,
        nb_bars_per_element: int,
        diameter_mm: int,
        clear_length_m: float,
        add_end_hooks: bool = True,
    ) -> float:
        unit_mass = self.validate_and_get_unit_mass(element_label, diameter_mm)
        dev = self.developed_length_longitudinal(
            diameter_mm, clear_length_m, add_end_hooks)
        return round(nb_elements * nb_bars_per_element * dev * unit_mass, 2)

    def stirrup_developed_length(self, b_m: float, h_m: float,
                                 diameter_mm: int) -> float:
        """L_cadre = 2(b-2c) + 2(h-2c) + 2*max(10phi, 0.08) (crochets 135°)."""
        phi_m = diameter_mm / 1000.0
        hook_len = 2.0 * max(10.0 * phi_m, 0.08)
        return round(
            2.0 * max(0.05, b_m - 2 * self.c_elev)
            + 2.0 * max(0.05, h_m - 2 * self.c_elev)
            + hook_len, 3)

    @staticmethod
    def stirrup_count(span_m: float, spacing_m: float) -> int:
        """nb_cadres = ceil(portee / e) + 1."""
        return int(math.ceil(span_m / max(spacing_m, 0.05))) + 1

    def compute_transverse_stirrups_weight(
        self,
        element_label: str,
        nb_elements: int,
        b_m: float,
        h_m: float,
        span_m: float,
        spacing_m: float,
        diameter_mm: int = 8,
    ) -> float:
        unit_mass = self.validate_and_get_unit_mass(element_label, diameter_mm)
        perim = self.stirrup_developed_length(b_m, h_m, diameter_mm)
        nb = self.stirrup_count(span_m, spacing_m)
        return round(nb_elements * nb * perim * unit_mass, 2)

    # ------------------------------------------------------------------
    # 3. Controleur de coherence (Porte 4)
    # ------------------------------------------------------------------

    def verify_element_consistency(
        self,
        line: AvantMetreLine,
        qty_on_formwork_plan: Optional[int],
        qty_in_schedule_table: Optional[int],
    ) -> bool:
        """1. Coffrage vs nomenclature  2. Ratio kg/m3 dans la plage BET."""
        is_valid = True
        label = line.label or line.designation

        if (qty_on_formwork_plan is not None
                and qty_in_schedule_table is not None
                and qty_on_formwork_plan != qty_in_schedule_table):
            is_valid = False
            self.anomalies.append(AuditAnomaly(
                severity="BLOQUANT",
                element_label=label,
                rule_code="CROSS_CHECK_QTY_MISMATCH",
                message=(f"Écart de quantité : {qty_on_formwork_plan} "
                         f"repéré(s) sur le plan de coffrage contre "
                         f"{qty_in_schedule_table} dans le tableau de "
                         f"ferraillage."),
                suggested_fix="Vérifier les files d'axes sur le plan de "
                              "coffrage avant validation.",
            ))

        if line.quantite_partielle > 0 and line.acier_kg > 0:
            line.ratio_acier_kg_m3 = round(
                line.acier_kg / line.quantite_partielle, 1)
            bounds = MOROCCAN_STEEL_RATIO_BOUNDS.get(line.category)
            if bounds:
                min_r, max_r = bounds
                if not (min_r <= line.ratio_acier_kg_m3 <= max_r):
                    is_valid = False
                    self.anomalies.append(AuditAnomaly(
                        severity="BLOQUANT",
                        element_label=label,
                        rule_code="RPS2011_RATIO_OUT_OF_BOUNDS",
                        message=(f"Ratio d'acier anormal : "
                                 f"{line.ratio_acier_kg_m3} kg/m³ (plage BET "
                                 f"Maroc '{line.category}' : {min_r}–{max_r} "
                                 f"kg/m³)."),
                        suggested_fix="Vérifier qu'une cote en cm n'a pas été "
                                      "lue en m ou qu'un diamètre n'est pas "
                                      "doublé.",
                    ))
        return is_valid

    def _validate_dimensions(self, label: str, dims: Dict[str, float],
                             min_val: float, max_val: float) -> None:
        for k, v in dims.items():
            if not (min_val <= v <= max_val):
                self.anomalies.append(AuditAnomaly(
                    severity="BLOQUANT",
                    element_label=label,
                    rule_code="DIMENSION_UNIT_ERROR",
                    message=f"Dimension {k}={v} m hors plage "
                            f"[{min_val} m, {max_val} m] (confusion cm/mm "
                            f"probable).",
                    suggested_fix=f"Tester {v/10.0:.2f} m ou {v/100.0:.2f} m.",
                ))


# ============================================================================
# Les 4 portes de securite
# ============================================================================

class PlanBAGatekeeper:
    """Porte 1 unites, Porte 2 deduplication, Porte 3 decodage armatures,
    Porte 4 controle croise (delegue a `MoroccanBETEngine`)."""

    RE_LONG_BAR = RE_LONG_BAR
    RE_STIRRUP = RE_STIRRUP

    SECTION_KEYS = ("b", "h", "a", "e", "h_patin", "h_glacis", "ep")
    LARGE_KEYS = ("A", "B", "L", "H", "portee", "longueur", "hauteur")

    def __init__(self, engine: MoroccanBETEngine):
        self.engine = engine

    # ---------------- Porte 1 ----------------
    def normalize_dimensions_to_meters(
        self, label: str, category: str, raw_dims: Dict[str, float]
    ) -> Dict[str, float]:
        """mm / cm -> m.
        Sections (b,h,a,e) : >= 100 -> /1000 ; > 3.5 -> /100.
        Grandes dims (A,B,L,H) : >= 500 -> /1000 ; > 25 -> /100."""
        norm: Dict[str, float] = {}
        for key, val in raw_dims.items():
            try:
                v = float(str(val).replace(",", "."))
            except (TypeError, ValueError):
                continue
            if key in self.SECTION_KEYS:
                if v >= 100.0:
                    v = v / 1000.0
                elif v > 3.5:
                    v = v / 100.0
            elif key in self.LARGE_KEYS:
                if v >= 500.0:
                    v = v / 1000.0
                elif v > 25.0:
                    v = v / 100.0
            norm[key] = round(v, 3)
        return norm

    # ---------------- Porte 2 ----------------
    def deduplicate_records(self, records: List[ExtractedPlanRecord]
                            ) -> List[ExtractedPlanRecord]:
        """Fusionne les occurrences d'un meme (level, label) : union des
        files d'axes, conservation des quantites de chaque source pour le
        controle croise, enrichissement des dimensions/aciers manquants."""
        merged: Dict[Tuple[str, str], ExtractedPlanRecord] = {}
        for rec in records:
            key = (rec.level.strip().upper(), rec.label.strip().upper())
            if key not in merged:
                # Les specs identiques d'un MEME enregistrement sont des
                # nappes distinctes (ex. nappe X + nappe Y '8HA12 L=1.30')
                rec.grid_axes = sorted(set(a for a in rec.grid_axes if a))
                merged[key] = rec
                continue
            ex = merged[key]
            ex.grid_axes = sorted(set(ex.grid_axes) | set(
                a for a in rec.grid_axes if a))
            # Chaque occurrence sur le plan de coffrage est une instance :
            # les quantites coffrage se CUMULENT ; la quantite du tableau de
            # nomenclature est une valeur unique (premiere lecture retenue).
            if rec.qty_plan_coffrage is not None:
                ex.qty_plan_coffrage = (ex.qty_plan_coffrage or 0) + rec.qty_plan_coffrage
            if ex.qty_table_schedule is None:
                ex.qty_table_schedule = rec.qty_table_schedule
            for k, v in rec.raw_dimensions.items():
                if not ex.raw_dimensions.get(k):
                    ex.raw_dimensions[k] = v
            for spec in rec.rebar_specs:
                if spec not in ex.rebar_specs:
                    ex.rebar_specs.append(spec)
            if ex.crop_image_bgr is None and rec.crop_image_bgr is not None:
                ex.crop_image_bgr = rec.crop_image_bgr
            for src in rec.source_engines:
                if src not in ex.source_engines:
                    ex.source_engines.append(src)
            ex.confidence = max(ex.confidence, rec.confidence)
            if ex.category == "autre" and rec.category != "autre":
                ex.category = rec.category
        return list(merged.values())

    # ---------------- Porte 3 ----------------
    def parse_rebar_specs_to_schedule(
        self,
        rec: ExtractedPlanRecord,
        norm_dims: Dict[str, float],
        effective_qty: int,
    ) -> Tuple[List[RebarScheduleRow], float]:
        """Annotations ('6HA14 L=4.20', 'Cad HA8 e=15') -> lignes de
        nomenclature RPS 2011 + poids total (kg)."""
        rows: List[RebarScheduleRow] = []
        total_kg = 0.0
        eng = self.engine

        b_m = norm_dims.get("b", norm_dims.get("A", 0.30))
        h_m = norm_dims.get("h", norm_dims.get("B", 0.30))
        if rec.category == "semelle":
            span_m = norm_dims.get("A", b_m)
        else:
            span_m = norm_dims.get("L", norm_dims.get("H", 3.00))

        for idx, spec in enumerate(rec.rebar_specs, start=1):
            spec = str(spec).strip()
            if not spec:
                continue

            m_stir = self.RE_STIRRUP.search(spec)
            if m_stir:
                dia = int(m_stir.group(1))
                esp = float((m_stir.group(2) or "15").replace(",", "."))
                esp_m = esp / 100.0 if esp >= 1.0 else esp
                unit_mass = eng.validate_and_get_unit_mass(rec.label, dia)
                dev_len = eng.stirrup_developed_length(b_m, h_m, dia)
                nb_cadres = eng.stirrup_count(span_m, esp_m)
                rows.append(RebarScheduleRow(
                    element_label=rec.label,
                    category=rec.category,
                    bar_mark=f"Pos.{idx} (Cad HA{dia} e={int(round(esp_m*100))})",
                    diameter_mm=dia,
                    nb_elements=effective_qty,
                    nb_bars_per_elem=nb_cadres,
                    developed_length_m=dev_len,
                    bending_schema="Cadre fermé crochets 135° (RPS 2011)",
                ))
                total_kg += effective_qty * nb_cadres * dev_len * unit_mass
                continue

            m_len = RE_EXPLICIT_LENGTH.search(spec)
            explicit_len = (float(m_len.group(1).replace(",", "."))
                            if m_len else None)
            if explicit_len is not None and explicit_len > 25.0:
                explicit_len = round(explicit_len / 100.0, 3)

            groups = RE_BAR_GROUP.findall(spec)
            if not groups:
                continue
            for nb_s, dia_s in groups:
                nb_bars, dia = int(nb_s), int(dia_s)
                unit_mass = eng.validate_and_get_unit_mass(rec.label, dia)
                clear_len = explicit_len if explicit_len else span_m
                dev_len = eng.developed_length_longitudinal(
                    dia, clear_len, add_end_hooks=True,
                    explicit_length=explicit_len is not None)
                rows.append(RebarScheduleRow(
                    element_label=rec.label,
                    category=rec.category,
                    bar_mark=f"Pos.{idx} ({nb_bars}HA{dia})",
                    diameter_mm=dia,
                    nb_elements=effective_qty,
                    nb_bars_per_elem=nb_bars,
                    developed_length_m=dev_len,
                    bending_schema=("Barre droite (L explicite)"
                                    if explicit_len
                                    else "Barre longitudinale + ancrage 15Φ"),
                ))
                total_kg += effective_qty * nb_bars * dev_len * unit_mass

        return rows, round(total_kg, 2)

    # ---------------- Porte 4 ----------------
    def cross_check(self, line: AvantMetreLine, rec: ExtractedPlanRecord
                    ) -> bool:
        return self.engine.verify_element_consistency(
            line, rec.qty_plan_coffrage, rec.qty_table_schedule)

    # ---------------- Utilitaires ----------------
    @staticmethod
    def effective_quantity(rec: ExtractedPlanRecord) -> int:
        """Priorite : files d'axes detectees / occurrences sur le plan de
        coffrage (la plus grande des deux) > tableau de nomenclature."""
        if rec.grid_axes or rec.qty_plan_coffrage:
            return max(len(rec.grid_axes), int(rec.qty_plan_coffrage or 0), 1)
        return int(rec.qty_table_schedule or 1)
