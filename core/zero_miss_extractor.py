#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/zero_miss_extractor.py — Pipeline d'extraction multi-passes "Zero-Miss".

Protocole d'extraction exhaustif pour plans BA :
  Passe 1 : Extraction des tableaux structures (Semelles, Poteaux) → dict de reference
  Passe 2 : Parsing de la legende (N=Poutre, P=Poteau, LG=Longrine, etc.)
  Passe 3 : Detection exhaustive de TOUTES les occurrences d'etiquettes sur chaque vue
  Passe 4 : Extraction de la trame d'axes (files + cotes) → longueurs reelles
  Passe 5 : Fusion tables+legend+occurrences+axes → calcul des volumes reels
  Passe 6 : Validation zero-miss (pas de valeurs codrees en dur)

ZERO MOCK : si une valeur n'est pas lisible → null + "statut": "manquant".
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ============================================================================
# Schema de sortie
# ============================================================================

@dataclass
class ElementMetre:
    """Un element de metree avec traçabilite complete."""
    repere: str
    famille: str  # SEMELLE, POTEAU, POUTRE, LONGRINE, CHAINAGE, MUR, VOILE, etc.
    dimensions: dict[str, Any] = field(default_factory=dict)
    ferraillage: dict[str, Any] = field(default_factory=dict)
    occurrences: list[dict[str, Any]] = field(default_factory=list)
    longueur_calculee_m: Optional[float] = None
    source_longueur: Optional[str] = None
    statut: str = "extrait"  # extrait | manquant | ambig | a_verifier
    sources: list[str] = field(default_factory=list)  # traçabilité

    def to_dict(self) -> dict:
        d = {
            "repere": self.repere,
            "famille": self.famille,
            "dimensions": self.dimensions,
            "ferraillage": self.ferraillage,
            "occurrences": self.occurrences,
            "statut": self.statut,
            "sources": self.sources,
        }
        if self.longueur_calculee_m is not None:
            d["longueur_calculee_m"] = self.longueur_calculee_m
        if self.source_longueur:
            d["source_longueur"] = self.source_longueur
        return d


@dataclass
class ExtractionResult:
    """Resultat complet de l'extraction multi-passes."""
    semelles: list[ElementMetre] = field(default_factory=list)
    poteaux: list[ElementMetre] = field(default_factory=list)
    poutres: list[ElementMetre] = field(default_factory=list)
    longrines: list[ElementMetre] = field(default_factory=list)
    chainages: list[ElementMetre] = field(default_factory=list)
    murs: list[ElementMetre] = field(default_factory=list)
    voiles: list[ElementMetre] = field(default_factory=list)
    autres: list[ElementMetre] = field(default_factory=list)
    legend: dict[str, str] = field(default_factory=dict)
    trame_axes: dict[str, Any] = field(default_factory=dict)
    rapport_ecart: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "semelles": [e.to_dict() for e in self.semelles],
            "poteaux": [e.to_dict() for e in self.poteaux],
            "poutres": [e.to_dict() for e in self.poutres],
            "longrines": [e.to_dict() for e in self.longrines],
            "chainages": [e.to_dict() for e in self.chainages],
            "murs": [e.to_dict() for e in self.murs],
            "voiles": [e.to_dict() for e in self.voiles],
            "autres": [e.to_dict() for e in self.autres],
            "legend": self.legend,
            "trame_axes": self.trame_axes,
            "rapport_ecart": self.rapport_ecart,
            "warnings": self.warnings,
        }


# ============================================================================
# Patterns de detection
# ============================================================================

# Legende
LEGEND_PREFIX_RX = re.compile(
    r"^(N|P|LG|BN|CH|V|R|M|C|Lt|RD|S|D|ESC|SF)\s*[:=\-–]\s*(.+)",
    re.I,
)

# Tableau structures
SEMELLE_REPERE_RX = re.compile(r"^S(\d+)$", re.I)
POTEAU_REPERE_RX = re.compile(r"^([PQ]\d+)$", re.I)
SECTION_CM_RX = re.compile(r"(\d{2,3})\s*[xX*]\s*(\d{2,3})")
FERRA_ARM_RX = re.compile(r"(\d+)\s*(?:HA|T)\s*(\d+)", re.I)
TRIPLET_RX = re.compile(r"(\d+)\s*[xX*]\s*(\d+)\s*[xX*]\s*(\d+)")

