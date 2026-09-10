#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/plan_detector.py -- Detection automatique du type de plan BA.

Analyse un PDF pour determiner :
  - Type (vectoriel / scanne / mixte)
  - Format (A0, A1, A2, A3)
  - Presence de tableaux recapitulatifs
  - Format des tableaux (colonnes / lignes / mixte)
  - Convention de labels (N/P/S ou Poutre/Pot/Semelle)
  - Presence de vues en elevation
  - Presence de cotes d'axes
  - Nombre d'elements detectes (apercu rapide)
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class PlanProfile:
    """Profil d'un plan BA detecte automatiquement."""
    pdf_type: str = "unknown"         # "vector" | "scanned" | "mixed"
    scale: float = 0.0                # mm par point PDF (ex: 0.3528 pour A0)
    page_size: str = "unknown"        # "A0" | "A1" | "A2" | "A3" | "unknown"
    page_width_pt: float = 0.0
    page_height_pt: float = 0.0
    total_pages: int = 0
    has_tables: bool = False
    table_formats: dict = field(default_factory=dict)  # {"poteaux": "column"|"row"|"mixed"}
    table_pages: list = field(default_factory=list)
    label_convention: str = "unknown" # "standard" | "text" | "mixed"
    has_elevations: bool = False
    has_cotes: bool = False
    cote_values: list = field(default_factory=list)   # ex: [3.50, 6.80]
    element_hints: dict = field(default_factory=dict) # {"poutres": 26, "poteaux": 6, ...}
    plan_pages: list = field(default_factory=list)
    scanned_pages: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# -- Patterns de detection --
POUTRE_LABEL_RX = re.compile(
    r"^(B?N\d+(?:BIS)?|PN\d+|PC\d*|LG\d*|CH\d*|BN\d*|PR\d*)$", re.I)
POTEAU_LABEL_RX = re.compile(r"^([PQ]\d+)$", re.I)
SEMELLE_LABEL_RX = re.compile(r"^S(\d+)$", re.I)
VOILE_LABEL_RX = re.compile(r"^V\d+$", re.I)

# Labels textuels (fallback)
TEXT_LABELS_RX = re.compile(
    r"^(?:POUTRE|POTEAU|SEMELLE|LONGRINE|CHAINAGE|BANDE\s*NOYEE|VOILE)\b",
    re.I)

# Tableaux
TABLEAU_KEYWORDS = [
    "TABLEAU DES", "TABLEAU DE", "RECAPITULATIF",
    "DESIGNATION", "NOMBRE", "SECTION", "QUANTITE",
]
ELEVATION_KEYWORDS = [
    "ELEVATION POTEAU", "ELEVATION POUTRE",
    "DETAIL POTEAU", "DETAIL POUTRE",
    "poteaux en", "poutres en",
]
COTE_PATTERN = re.compile(
    r"(?<!\d)(\d{1,2}[.,]\d{2})(?!\d)", re.I)


