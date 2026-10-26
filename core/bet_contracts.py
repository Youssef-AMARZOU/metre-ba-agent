#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/bet_contracts.py -- Contrats de donnees unifies PlanBA (Vision + BET).

Tout moteur d'extraction (PyMuPDF, Docling, PlanBAVisionExtractor, VLM)
converge vers `DrawingElement` (element structural lu sur le plan), qui est
ensuite converti en `ExtractedPlanRecord` pour passer les 4 portes du
`PlanBAGatekeeper` avant tout calcul dans `MoroccanBETEngine`.

NB : `core.schemas.DrawingElement` (Pydantic) reste le contrat *token*
(texte + bbox + provenance) de l'ingestion historique. Le `DrawingElement`
defini ici est le contrat *structural* (categorie, repere, dimensions,
ferraillage). `to_schema_element()` fait le pont vers l'ancien contrat.

Regle absolue : aucun volume, aucun poids n'est stocke dans ces contrats
en provenance de l'OCR/VLM. Seules les grandeurs LUES sont transportees ;
les calculs sont faits par `MoroccanBETEngine` et les formules Excel.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np


# ============================================================================
# Taxonomie BA Maroc
# ============================================================================

CATEGORIES: Tuple[str, ...] = (
    "semelle", "poteau", "poutre", "longrine", "chainage", "voile",
    "dalle_pleine", "nomenclature", "autre",
)

LABEL_PREFIX_TO_CATEGORY: Dict[str, str] = {
    "P": "poteau",
    "Q": "poteau",
    "S": "semelle",
    "SI": "semelle",
    "SF": "semelle",
    "N": "poutre",
    "B": "poutre",
    "BN": "poutre",
    "PN": "poutre",
    "PC": "poutre",
    "LG": "longrine",
    "CH": "chainage",
    "V": "voile",
    "D": "dalle_pleine",
}

# Regex metier BA Maroc (BAEL 91 mod. 99 / RPS 2000 v2011 / NM 01.4.097)
# Prefixes longs en premier (SI, SF, LG, CH, BN, PN, PC) puis simples.
RE_LABEL = re.compile(
    r"\b(SI|SF|LG|CH|BN|PN|PC|P|S|N|B|V|D|Q)\s*[-_]?\s*(\d+[A-Za-z]?)\b",
    re.IGNORECASE)
RE_SECTION = re.compile(
    r"\b(\d{2,3})\s*[xX×*]\s*(\d{2,3})(?:\s*[xX×*]\s*(\d{2,3}))?\b")
RE_LONG_BAR = re.compile(
    r"(\d+)\s*(?:HA|T|Ø|PHI)\s*(\d{1,2})(?:.*?(?:L\s*=\s*)?(\d+(?:[.,]\d+)?))?",
    re.IGNORECASE)
RE_STIRRUP = re.compile(
    r"(?:CAD|EP|ETRIER|ST)\w*.*?(?:HA|T|Ø|PHI)\s*(\d{1,2})"
    r"\s*(?:[eE=]+\s*(\d{1,2}(?:[.,]\d+)?))?",
    re.IGNORECASE)