# Occurrences sur vues
# Pattern : prefixe + numero + optionnellement -(section)
# Ex: N10-(25X40), BN-(25X30), LG-25x35-, P1, S1, V1
OCCURRENCE_RX = re.compile(
    r"(?:(?:^|\s)(N\d+(?:BIS)?|PN\d+|PC\d*|LG\d*|CH\d*|BN\d*|PR\d*"
    r"|P\d+|Q\d+|S\d+|D\d+|V\d+|R\d+|M\d+|C\d+|Lt\d*|RD\d*"
    r"|ESC\d+|SF)"
    r"(?:\s*[-–]\s*\((\d+)\s*[xX*]\s*(\d+)\))?)",
    re.I,
)

# Trame d'axes : cotes entre files (ex: 441, 438, 460)
AXIS_DIM_RX = re.compile(r"(\d{2,4}(?:\.\d+)?)")
AXIS_LETTER_RX = re.compile(r"^[A-T]$")
AXIS_NUM_RX = re.compile(r"^(?:[1-9]|1\d|20)$")


# ============================================================================
# Passe 1 : Extraction des tableaux structures
# ============================================================================

def extract_structured_tables(doc) -> dict[str, dict]:
    """Extrait les tableaux structures (Semelles, Poteaux) comme dictionnaires
    de reference AVANT de lire les vues en plan.

    Retourne:
        {
            "semelles": {"S1": {a, b, h, ferr_x, ferr_y, ...}, ...},
            "poteaux": {"P1": {section, barres_long, cadres, ...}, ...},
        }
    """
    result = {"semelles": {}, "poteaux": {}}

    for page_num in range(len(doc)):
        page = doc[page_num]
        try:
            tabs = page.find_tables()
        except Exception:
            continue

        for t in tabs.tables:
            try:
                data = t.extract()
            except Exception:
                continue
            if not data or len(data) < 2:
                continue

            # Parser toutes les tables — la detection du type est interne
            _parse_semelles_table(data, result["semelles"], page_num + 1)
            _parse_poteaux_table(data, result["poteaux"], page_num + 1)

    return result


def _parse_semelles_table(data: list, semelles: dict, page_num: int):
    """Parse un tableau de semelles au format riche ou compact."""
    # Detecter si c'est un tableau de semelles
    all_text = " ".join(str(c or "") for row in data for c in row).upper()
    if "SEMELLE" not in all_text and not any(
        SEMELLE_REPERE_RX.match((c or "").strip())
        for row in data for c in row
    ):
        return

    # Format riche : Repere | AxB | H | Ferraillage selon x | selon y
    hdr_idx = None
    for ri, row in enumerate(data):
        cells = [(c or "").strip().lower() for c in row]
        if any(c.startswith("rep") for c in cells):
            hdr_idx = ri
            break

    if hdr_idx is not None:
        hdr = [(c or "").strip().lower() for c in data[hdr_idx]]
        col_rep = col_axb = col_h = col_fx = col_fy = None
        for ci, c in enumerate(hdr):
            if c.startswith("rep"):
                col_rep = ci
            elif "(axb)" in c or "a x b" in c or "a*b" in c:
                col_axb = ci
            elif c.startswith("h") and col_h is None:
                col_h = ci
            elif "selon x" in c or "fx" in c:
                col_fx = ci
            elif "selon y" in c or "fy" in c:
                col_fy = ci

        if col_rep is not None and col_axb is not None:
            for row in data[hdr_idx + 1:]:
                cells = [(c or "").strip() for c in row]
                if col_rep >= len(cells):
                    continue
                m = SEMELLE_REPERE_RX.match(cells[col_rep])
                if not m:
                    continue
                ref = f"S{m.group(1)}"
                spec: dict[str, Any] = {"source": f"Tableau Semelles, page {page_num}"}

                if col_axb < len(cells):
                    md = SECTION_CM_RX.match(cells[col_axb])
                    if md:
                        spec["a"] = int(md.group(1)) / 100  # cm → m
                        spec["b"] = int(md.group(2)) / 100
                if col_h is not None and col_h < len(cells):
                    h_str = cells[col_h].replace(",", ".").strip()
                    if h_str.replace(".", "").isdigit():
                        h_val = float(h_str)
                        spec["h"] = h_val / 100 if h_val > 10 else h_val
                if col_fx is not None and col_fx < len(cells):
                    mf = FERRA_ARM_RX.search(cells[col_fx])
                    if mf:
                        spec["ferr_x"] = {"nb": int(mf.group(1)),
                                          "phi": int(mf.group(2))}
                if col_fy is not None and col_fy < len(cells):
                    mf = FERRA_ARM_RX.search(cells[col_fy])
                    if mf:
                        spec["ferr_y"] = {"nb": int(mf.group(1)),
                                          "phi": int(mf.group(2))}

                if spec.get("a") and spec.get("b"):
                    semelles[ref] = spec
            return

    # Format compact : S1 | 90x90x25
    for row in data:
        cells = [(c or "").strip() for c in row]
        if not cells:
            continue
        rep = cells[0].upper().replace(" ", "")
        m = SEMELLE_REPERE_RX.match(rep)
        if not m:
            continue
        ref = f"S{m.group(1)}"
        rest = " ".join(cells[1:])
        td = TRIPLET_RX.search(rest)
        if td:
            a = int(td.group(1)) / 100
            b = int(td.group(2)) / 100
            h = int(td.group(3)) / 100
            spec: dict[str, Any] = {
                "a": a, "b": b, "h": h,
                "source": f"Tableau Semelles (compact), page {page_num}",
            }
            mf = FERRA_ARM_RX.search(rest)
            if mf:
                spec["ferr_x"] = {"nb": int(mf.group(1)),
                                  "phi": int(mf.group(2))}
            semelles[ref] = spec