class PlanDetector:
    """Detecte automatiquement les caracteristiques d'un plan BA."""

    def __init__(self, pdf_path):
        self.pdf_path = Path(pdf_path)
        self._doc = None

    def detect(self) -> PlanProfile:
        """Analyse complete du PDF. Retourne un PlanProfile."""
        import pymupdf

        profile = PlanProfile()
        with pymupdf.open(str(self.pdf_path)) as doc:
            self._doc = doc
            profile.total_pages = len(doc)
            if profile.total_pages == 0:
                profile.warnings.append("PDF vide")
                return profile

            # 1. Format de page
            self._detect_page_size(doc, profile)

            # 2. Vectoriel vs scanne
            self._detect_pdf_type(doc, profile)

            # 3. Parcours rapide pour tables, labels, elevations, cotes
            self._quick_scan(doc, profile)

            # 4. Convention de labels
            self._detect_label_convention(profile)

        return profile

    def _detect_page_size(self, doc, profile):
        """Determine le format de papier a partir des dimensions."""
        page = doc[0]
        rect = page.rect
        profile.page_width_pt = rect.width
        profile.page_height_pt = rect.height
        w_mm = rect.width * 25.4 / 72
        h_mm = rect.height * 25.4 / 72

        # Formats standard (tolerance 10%)
        formats = {
            "A0": (1189, 841),
            "A1": (841, 594),
            "A2": (594, 420),
            "A3": (420, 297),
        }
        for name, (ref_w, ref_h) in formats.items():
            # Comparer dans les deux orientations
            if (abs(w_mm - ref_w) / ref_w < 0.10
                    and abs(h_mm - ref_h) / ref_h < 0.10):
                profile.page_size = name
                profile.scale = ref_w / rect.width  # mm/pt
                return
            if (abs(w_mm - ref_h) / ref_h < 0.10
                    and abs(h_mm - ref_w) / ref_w < 0.10):
                profile.page_size = name
                profile.scale = ref_h / rect.width
                return

        # Si pas de match exact, estimer a partir de la largeur
        # (les plans BA sont souvent en format large personnalise)
        if w_mm > 800:
            profile.page_size = "custom_large"
            # Utiliser la largeur comme reference pour l'echelle
            profile.scale = 1189 / rect.width  # Basé sur A0
        elif w_mm > 500:
            profile.page_size = "custom_medium"
            profile.scale = 841 / rect.width  # Basé sur A1
        else:
            profile.page_size = "unknown"
            profile.scale = 0.3528

    def _detect_pdf_type(self, doc, profile):
        """Determine si le PDF est vectoriel, scanne, ou mixte."""
        scanned_count = 0
        vector_count = 0
        pages_to_check = min(len(doc), 10)  # Echantillonner max 10 pages

        for idx in range(pages_to_check):
            page = doc[idx]
            text = page.get_text().strip()
            images = page.get_images()

            has_text = len(text) > 50  # Au moins 50 chars de texte
            has_large_image = False
            for img in images:
                try:
                    w = img[2] if len(img) > 2 else 0
                    h = img[3] if len(img) > 3 else 0
                    if w * h > 500000:  # > 0.5 Mpx
                        has_large_image = True
                        break
                except Exception:
                    pass

            if has_text and not has_large_image:
                vector_count += 1
            elif has_large_image and not has_text:
                scanned_count += 1
            elif has_text and has_large_image:
                vector_count += 1  # Vectoriel avec fond scanne

        if scanned_count > pages_to_check * 0.7:
            profile.pdf_type = "scanned"
        elif vector_count > pages_to_check * 0.7:
            profile.pdf_type = "vector"
        else:
            profile.pdf_type = "mixed"

    def _quick_scan(self, doc, profile):
        """Parcours rapide pour detecter tables, labels, elevations, cotes."""
        all_labels_poutres = set()
        all_labels_poteaux = set()
        all_labels_semelles = set()
        all_text_labels = set()

        for idx in range(len(doc)):
            page = doc[idx]
            page_num = idx + 1
            text = page.get_text()
            text_upper = text.upper()
            words = page.get_text("words")

            # Detection tableaux
            has_tableau_keyword = any(k in text_upper for k in TABLEAU_KEYWORDS)
            try:
                tables = page.find_tables()
                has_bordered_tables = len(tables.tables) > 0
            except Exception:
                has_bordered_tables = False

            if has_tableau_keyword or has_bordered_tables:
                profile.has_tables = True
                profile.table_pages.append(page_num)

            # Detection elevations
            if any(k in text_upper for k in ELEVATION_KEYWORDS):
                profile.has_elevations = True

            # Detection cotes
            cote_matches = COTE_PATTERN.findall(text)
            for c in cote_matches:
                try:
                    val = float(c.replace(",", "."))
                    if 1.0 <= val <= 20.0:  # Cotes plausibles en metres
                        if val not in profile.cote_values:
                            profile.cote_values.append(val)
                except ValueError:
                    pass

            if profile.cote_values:
                profile.has_cotes = True

            # Collecte des labels
            for w in words:
                word_text = w[4].strip()
                if POUTRE_LABEL_RX.match(word_text):
                    all_labels_poutres.add(word_text.upper())
                elif POTEAU_LABEL_RX.match(word_text):
                    all_labels_poteaux.add(word_text.upper())
                elif SEMELLE_LABEL_RX.match(word_text):
                    all_labels_semelles.add(word_text.upper())
                elif TEXT_LABELS_RX.match(word_text):
                    all_text_labels.add(word_text.upper())
                # Labels composites : N1-(25x40), LG-(25x35), etc.
                m = re.match(
                    r"^(B?N\d+(?:BIS)?|PN\d+|LG\d*|CH\d*|BN\d*)"
                    r"\s*[-–]\s*\(\d+\s*[xX*]\s*\d+\)",
                    word_text, re.I)
                if m:
                    all_labels_poutres.add(m.group(1).upper())

        # Hints
        if all_labels_poutres:
            profile.element_hints["poutres"] = len(all_labels_poutres)
        if all_labels_poteaux:
            profile.element_hints["poteaux"] = len(all_labels_poteaux)
        if all_labels_semelles:
            profile.element_hints["semelles"] = len(all_labels_semelles)

    def _detect_label_convention(self, profile):
        """Determine la convention de labels utilisee."""
        standard_count = sum([
            profile.element_hints.get("poutres", 0),
            profile.element_hints.get("poteaux", 0),
            profile.element_hints.get("semelles", 0),
        ])
        text_count = len(getattr(profile, "_text_labels", set()))

        if standard_count > 0 and text_count == 0:
            profile.label_convention = "standard"
        elif standard_count == 0 and text_count > 0:
            profile.label_convention = "text"
        elif standard_count > 0 and text_count > 0:
            profile.label_convention = "mixed"
        else:
            profile.label_convention = "unknown"

    def __del__(self):
        self._doc = None


def detect_plan(pdf_path) -> PlanProfile:
    """Fonction de commodite pour detecter un plan."""
    return PlanDetector(pdf_path).detect()