RE_EXPLICIT_LENGTH = re.compile(r"L\s*=\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)
RE_BAR_GROUP = re.compile(r"(\d+)\s*(?:HA|T|Ø|PHI)\s*(\d{1,2})", re.IGNORECASE)
RE_AXIS_LETTER = re.compile(r"^[A-Z]$")
RE_AXIS_NUMBER = re.compile(r"^\d{1,2}$")


def category_from_label(label: str) -> str:
    """'P12' -> 'poteau', 'LG3' -> 'longrine', 'XYZ' -> 'autre'."""
    m = RE_LABEL.search(label or "")
    if not m:
        return "autre"
    return LABEL_PREFIX_TO_CATEGORY.get(m.group(1).upper(), "autre")


# ============================================================================
# Contrat structural unifie
# ============================================================================

@dataclass
class DrawingElement:
    """Element structural lu sur un plan (contrat unifie PlanBA).

    `dimensions` contient des grandeurs LUES (m, cm ou mm bruts) ; la
    normalisation en metres est faite par la Porte 1 du Gatekeeper.
    """
    category: str
    label: str
    dimensions: Dict[str, float] = field(default_factory=dict)
    rebar_spec: List[str] = field(default_factory=list)
    quantity: int = 1
    grid_axes: List[str] = field(default_factory=list)
    page_num: int = 1
    bbox: Tuple[int, int, int, int] = (0, 0, 0, 0)     # (x, y, w, h) px
    confidence: float = 1.0
    source_engine: str = "cv_hybrid"
    crop_image_bgr: Optional["np.ndarray"] = field(default=None, repr=False)
    level: str = "Fondations"
    warnings: List[str] = field(default_factory=list)

    def __post_init__(self):
        self.category = (self.category or "autre").strip().lower()
        if self.category not in CATEGORIES:
            self.category = "autre"
        self.label = (self.label or "INCONNU").strip().upper()
        self.quantity = max(1, int(self.quantity or 1))
        self.confidence = float(min(1.0, max(0.0, self.confidence)))

    @property
    def centroid(self) -> Tuple[float, float]:
        x, y, w, h = self.bbox
        return (x + w / 2.0, y + h / 2.0)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("crop_image_bgr", None)
        data["bbox"] = list(self.bbox)
        return data

    def to_schema_element(self):
        """Pont vers `core.schemas.DrawingElement` (contrat token Pydantic)."""
        from core.schemas import DrawingElement as SchemaElement
        x, y, w, h = self.bbox
        text = self.label
        if self.dimensions:
            text += " " + " ".join(
                f"{k}={v}" for k, v in self.dimensions.items())
        if self.rebar_spec:
            text += " | " + " ; ".join(self.rebar_spec)
        return SchemaElement(
            id=f"{self.source_engine}:p{self.page_num}:{self.label}",
            text=text,
            bbox=(float(x), float(y), float(x + w), float(y + h)),
            page=max(1, int(self.page_num)),
            source=self.source_engine,
            confidence=self.confidence,
            provenance={
                "category": self.category,
                "dimensions": dict(self.dimensions),
                "rebar_spec": list(self.rebar_spec),
                "grid_axes": list(self.grid_axes),
                "quantity": self.quantity,
            },
            warnings=list(self.warnings),
        )

    def to_plan_record(self) -> "ExtractedPlanRecord":
        return ExtractedPlanRecord.from_drawing_element(self)


# ============================================================================
# Enregistrement consolide avant les 4 portes
# ============================================================================

@dataclass
class ExtractedPlanRecord:
    """Objet consolide (toutes sources) avant passage dans le Gatekeeper."""
    category: str
    label: str
    level: str = "Fondations"
    grid_axes: List[str] = field(default_factory=list)
    qty_plan_coffrage: Optional[int] = None
    qty_table_schedule: Optional[int] = None
    raw_dimensions: Dict[str, float] = field(default_factory=dict)
    support_left_m: float = 0.30
    support_right_m: float = 0.30
    type_plancher: str = "hourdis"          # 'hourdis' | 'dalle_pleine'
    epaisseur_dalle_m: float = 0.0
    rebar_specs: List[str] = field(default_factory=list)
    crop_image_bgr: Optional["np.ndarray"] = field(default=None, repr=False)
    source_engines: List[str] = field(default_factory=list)
    confidence: float = 1.0
    page_num: int = 1

    def __post_init__(self):
        self.category = (self.category or "autre").strip().lower()
        self.label = (self.label or "INCONNU").strip().upper()
        self.level = (self.level or "Fondations").strip()

    @classmethod
    def from_drawing_element(cls, el: DrawingElement,
                             source_is_schedule: Optional[bool] = None
                             ) -> "ExtractedPlanRecord":
        """Un element lu sur un tableau alimente `qty_table_schedule`,
        un element lu sur une vue en plan alimente `qty_plan_coffrage`."""
        if source_is_schedule is None:
            source_is_schedule = (
                el.category == "nomenclature"
                or "vlm" in el.source_engine
                or "table" in el.source_engine
                or "schedule" in el.source_engine)
        qty_sched = el.quantity if source_is_schedule else None
        qty_plan = None if source_is_schedule else (
            len(el.grid_axes) if el.grid_axes else el.quantity)
        return cls(
            category=el.category,
            label=el.label,
            level=el.level,
            grid_axes=list(el.grid_axes),
            qty_plan_coffrage=qty_plan,
            qty_table_schedule=qty_sched,
            raw_dimensions=dict(el.dimensions),
            rebar_specs=list(el.rebar_spec),
            crop_image_bgr=el.crop_image_bgr,
            source_engines=[el.source_engine],
            confidence=el.confidence,
            page_num=el.page_num,
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("crop_image_bgr", None)
        return data


# ============================================================================
# Sorties du moteur BET
# ============================================================================

@dataclass
class AuditAnomaly:
    """Anomalie bloquante ou avertissement leve par le controleur BET."""
    severity: str          # 'BLOQUANT' | 'AVERTISSEMENT'
    element_label: str
    rule_code: str         # ex: 'RPS2011_RATIO_OUT_OF_BOUNDS', 'INVALID_DIAMETER_OCR'
    message: str
    suggested_fix: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AvantMetreLine:
    """Ligne d'avant-metre normalisee conforme aux colonnes CPS / BPU Maroc."""
    prix_code: str
    category: str
    designation: str
    unite: str              # 'm3', 'm2', 'ml', 'kg'
    nombre: int
    longueur: float
    largeur: float
    hauteur: float
    quantite_partielle: float
    acier_kg: float = 0.0
    ratio_acier_kg_m3: float = 0.0
    grid_locations: List[str] = field(default_factory=list)
    label: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RebarScheduleRow:
    """Ligne unitaire de nomenclature d'acier (FeE500 - NM 01.4.097)."""
    element_label: str
    category: str
    bar_mark: str
    diameter_mm: int
    nb_elements: int
    nb_bars_per_elem: int
    developed_length_m: float
    bending_schema: str = "Droit + Crosses 135°"

    @property
    def total_length_m(self) -> float:
        return round(self.nb_elements * self.nb_bars_per_elem
                     * self.developed_length_m, 2)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================================
# Pont vers le contrat legacy `plan_data` (catalogue_types / implantations)
# ============================================================================

def _bars_from_specs(specs: List[str]) -> Tuple[List[dict], Optional[dict]]:
    """Decode '6HA14' -> [{'nb':6,'phi':14}] et 'Cad HA8 e=15' -> {'phi':8,'esp':0.15}."""
    long_bars: List[dict] = []
    cadres: Optional[dict] = None
    for spec in specs:
        m_st = RE_STIRRUP.search(spec)
        if m_st:
            esp = float((m_st.group(2) or "15").replace(",", "."))
            if esp >= 1.0:
                esp = esp / 100.0
            if cadres is None:
                cadres = {"phi": int(m_st.group(1)), "esp": round(esp, 3)}
            continue
        for nb, phi in RE_BAR_GROUP.findall(spec):
            long_bars.append({"nb": int(nb), "phi": int(phi)})
    return long_bars, cadres


def records_to_legacy_plan_data(records: List[ExtractedPlanRecord],
                                projet: str = "Projet extrait (Vision)",
                                normalizer=None) -> dict:
    """Convertit les enregistrements certifies en `plan_data` legacy pour
    alimenter sans rupture `MetreGenerator`, `verifier_livrables_ou_lever`
    et la vue de correction de l'UI.

    `normalizer(label, category, raw_dims) -> dims_m` : Porte 1 du
    Gatekeeper (injectee pour eviter un import circulaire).
    """
    cat: Dict[str, Any] = {
        "semelles": {}, "poteaux": {}, "poutres": {},
        "longrines": {}, "chainages": {}, "voiles": {}, "murs": {},
    }
    impl: Dict[str, list] = {
        "semelles": [], "poteaux": [], "poutres": [],
        "longrines": [], "chainages": [], "voiles": [],
    }
    avertissements: List[str] = []

    for rec in records:
        dims = (normalizer(rec.label, rec.category, rec.raw_dimensions)
                if normalizer else dict(rec.raw_dimensions))
        long_bars, cadres = _bars_from_specs(rec.rebar_specs)
        qty = (len(rec.grid_axes) if rec.grid_axes
               else (rec.qty_plan_coffrage or rec.qty_table_schedule or 1))
        axes = rec.grid_axes or [""] * qty

        def _impl(fam: str):
            for i, ax in enumerate(axes, start=1):
                axe, _, file_ = (ax.partition("-") if ax else ("", "", ""))
                impl[fam].append({
                    "id": f"{rec.label}_{i}", "type": rec.label,
                    "axe": axe, "file": file_, "level": rec.level,
                })

        if rec.category == "semelle":
            fx = long_bars[0] if long_bars else {"nb": 0, "phi": 0}
            fy = long_bars[1] if len(long_bars) > 1 else fx
            cat["semelles"][rec.label] = {
                "a": dims.get("A", dims.get("a", 0.0)),
                "b": dims.get("B", dims.get("b", 0.0)),
                "h": dims.get("H", dims.get("h_patin", dims.get("h", 0.0))),
                "ferr_x": fx, "ferr_y": fy, "level": rec.level,
            }
            _impl("semelles")
        elif rec.category == "poteau":
            cat["poteaux"][rec.label] = {
                "a": dims.get("b", 0.0), "b": dims.get("h", 0.0),
                "hauteur": dims.get("L", dims.get("H", 0.0)),
                "long_bars": long_bars,
                "cadres": cadres or {"phi": 0, "esp": 0.15},
                "level": rec.level,
            }
            _impl("poteaux")
        elif rec.category in ("poutre", "longrine", "chainage"):
            fam = {"poutre": "poutres", "longrine": "longrines",
                   "chainage": "chainages"}[rec.category]
            entry = {
                "b": dims.get("b", 0.0), "h": dims.get("h", 0.0),
                "portee": dims.get("L", dims.get("portee")),
                "filants_inf": long_bars[:1],
                "filants_sup": long_bars[1:2],
                "cadres": cadres or {"phi": 0, "esp": 0.15},
                "level": rec.level,
            }
            cat[fam][rec.label] = entry
            _impl(fam)
        elif rec.category == "voile":
            cat["voiles"][rec.label] = {
                "ep": dims.get("b", dims.get("e", 0.0)),
                "longueur": dims.get("L", 0.0),
                "hauteur": dims.get("h", dims.get("H", 0.0)),
                "level": rec.level,
            }
            _impl("voiles")
        else:
            avertissements.append(
                f"{rec.label} ({rec.category}) : categorie non routee vers "
                f"le catalogue legacy.")

    return {
        "projet": {"nom": projet, "date": None},
        "catalogue_types": cat,
        "implantations": impl,
        "_meta": {
            "moteur": "PlanBAVisionExtractor + MoroccanBETEngine",
            "avertissements": avertissements,
            "hypotheses": [],
            "pages_ocr": [], "pages_tableau": [], "pages_plan": [],
            "pages_ignorees": 0, "total_pages_scanned": 0,
            "nb_mots_lus": 0,
        },
    }