def _parse_poteaux_table(data: list, poteaux: dict, page_num: int):
    """Parse un tableau de poteaux, supporte en-tetes P1..Pn en colonnes
    avec P1 non labelise (premiere colonne de donnees)."""
    # Detecter si c'est un tableau de poteaux
    all_text = " ".join(str(c or "") for row in data for c in row).upper()
    has_pot_keyword = "POTEAU" in all_text or "Cad" in all_text
    has_pot_reps = any(
        POTEAU_REPERE_RX.match((c or "").strip())
        for row in data for c in row
    )
    if not has_pot_keyword and not has_pot_reps:
        return

    # Detection en-tete : chercher des labels Pk/Qk en en-tete
    hdr_idx = None
    poteau_cols: dict[str, int] = {}
    lignes_reperes: dict[str, int] = {}

    for ri, row in enumerate(data):
        cells = [(c or "").strip().upper() for c in row]
        cols = {c: ci for ci, c in enumerate(cells)
                if POTEAU_REPERE_RX.match(c)}
        if len(cols) >= 2 or (
            len(cols) >= 1
            and any("POTEAUX" in c or "POTEAU" in c for c in cells)
        ):
            hdr_idx = ri
            poteau_cols = cols
            break
        if cells:
            m = POTEAU_REPERE_RX.match(cells[0])
            if m:
                lignes_reperes[m.group(1).upper()] = ri

    n_found = 0

    # Disposition colonnes (en-tete avec P1..P6)
    if hdr_idx is not None:
        max_col = max(poteau_cols.values()) if poteau_cols else 0
        has_first_data_col = (
            max_col >= 2
            and len(data) > hdr_idx + 1
            and len(data[hdr_idx + 1]) > 1
            and data[hdr_idx + 1][1]
        )

        for row in data[hdr_idx + 1:]:
            row_cells = [(c or "").strip() for c in row]
            for label, ci in poteau_cols.items():
                if ci >= len(row):
                    continue
                cell_text = row[ci] or ""
                if _parse_poteau_cell(label, cell_text, poteaux, page_num):
                    n_found += 1
            if has_first_data_col and len(row) > 1:
                cell_text = row[1] or ""
                if (SECTION_CM_RX.search(cell_text)
                        or FERRA_ARM_RX.search(cell_text)
                        or "Cad" in cell_text):
                    if _parse_poteau_cell("P1", cell_text, poteaux, page_num):
                        n_found += 1

    # Disposition lignes
    for label, ri in lignes_reperes.items():
        if ri < len(data):
            row = data[ri]
            rest = " ".join(str(c or "") for c in row[1:])
            if _parse_poteau_cell(label, rest, poteaux, page_num):
                n_found += 1

    return n_found > 0


def _parse_poteau_cell(label: str, cell_text: str, poteaux: dict,
                       page_num: int) -> bool:
    """Parse une cellule de poteau et l'ajoute au dictionnaire."""
    if not cell_text or not label:
        return False
    # Normaliser : remplacer \n par espace, supprimer doublons
    cell = re.sub(r"\s+", " ", cell_text).strip()
    if not cell:
        return False

    spec: dict[str, Any] = {
        "source": f"Tableau Poteaux, page {page_num}",
    }

    sm = SECTION_CM_RX.search(cell)
    if not sm:
        # Fallback : section sur lignes separatees (ex: "25 30" apres normalisation)
        sm = re.search(r"\b(\d{2,3})\s+(\d{2,3})\b", cell)
        if sm and int(sm.group(1)) > 10 and int(sm.group(2)) > 10:
            spec["a"] = int(sm.group(1)) / 100
            spec["b"] = int(sm.group(2)) / 100
        else:
            sm = None
    if sm and not spec.get("a"):
        spec["a"] = int(sm.group(1)) / 100
        spec["b"] = int(sm.group(2)) / 100

    am = FERRA_ARM_RX.search(cell)
    if am:
        spec["barres_long"] = {
            "nb": int(am.group(1)),
            "phi": int(am.group(2)),
        }

    cm = re.search(r"(?:Cad\+?(\d+Ep?)|(\d+)\s*CAD)", cell, re.I)
    if cm:
        spec["cadres"] = cm.group(1) or cm.group(2)

    em = re.search(r"(?:e|esp)\s*=?\s*(\d+)", cell, re.I)
    if em:
        spec["espacement_cm"] = int(em.group(1))

    if "T6" in cell.upper():
        spec["type_acier"] = "T6"
    elif "T8" in cell.upper():
        spec["type_acier"] = "T8"

    if spec.get("a") and spec.get("b"):
        poteaux[label] = spec
        return True
    return False


# ============================================================================
# Passe 2 : Parsing de la legende
# ============================================================================

def extract_legend(doc) -> dict[str, str]:
    """Extrait la legende du plan (prefixe → type d'element).

    Cherche des patterns comme "N=Poutre", "LG=Longrine", etc.
    sur toutes les pages.
    """
    legend = {}

    # Legende par defaut (conventions marocaines)
    default_legend = {
        "N": "Poutre",
        "P": "Poteau",
        "LG": "Longrine",
        "BN": "Bande noyee",
        "CH": "Chainage",
        "V": "Voile",
        "R": "Raidisseur",
        "M": "Massif",
        "C": "Console/Corbeau",
        "Lt": "Linteau",
        "RD": "Regard",
        "S": "Semelle",
        "D": "Dalle",
        "ESC": "Escalier",
        "Q": "Poteau",
        "PN": "Poutre",
        "PC": "Poutre de couronnement",
        "SF": "Semelle filante",
    }
    legend.update(default_legend)

    # Chercher des legendes explicites dans le texte des pages
    for page_num in range(min(len(doc), 10)):  # 10 premieres pages
        page = doc[page_num]
        try:
            text = page.get_text("text")
        except Exception:
            continue

        for line in text.split("\n"):
            line = line.strip()
            m = LEGEND_PREFIX_RX.match(line)
            if m:
                prefix = m.group(1).upper().strip()
                desc = m.group(2).strip()
                if prefix not in legend or len(desc) > len(legend[prefix]):
                    legend[prefix] = desc

    return legend


# ============================================================================
# Passe 3 : Detection exhaustive des occurrences sur chaque vue
# ============================================================================

def extract_all_occurrences(doc) -> list[dict[str, Any]]:
    """Detecte TOUTES les occurrences d'etiquettes sur CHAQUE vue.

    Chaque occurrence geographique = 1 element physique distinct.
    Retourne une liste de dictionnaires avec :
      - repere: str (ex: "N10")
      - section: str|None (ex: "25x40")
      - page: int
      - x, y: float (coordonnees)
      - vue: str (ex: "Fondation", "RDC", "Etage")
    """
    occurrences = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        page_label = f"page_{page_num + 1}"

        # Detecter le type de vue
        vue = _detect_vue_type(page)

        # Extraire tous les mots avec positions
        try:
            words = page.get_text("words")
        except Exception:
            continue

        # Construire le texte avec positions
        for w in words:
            if len(w) < 5:
                continue
            x0, y0, x1, y1 = w[0], w[1], w[2], w[3]
            word_text = str(w[4]) if len(w) > 4 else ""
            word_text = word_text.strip()

            if not word_text:
                continue

            # Chercher des occurrences d'etiquettes
            # Pattern 1 : etiquette avec section (ex: N10-(25X40))
            m = OCCURRENCE_RX.search(word_text)
            if m:
                ref = m.group(1).upper()
                section = None
                if m.group(2) and m.group(3):
                    section = f"{m.group(2)}x{m.group(3)}"

                occurrences.append({
                    "repere": ref,
                    "section": section,
                    "page": page_num + 1,
                    "x": (x0 + x1) / 2,
                    "y": (y0 + y1) / 2,
                    "vue": vue,
                    "mot": word_text,
                })

        # Aussi chercher dans le texte combine de la page
        # pour les etiquettes qui peuvent etre sur des lignes separatees
        text = page.get_text("text")
        for line in text.split("\n"):
            line = line.strip()
            for m in OCCURRENCE_RX.finditer(line):
                ref = m.group(1).upper()
                section = None
                if m.group(2) and m.group(3):
                    section = f"{m.group(2)}x{m.group(3)}"

                # Verifier si pas deja ajoute depuis les mots
                already = any(
                    o["repere"] == ref and o["page"] == page_num + 1
                    for o in occurrences
                )
                if not already:
                    occurrences.append({
                        "repere": ref,
                        "section": section,
                        "page": page_num + 1,
                        "x": 0,
                        "y": 0,
                        "vue": vue,
                        "mot": line[:50],
                    })

    return occurrences


def _detect_vue_type(page) -> str:
    """Detecte le type de vue (Fondation, RDC, Etage, etc.)."""
    try:
        text = page.get_text("text").upper()
    except Exception:
        return "inconnu"

    if any(kw in text for kw in ["FONDATION", "FOND.", "PL.FOND"]):
        return "Fondation"
    elif any(kw in text for kw in ["RDC", "REZ-DE-CHAUSS", "REZ DE CHAUSS"]):
        return "RDC"
    elif any(kw in text for kw in ["1ER", "1ER ETAGE", "PREMIER", "ETAGE"]):
        return "Etage"
    elif any(kw in text for kw in ["PL.HT", "PLANCHER HAUT", "TOITURE"]):
        return "Toiture"
    else:
        return "inconnu"


# ============================================================================
# Passe 4 : Extraction de la trame d'axes
# ============================================================================

def extract_axis_grid(doc) -> dict[str, Any]:
    """Extrait la trame d'axes (files + cotes) pour calculer les longueurs reelles.

    Cherche les cotes entre files (441, 438, 460, ...) dans le texte des pages.
    Ces cotes sont typiquement en cm et entre 100 et 800.
    """
    result = {
        "files": {},
        "niveaux": {},
        "cotes_files": [],
        "cotes_niveaux": [],
    }

    # Chercher les cotes d'axes dans les tableaux et texte des pages
    seen_cotes = set()
    for page_num in range(min(len(doc), 10)):
        page = doc[page_num]
        try:
            tabs = page.find_tables()
        except Exception:
            continue

        for t in tabs.tables:
            data = t.extract()
            if not data:
                continue
            for row in data:
                for cell in row:
                    cell_str = str(cell or "")
                    # Diviser la cellule par lignes
                    lines = cell_str.split("\n")
                    for line in lines:
                        nums = re.findall(r"\b(\d+(?:\.\d+)?)\b", line)
                        if len(nums) < 5:
                            continue
                        # Filtrer les cotes (100-600 cm, exclure marges < 100)
                        cotes = []
                        for n in nums:
                            try:
                                val = float(n)
                                if 100 <= val <= 600:
                                    cotes.append(val)
                            except ValueError:
                                pass
                        # Si on a 5+ cotes > 100, c'est la ligne des cotes d'axes
                        if len(cotes) >= 5 and not result["cotes_files"]:
                            # Prendre toutes les cotes de CETTE ligne
                            all_cotes = []
                            for n in nums:
                                try:
                                    val = float(n)
                                    if val > 0:
                                        all_cotes.append(val)
                                except ValueError:
                                    pass
                            # Detecter la fin de la sequence (valeur qui se repete)
                            # La sequence cotes se termine quand on revoit la premiere valeur
                            if len(all_cotes) >= 10:
                                first = all_cotes[0]
                                for idx in range(1, len(all_cotes)):
                                    if all_cotes[idx] == first:
                                        all_cotes = all_cotes[:idx]
                                        break
                            for v in all_cotes:
                                if v not in seen_cotes:
                                    seen_cotes.add(v)
                                    result["cotes_files"].append(v)

        # Aussi chercher des cotes de niveaux
        # Les niveaux ont des cotes entre 250-500 cm (hauteurs d'etage)
        if not result["cotes_niveaux"]:
            for row in (tabs.tables[0].extract() if tabs.tables else []):
                for cell in row:
                    cell_str = str(cell or "")
                    nums = re.findall(r"\b(\d{3,4})\b", cell_str)
                    for n in nums:
                        try:
                            val = float(n)
                            if 250 <= val <= 500:
                                if val not in seen_cotes:
                                    seen_cotes.add(val)
                                    result["cotes_niveaux"].append(val)
                        except ValueError:
                            pass
            if len(result["cotes_niveaux"]) < 2:
                result["cotes_niveaux"] = []

    # Convertir les cotes en positions cumulees
    if result["cotes_files"]:
        cumulative = 0
        letters = "ABCDEFGHIJKLMN"
        for i, cote in enumerate(result["cotes_files"]):
            if i < len(letters):
                pos = cote / 100  # cm → m
                cumulative += pos
                result["files"][letters[i]] = round(cumulative, 3)

    if result["cotes_niveaux"]:
        cumulative = 0
        for i, cote in enumerate(result["cotes_niveaux"]):
            pos = cote / 100
            cumulative += pos
            result["niveaux"][f"{i + 1:02d}"] = round(cumulative, 3)

    return result


# ============================================================================
# Passe 5 : Fusion et calcul du metree
# ============================================================================

def merge_and_calculate(
    tables: dict[str, dict],
    occurrences: list[dict[str, Any]],
    legend: dict[str, str],
    axis_grid: dict[str, Any],
) -> ExtractionResult:
    """Fusionne toutes les passes et calcule les metrees reels.

    Regles :
    - Chaque occurrence geographique = 1 element distinct
    - Dimensions depuis les tableaux structures (Passe 1)
    - Longueur depuis la trame d'axes (Passe 4)
    - JAMAIS de valeur par defaut
    """
    result = ExtractionResult()
    result.legend = legend
    result.trame_axes = axis_grid

    # Indexer les occurrences par repere
    occ_by_ref: dict[str, list[dict]] = {}
    for occ in occurrences:
        ref = occ["repere"]
        occ_by_ref.setdefault(ref, []).append(occ)

    # Traiter les semelles
    for ref, spec in tables.get("semelles", {}).items():
        elem = ElementMetre(
            repere=ref,
            famille="SEMELLE",
            dimensions={
                "a": spec.get("a"),
                "b": spec.get("b"),
                "h": spec.get("h"),
            },
            ferraillage={
                "ferr_x": spec.get("ferr_x"),
                "ferr_y": spec.get("ferr_y"),
            },
            statut="extrait" if spec.get("a") and spec.get("b") else "manquant",
            sources=[spec.get("source", "Tableau Semelles")],
        )
        # Compter les occurrences
        for occ in occ_by_ref.get(ref, []):
            elem.occurrences.append({
                "page": occ["page"],
                "vue": occ["vue"],
                "x": occ["x"],
                "y": occ["y"],
            })
        if not elem.occurrences:
            elem.warnings = [f"{ref}: aucune occurrence trouvee sur les vues"]
        result.semelles.append(elem)

    # Traiter les poteaux
    for ref, spec in tables.get("poteaux", {}).items():
        elem = ElementMetre(
            repere=ref,
            famille="POTEAU",
            dimensions={
                "a": spec.get("a"),
                "b": spec.get("b"),
            },
            ferraillage={
                "barres_long": spec.get("barres_long"),
                "nb_cadres": spec.get("nb_cadres"),
                "espacement_cm": spec.get("espacement_cm"),
                "type_acier": spec.get("type_acier"),
            },
            statut="extrait" if spec.get("a") and spec.get("b") else "manquant",
            sources=[spec.get("source", "Tableau Poteaux")],
        )
        for occ in occ_by_ref.get(ref, []):
            elem.occurrences.append({
                "page": occ["page"],
                "vue": occ["vue"],
                "x": occ["x"],
                "y": occ["y"],
            })
        result.poteaux.append(elem)

    # Traiter les poutres et autres elements depuis les occurrences
    # Grouper par repere UNIQUE (chaque numero = un element distinct)
    poutre_groups: dict[str, list[dict]] = {}
    for occ in occurrences:
        ref = occ["repere"]
        # Garder le numero individuel : N1, N2, LG1, LG2, CH1, BN1
        poutre_groups.setdefault(ref, []).append(occ)

    # Creer les elements poutres
    for ref, occs in poutre_groups.items():
        # Determiner la famille depuis le prefixe (supporte N1, LG1, CH1, BN1)
        fam = "POUTRE"
        ref_upper = ref.upper()
        # Exclure les poteaux (P1-P6) et semelles (S1-S3) — viennent des tableaux
        if ref_upper.startswith("P") and not ref_upper.startswith("PN"):
            continue  # Poteaux geres par le tableau structure
        if ref_upper.startswith("S"):
            continue  # Semelles gerees par le tableau structure
        if ref_upper.startswith("LG"):
            fam = "LONGRINE"
        elif ref_upper.startswith("CH"):
            fam = "CHAINAGE"
        elif ref_upper.startswith("BN"):
            fam = "MUR"  # Bande noyee = mur
        elif ref_upper.startswith("V"):
            fam = "VOILE"
        elif ref_upper.startswith("R"):
            fam = "RAIDISSEUR"
        elif ref_upper.startswith("M"):
            fam = "MASSIF"
        elif ref_upper.startswith("D"):
            fam = "DALLE"
        elif ref_upper.startswith("ESC"):
            fam = "ESCALIER"

        # Section depuis la premiere occurrence
        section = occs[0].get("section")

        # Calculer la longueur depuis la trame d'axes
        longueur = _calculate_beam_length(occs, axis_grid)

        elem = ElementMetre(
            repere=ref,
            famille=fam,
            dimensions={"section": section},
            longueur_calculee_m=longueur,
            source_longueur="trame axes" if longueur else None,
            statut="extrait" if section else "a_verifier",
            sources=[f"Vue {occs[0]['vue']}, page {occs[0]['page']}"],
        )
        for occ in occs:
            elem.occurrences.append({
                "page": occ["page"],
                "vue": occ["vue"],
                "x": occ["x"],
                "y": occ["y"],
            })

        if fam == "POUTRE":
            result.poutres.append(elem)
        elif fam == "LONGRINE":
            result.longrines.append(elem)
        elif fam == "CHAINAGE":
            result.chainages.append(elem)
        elif fam == "MUR":
            result.murs.append(elem)

    # Generer le rapport d'ecart
    result.rapport_ecart = _generate_ecart_report(
        tables, occurrences, result
    )

    return result


def _calculate_beam_length(
    occs: list[dict], axis_grid: dict
) -> Optional[float]:
    """Calcule la longueur reelle d'une poutre/longrine/chainage
    en fonction de sa position dans la trame d'axes.

    Algorithme :
    1. Identifier les positions X des files d'axes sur le plan
    2. Pour chaque occurrence, trouver la file la plus proche
    3. Calculer la distance reelle entre les files extremites
       en utilisant les cotes du tableau d'axes
    """
    files = axis_grid.get("files", {})
    cotes = axis_grid.get("cotes_files", [])
    if not files or len(files) < 2:
        return None

    x_positions = [o["x"] for o in occs if o["x"] > 0]
    if len(x_positions) < 2:
        return None

    x_min, x_max = min(x_positions), max(x_positions)

    # Identifier les positions X des files sur le plan
    # Les files apparaissent en colonnes verticales sur le plan
    # On cherche les positions X des labels A, B, C, ... dans le texte
    # On utilise les occurrences comme reference

    # Trier les files par position cumulative
    sorted_files = sorted(files.items(), key=lambda kv: kv[1])

    # Calculer les positions X des files en pixels
    # Calibration: le premier fichier (A) a la plus petite position X
    #              le dernier fichier (L) a la plus grande position X
    # Les positions intermediaires sont proportionnelles

    # Trouver les positions X extremes des fichiers dans les occurrences
    # (les fichiers apparaissent comme labels sur le plan)
    file_x_map = {}  # file_letter → x_position_pixels

    # Utiliser les positions X des occurrences pour calibrer
    # Chaque occurrence est pres d'un fichier d'axe
    # On suppose que les fichiers sont uniformement distribues en X

    # Positions cumulees des files
    file_cumul = {name: pos for name, pos in sorted_files}
    first_pos = sorted_files[0][1]
    last_pos = sorted_files[-1][1]
    total_span = last_pos - first_pos
    if total_span <= 0:
        return None

    # Calibrer: x_min → file A, x_max → file L
    # Les positions intermediaires sont proportionnelles
    file_pixel_positions = {}
    for name, cumul_pos in file_cumul.items():
        # Position relative dans la trame (0 à 1)
        rel = (cumul_pos - first_pos) / total_span
        # Position en pixels
        px = x_min + rel * (x_max - x_min)
        file_pixel_positions[name] = px

    # Pour chaque occurrence, trouver la file la plus proche
    occ_files = set()
    for occ in occs:
        x = occ["x"]
        best_name = None
        best_dist = float("inf")
        for name, px in file_pixel_positions.items():
            dist = abs(x - px)
            if dist < best_dist:
                best_dist = dist
                best_name = name
        if best_name:
            occ_files.add(best_name)

    if len(occ_files) < 2:
        return None

    # Calculer la distance reelle entre les files extremites
    # en utilisant les cotes cumulees
    occ_file_positions = sorted(
        [file_cumul[n] for n in occ_files]
    )
    length = occ_file_positions[-1] - occ_file_positions[0]

    return round(length, 2) if length > 0 else None


def _generate_ecart_report(
    tables: dict, occurrences: list, result: ExtractionResult
) -> dict:
    """Genere un rapport d'ecart entre le plan et le metre."""
    # Repere detectes sur le plan
    plan_refs = set()
    for occ in occurrences:
        plan_refs.add(occ["repere"])

    # Repere dans le metre
    metre_refs = set()
    for elem in result.semelles + result.poteaux + result.poutres + \
                 result.longrines + result.chainages + result.murs + \
                 result.voiles + result.autres:
        metre_refs.add(elem.repere)

    return {
        "reperes_plan_absents_metre": sorted(plan_refs - metre_refs),
        "reperes_metre_absents_plan": sorted(metre_refs - plan_refs),
        "total_plan": len(plan_refs),
        "total_metre": len(metre_refs),
    }


# ============================================================================
# Passe 6 : Validation zero-miss
# ============================================================================

def validate_zero_miss(result: ExtractionResult) -> list[str]:
    """Valide les regles zero-miss sur le resultat d'extraction.

    Regles :
    1. Pas de valeurs codrees en dur (0, 0.2, 3.5 partout)
    2. Comptage croise (occurrences vs elements)
    3. Traçabilite de chaque valeur
    """
    warnings = []

    # Regle 1 : Verifier les dimensions nulles
    for elem in result.semelles + result.poteaux:
        dims = elem.dimensions
        if dims.get("a") == 0 or dims.get("b") == 0:
            warnings.append(
                f"{elem.repere}: dimension nulle (a={dims.get('a')}, "
                f"b={dims.get('b')}) — a verifier sur le plan"
            )
        if dims.get("h") == 0 and elem.famille == "SEMELLE":
            warnings.append(
                f"{elem.repere}: hauteur nulle — a verifier sur le plan"
            )

    # Regle 2 : Comptage croise
    total_occurrences = len(result.rapport_ecart.get("reperes_plan_absents_metre", []))
    if total_occurrences > 0:
        warnings.append(
            f"{total_occurrences} repere(s) present(s) sur le plan "
            f"mais absent(s) du metree"
        )

    # Regle 3 : Verifier la traçabilite
    for elem in result.semelles + result.poteaux + result.poutres:
        if not elem.sources:
            warnings.append(
                f"{elem.repere}: pas de source tracee — "
                "valeur non verifiable"
            )

    # Regle 4 : Detecter les valeurs suspectement uniformes
    # (ex: toutes les poutres ont exactement la meme longueur)
    poutre_lengths = [
        p.longueur_calculee_m for p in result.poutres
        if p.longueur_calculee_m is not None
    ]
    if len(poutre_lengths) > 3:
        unique_lengths = set(poutre_lengths)
        if len(unique_lengths) == 1:
            warnings.append(
                f"ALERT: Toutes les {len(poutre_lengths)} poutres ont "
                f"la meme longueur ({poutre_lengths[0]}m) — "
                "valeur probablement codree en dur"
            )

    return warnings


# ============================================================================
# Point d'entree principal
# ============================================================================

def run_zero_miss_extraction(pdf_path: str) -> dict:
    """Execute le pipeline d'extraction multi-passes "Zero-Miss".

    Args:
        pdf_path: chemin vers le fichier PDF

    Returns:
        Dictionnaire structuré avec toutes les familles + validation
    """
    import pymupdf

    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF introuvable : {pdf_path}")

    doc = pymupdf.open(str(path))
    result = ExtractionResult()

    try:
        # Passe 1 : Tableaux structures
        logger.info("Passe 1 : Extraction des tableaux structures...")
        tables = extract_structured_tables(doc)

        # Passe 2 : Legende
        logger.info("Passe 2 : Parsing de la legende...")
        legend = extract_legend(doc)

        # Passe 3 : Occurrences exhaustives
        logger.info("Passe 3 : Detection des occurrences...")
        occurrences = extract_all_occurrences(doc)

        # Passe 4 : Trame d'axes
        logger.info("Passe 4 : Extraction de la trame d'axes...")
        axis_grid = extract_axis_grid(doc)

        # Passe 5 : Fusion et calcul
        logger.info("Passe 5 : Fusion et calcul du metree...")
        result = merge_and_calculate(
            tables, occurrences, legend, axis_grid
        )

        # Passe 6 : Validation
        logger.info("Passe 6 : Validation zero-miss...")
        result.warnings = validate_zero_miss(result)

        logger.info(
            "Extraction terminee : %d semelles, %d poteaux, %d poutres, "
            "%d longrines, %d chainages, %d murs",
            len(result.semelles), len(result.poteaux), len(result.poutres),
            len(result.longrines), len(result.chainages), len(result.murs),
        )

    finally:
        doc.close()

    return result.to_dict()
