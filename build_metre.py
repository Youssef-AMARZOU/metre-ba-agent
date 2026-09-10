#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_metre.py — Moteur de generation de metre Excel conforme reference MZINDA.

Genere un classeur Excel avec 2 feuilles :
  1. "Detail quontitafif fondation" — volumes beton
  2. "Armatures" — ferraillage avec ventilation par diametre

Entree : JSON (catalogue_types + implantations)
Sortie : output/metre_genere.xlsx
"""
import argparse
import json
import os
import sys
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet


def _safe_float(value, default=0.0):
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=0):
    try:
        return int(_safe_float(value, default))
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_dict(value):
    return value if isinstance(value, dict) else {}


def _safe_list(value):
    return value if isinstance(value, list) else []


def _normalise_plan_data(plan_data):
    """Normalise les donnees OCR avant tout calcul Excel."""
    source = _safe_dict(plan_data)
    catalogue_source = _safe_dict(source.get("catalogue_types"))
    catalogue = {"semelles": {}, "poteaux": {}, "poutres": {},
                 "longrines": {}, "chainages": {}, "murs": {}, "voiles": {}}
    for family in catalogue:
        for key, raw in _safe_dict(catalogue_source.get(family)).items():
            dims = _safe_dict(raw).copy()
            for name in ("a", "b", "h", "esp"):
                if name in dims:
                    dims[name] = _safe_float(dims[name])
            for name in ("ferr_x", "ferr_y", "cadres"):
                if name in dims:
                    dims[name] = _safe_dict(dims[name]).copy()
            for name in ("ferr_x", "ferr_y"):
                bar = dims.setdefault(name, {})
                bar["nb"] = _safe_int(bar.get("nb"))
                bar["phi"] = _safe_int(bar.get("phi"))
            for name in ("long_bars", "filants_inf", "filants_sup"):
                if name in dims:
                    dims[name] = [
                        {"nb": _safe_int(_safe_dict(bar).get("nb")),
                         "phi": _safe_int(_safe_dict(bar).get("phi"))}
                        for bar in _safe_list(dims[name])
                    ]
            cadres = dims.get("cadres")
            if isinstance(cadres, dict):
                cadres["phi"] = _safe_int(cadres.get("phi"))
                cadres["esp"] = _safe_float(cadres.get("esp"), 0.15)
            catalogue[family][key] = dims

    implantations = {}
    for family in ("semelles", "poteaux", "poutres"):
        implantations[family] = []
        for raw in _safe_list(_safe_dict(source.get("implantations")).get(family)):
            inst = _safe_dict(raw).copy()
            for name in ("hauteur", "portee"):
                if name in inst and inst[name] is not None:
                    inst[name] = _safe_float(inst[name])
            implantations[family].append(inst)
    projet = source.get("projet", {})
    return {"projet": projet, "catalogue_types": catalogue,
            "implantations": implantations}


if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
else:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
else:
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ============================================================================
# Constantes normatives
# ============================================================================

ENROBAGE_FONDATION = 0.05
COEF_ANCRAGE = 34
COEF_CADRE = 20.5
COEF_EPIGLE = 22
COEF_RECOUVREMENT = 36
HAUTEUR_TERRE_VEGETALE = 0.30
PROFONDEUR_FOUILLE = 1.50  # Poste 3 : terrassement a 1.50 m

# ============================================================================
# Styles Excel
# ============================================================================

THIN = Side(style="thin")
BORDER_ALL = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

FONT_HEADER = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
FONT_TITRE = Font(name="Calibri", size=14, bold=True)
FONT_SECTION = Font(name="Calibri", size=11, bold=True)
FONT_DATA = Font(name="Calibri", size=10)
FONT_TOTAL = Font(name="Calibri", size=10, bold=True)
FONT_FORMULE = Font(name="Calibri", size=10, italic=True, color="0000AA")
FONT_LABEL = Font(name="Calibri", size=10, italic=True, color="555555")

FILL_HEADER = PatternFill("solid", fgColor="2F5496")
FILL_SECTION = PatternFill("solid", fgColor="D6E4F0")
FILL_TOTAL = PatternFill("solid", fgColor="BDD7EE")
FILL_ALT_ROW = PatternFill("solid", fgColor="F2F2F2")

ALIGN_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
ALIGN_LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")


# ============================================================================
# Utilitaires de style
# ============================================================================

def _style_header(ws: Worksheet, row: int, cols: int):
    for c in range(1, cols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = FONT_HEADER
        cell.fill = FILL_HEADER
        cell.alignment = ALIGN_CENTER
        cell.border = BORDER_ALL


def _style_section(ws: Worksheet, row: int, cols: int, text: str):
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=cols)
    cell = ws.cell(row=row, column=1, value=text)
    cell.font = FONT_SECTION
    cell.fill = FILL_SECTION
    cell.alignment = ALIGN_LEFT
    cell.border = BORDER_ALL
    for c in range(2, cols + 1):
        ws.cell(row=row, column=c).border = BORDER_ALL


def _style_row(ws: Worksheet, row: int, cols: int, font=FONT_DATA, fill=None, alt=False):
    for c in range(1, cols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = font
        cell.border = BORDER_ALL
        if fill:
            cell.fill = fill
        elif alt:
            cell.fill = FILL_ALT_ROW
        if c >= 4:
            cell.alignment = ALIGN_RIGHT


def _detect_diameters(plan_data: dict) -> list:
    """Detecte tous les diametres reels utilises dans le plan, tries.
    Les diametres nuls (element sans ferraillage lu) sont ignores."""
    diam_set = set()
    catalogue = _safe_dict(plan_data.get("catalogue_types"))

    for dims in _safe_dict(catalogue.get("semelles")).values():
        dims = _safe_dict(dims)
        for key in ("ferr_x", "ferr_y"):
            ferr = _safe_dict(dims.get(key))
            phi, nb = _safe_int(ferr.get("phi")), _safe_int(ferr.get("nb"))
            if phi > 0 and nb > 0:
                diam_set.add(phi)

    for dims in _safe_dict(catalogue.get("poteaux")).values():
        dims = _safe_dict(dims)
        for lb in _safe_list(dims.get("long_bars")):
            lb = _safe_dict(lb)
            phi, nb = _safe_int(lb.get("phi")), _safe_int(lb.get("nb"))
            if phi > 0 and nb > 0:
                diam_set.add(phi)
        cadres = _safe_dict(dims.get("cadres"))
        phi = _safe_int(cadres.get("phi"))
        if phi > 0:
            diam_set.add(phi)

    for dims in _safe_dict(catalogue.get("poutres")).values():
        dims = _safe_dict(dims)
        bars = _safe_list(dims.get("filants_inf")) + _safe_list(
            dims.get("filants_sup"))
        for fi in bars:
            fi = _safe_dict(fi)
            phi, nb = _safe_int(fi.get("phi")), _safe_int(fi.get("nb"))
            if phi > 0 and nb > 0:
                diam_set.add(phi)
        cadres = _safe_dict(dims.get("cadres"))
        phi = _safe_int(cadres.get("phi"))
        if phi > 0:
            diam_set.add(phi)

    return sorted(diam_set) if diam_set else [6, 8, 10, 12, 14, 16, 20, 25, 32]


# ============================================================================
# FEUILLE 1 : Detail quantitatif fondation
# ============================================================================

class FeuilleDetailFondation:
    """Genere la feuille 'Detail quontitafif fondation'."""

    # Colonnes : A=N, B=Designation, C=Axe, D=File, E=U, F=N, G=Long, H=Larg, I=Haut, J=Qte_part, K=Qte_tot
    COL_HEADERS = [
        ("A", "N°"),
        ("B", "Désignation, détail de calcul, croquis, principe et justificatif"),
        ("C", "Axe"),
        ("D", "File"),
        ("E", "U"),
        ("F", "N"),
        ("G", "Longueur"),
        ("H", "Largeur"),
        ("I", "Hauteur / épaisseur"),
        ("J", "Qté partielle"),
        ("K", "Qté Total des Métrés"),
    ]
    NUM_COLS = 11  # A-K

    def __init__(self, plan_data: dict):
        self.plan = plan_data
        self.catalogue = plan_data.get("catalogue_types", {})
        self.implantations = plan_data.get("implantations", {})
        self.projet = plan_data.get("projet", {})

    def generer(self, ws: Worksheet):
        """Remplit la feuille Detail quantitatif fondation."""
        ws.title = "Detail quontitafif fondation"

        # --- Titre projet ---
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=self.NUM_COLS)
        ws.cell(row=1, column=1, value=" AO N° : ").font = FONT_DATA

        ws.merge_cells(start_row=4, start_column=3, end_row=4, end_column=self.NUM_COLS)
        ws.cell(row=4, column=3,
                value=self.projet.get("nom", "Projet")).font = FONT_TITRE

        ws.merge_cells(start_row=5, start_column=5, end_row=5, end_column=self.NUM_COLS)
        ws.cell(row=5, column=5, value="Détail Quantitatif Métré").font = FONT_SECTION

        # --- En-tetes colonnes ---
        row = 6
        for col_letter, header in self.COL_HEADERS:
            col_idx = ord(col_letter) - ord("A") + 1
            ws.cell(row=row, column=col_idx, value=header)
        _style_header(ws, row, self.NUM_COLS)

        # --- Postes ---
        row = 8

        # Poste 3 : Terrassement
        _style_section(ws, row, self.NUM_COLS,
                       "B-   GROS ŒUVRES et maconnerie")
        row += 1
        ws.cell(row=row, column=1, value=3).font = FONT_SECTION
        ws.cell(row=row, column=2,
                value="Terrassement en fouilles, en tranchées ou en plein masse "
                      "y compris décapage de la terre végétale").font = FONT_DATA
        row += 1

        # Sous-titres axe/file
        ws.cell(row=row, column=2, value="axe").font = FONT_LABEL
        ws.cell(row=row, column=3, value="fill").font = FONT_LABEL
        row += 1

        # Lignes semelles pour terrassement
        sem_impl = self.implantations.get("semelles", [])
        for inst in sem_impl:
            type_key = inst.get("type", "")
            dims = self.catalogue.get("semelles", {}).get(type_key, {})
            a = dims.get("a", 0)
            b = dims.get("b", 0)

            ws.cell(row=row, column=2, value=inst.get("axe", ""))
            ws.cell(row=row, column=3, value=inst.get("file", ""))
            ws.cell(row=row, column=5, value="M3")
            ws.cell(row=row, column=6, value=1)
            # Poste 3 : fouille (a+0.40) x (b+0.40) x 1.50 m
            ws.cell(row=row, column=7, value=a + 0.40)
            ws.cell(row=row, column=8, value=b + 0.40)
            ws.cell(row=row, column=9, value=PROFONDEUR_FOUILLE)
            # Formule Qté partielle
            ws.cell(row=row, column=10,
                    value=f"=F{row}*G{row}*H{row}*I{row}")
            ws.cell(row=row, column=10).font = FONT_FORMULE
            _style_row(ws, row, self.NUM_COLS)
            row += 1

        # Total terrassement
        if sem_impl:
            start_data = row - len(sem_impl)
            ws.cell(row=row, column=1, value="total").font = FONT_TOTAL
            ws.cell(row=row, column=10,
                    value=f"=SUM(J{start_data}:J{row - 1})")
            ws.cell(row=row, column=10).font = FONT_FORMULE
            ws.cell(row=row, column=11,
                    value=f"=SUM(K{start_data}:K{row - 1})")
            ws.cell(row=row, column=11).font = FONT_FORMULE
            _style_row(ws, row, self.NUM_COLS, font=FONT_TOTAL, fill=FILL_TOTAL)
            row += 1

        # Poste 15 : Beton de propriete
        row += 1
        ws.cell(row=row, column=1, value=15).font = FONT_SECTION
        ws.cell(row=row, column=2, value=" Béton de propreté ").font = FONT_SECTION
        row += 1
        ws.cell(row=row, column=2, value="pour semelle isolee").font = FONT_DATA
        row += 1
        ws.cell(row=row, column=2, value="axe").font = FONT_LABEL
        ws.cell(row=row, column=3, value="fill").font = FONT_LABEL
        row += 1

        for inst in sem_impl:
            type_key = inst.get("type", "")
            dims = self.catalogue.get("semelles", {}).get(type_key, {})
            a = dims.get("a", 0)
            b = dims.get("b", 0)

            ws.cell(row=row, column=2, value=inst.get("axe", ""))
            ws.cell(row=row, column=3, value=inst.get("file", ""))
            ws.cell(row=row, column=5, value="M3")
            ws.cell(row=row, column=6, value=1)
            ws.cell(row=row, column=7, value=a + 0.20)
            ws.cell(row=row, column=8, value=b + 0.20)
            ws.cell(row=row, column=9, value=0.10)  # Epaisseur propriete = 0.10
            ws.cell(row=row, column=10,
                    value=f"=F{row}*G{row}*H{row}*I{row}")
            ws.cell(row=row, column=10).font = FONT_FORMULE
            _style_row(ws, row, self.NUM_COLS)
            row += 1

        # Total beton propriete
        if sem_impl:
            start_data = row - len(sem_impl)
            ws.cell(row=row, column=1, value="total").font = FONT_TOTAL
            ws.cell(row=row, column=10,
                    value=f"=SUM(J{start_data}:J{row - 1})")
            ws.cell(row=row, column=10).font = FONT_FORMULE
            ws.cell(row=row, column=11,
                    value=f"=SUM(K{start_data}:K{row - 1})")
            ws.cell(row=row, column=11).font = FONT_FORMULE
            _style_row(ws, row, self.NUM_COLS, font=FONT_TOTAL, fill=FILL_TOTAL)
            row += 1

        # Poste 16 : Beton armé semelles
        row += 1
        ws.cell(row=row, column=1, value=16).font = FONT_SECTION
        ws.cell(row=row, column=2,
                value=" Béton pour béton armé en fondation calculé en M3").font = FONT_SECTION
        row += 1
        ws.cell(row=row, column=2, value="pour semelle isolee").font = FONT_DATA
        row += 1
        ws.cell(row=row, column=2, value="axe").font = FONT_LABEL
        ws.cell(row=row, column=3, value="fill").font = FONT_LABEL
        row += 1

        for inst in sem_impl:
            type_key = inst.get("type", "")
            dims = self.catalogue.get("semelles", {}).get(type_key, {})
            a = dims.get("a", 0)
            b = dims.get("b", 0)
            h = dims.get("h", 0.25)

            ws.cell(row=row, column=2, value=inst.get("axe", ""))
            ws.cell(row=row, column=3, value=inst.get("file", ""))
            ws.cell(row=row, column=5, value="M3")
            ws.cell(row=row, column=6, value=1)
            ws.cell(row=row, column=7, value=a)
            ws.cell(row=row, column=8, value=b)
            ws.cell(row=row, column=9, value=h)
            ws.cell(row=row, column=10,
                    value=f"=F{row}*G{row}*H{row}*I{row}")
            ws.cell(row=row, column=10).font = FONT_FORMULE
            _style_row(ws, row, self.NUM_COLS)
            row += 1

        # Total beton armé
        if sem_impl:
            start_data = row - len(sem_impl)
            ws.cell(row=row, column=1, value="total").font = FONT_TOTAL
            ws.cell(row=row, column=10,
                    value=f"=SUM(J{start_data}:J{row - 1})")
            ws.cell(row=row, column=10).font = FONT_FORMULE
            ws.cell(row=row, column=11,
                    value=f"=SUM(K{start_data}:K{row - 1})")
            ws.cell(row=row, column=11).font = FONT_FORMULE
            _style_row(ws, row, self.NUM_COLS, font=FONT_TOTAL, fill=FILL_TOTAL)
            row += 1

        # Poste 20 : Fut poteaux
        pot_impl = self.implantations.get("poteaux", [])
        if pot_impl:
            row += 1
            ws.cell(row=row, column=1, value=20).font = FONT_SECTION
            ws.cell(row=row, column=2,
                    value=" Béton pour fût de poteau en fondation calculé en M3").font = FONT_SECTION
            row += 1
            ws.cell(row=row, column=2, value="axe").font = FONT_LABEL
            ws.cell(row=row, column=3, value="fill").font = FONT_LABEL
            row += 1

            for inst in pot_impl:
                type_key = inst.get("type", "")
                dims = self.catalogue.get("poteaux", {}).get(type_key, {})
                a = dims.get("a", 0.25)
                b = dims.get("b", 0.35)
                hauteur = inst.get("hauteur", 3.0)

                ws.cell(row=row, column=2, value=inst.get("axe", ""))
                ws.cell(row=row, column=3, value=inst.get("file", ""))
                ws.cell(row=row, column=5, value="M3")
                ws.cell(row=row, column=6, value=1)
                ws.cell(row=row, column=7, value=a)
                ws.cell(row=row, column=8, value=b)
                ws.cell(row=row, column=9, value=hauteur)
                ws.cell(row=row, column=10,
                        value=f"=F{row}*G{row}*H{row}*I{row}")
                ws.cell(row=row, column=10).font = FONT_FORMULE
                _style_row(ws, row, self.NUM_COLS)
                row += 1

            start_data = row - len(pot_impl)
            ws.cell(row=row, column=1, value="total").font = FONT_TOTAL
            ws.cell(row=row, column=10,
                    value=f"=SUM(J{start_data}:J{row - 1})")
            ws.cell(row=row, column=10).font = FONT_FORMULE
            ws.cell(row=row, column=11,
                    value=f"=SUM(K{start_data}:K{row - 1})")
            ws.cell(row=row, column=11).font = FONT_FORMULE
            _style_row(ws, row, self.NUM_COLS, font=FONT_TOTAL, fill=FILL_TOTAL)

        # --- Ajuster largeurs ---
        widths = [6, 50, 6, 6, 5, 5, 10, 10, 14, 12, 14]
        for i, w in enumerate(widths[:self.NUM_COLS], 1):
            ws.column_dimensions[get_column_letter(i)].width = w


# ============================================================================
# FEUILLE 2 : Armatures
# ============================================================================

class FeuilleArmatures:
    """Genere la feuille 'Armatures' conforme a la reference MZINDA."""

    # Colonnes principales : A=OUVRAGES, B=axe, C=fill, D=a, E=b, F=h, G=N°ELE, H=NOMB, I=DIAM, J=LONG
    # Colonnes ACIER : K=T6, L=T8, M=T10, N=T12, O=T14, P=T16, Q=T20, R=T25, S=T32
    COL_MAIN_HEADERS = [
        "O U V R A G E S", "Axe", "File", "a", "b", "h",
        "N° ELE", "NOMB B", "DIAM", "LONG"
    ]
    NUM_MAIN_COLS = 10  # A-J

    def __init__(self, plan_data: dict, diameters: list):
        self.plan = plan_data
        self.catalogue = plan_data.get("catalogue_types", {})
        self.implantations = plan_data.get("implantations", {})
        self.diameters = diameters or [6, 8, 10, 12, 14, 16, 20, 25, 32]

    def _get_total_cols(self) -> int:
        return self.NUM_MAIN_COLS + len(self.diameters)

    def _diam_col(self, idx: int) -> int:
        return self.NUM_MAIN_COLS + 1 + idx

    def _write_acier_headers(self, ws: Worksheet, row: int):
        """Ecrit les en-tetes ACIER TOR (ligne 1 + ligne 2)."""
        total_cols = self._get_total_cols()

        # Ligne 1 : "A C I E R    T O R" en colonne K
        ws.cell(row=row, column=self.NUM_MAIN_COLS + 1,
                value="A C I E R    T O R").font = FONT_HEADER
        for c in range(1, self.NUM_MAIN_COLS + 1):
            cell = ws.cell(row=row, column=c)
            cell.font = FONT_HEADER
            cell.fill = FILL_HEADER
            cell.alignment = ALIGN_CENTER
            cell.border = BORDER_ALL
        for c in range(self.NUM_MAIN_COLS + 1, total_cols + 1):
            cell = ws.cell(row=row, column=c)
            cell.fill = FILL_HEADER
            cell.border = BORDER_ALL

        # Ligne 2 : T6, T8, T10, ...
        row2 = row + 1
        for i, d in enumerate(self.diameters):
            col = self._diam_col(i)
            ws.cell(row=row2, column=col, value=f"T {d}").font = FONT_HEADER
            ws.cell(row=row2, column=col).fill = FILL_HEADER
            ws.cell(row=row2, column=col).alignment = ALIGN_CENTER
            ws.cell(row=row2, column=col).border = BORDER_ALL
        # Remplir les colonnes main de la ligne 2
        for c in range(1, self.NUM_MAIN_COLS + 1):
            cell = ws.cell(row=row2, column=c)
            cell.fill = FILL_HEADER
            cell.border = BORDER_ALL
        ws.cell(row=row2, column=1, value="A C I E R    T O R").font = FONT_HEADER

        return row2 + 1

    def _write_acier_formulas(self, ws: Worksheet, row: int):
        """Ecrit les formules IF pour les colonnes ACIER d'une ligne donnee."""
        for i, d in enumerate(self.diameters):
            col = self._diam_col(i)
            # Formule : =IF(I{row}=d, G{row}*H{row}*J{row}, "")
            ws.cell(row=row, column=col,
                    value=f'=IF($I{row}={d},$G{row}*$H{row}*$J{row},"")')
            ws.cell(row=row, column=col).font = FONT_FORMULE

    def _write_element_block(self, ws: Worksheet, start_row: int,
                              inst: dict, type_key: str,
                              dims: dict, is_poteau: bool = False) -> int:
        """Ecrit un bloc element (ligne principale + armatures)."""
        row = start_row
        total_cols = self._get_total_cols()
        a = dims.get("a", 0)
        b = dims.get("b", 0)
        h = dims.get("h", dims.get("hauteur", 0))

        # Ligne principale element
        ws.cell(row=row, column=1, value=inst.get("id", type_key))
        ws.cell(row=row, column=2, value=inst.get("axe", ""))
        ws.cell(row=row, column=3, value=inst.get("file", ""))
        ws.cell(row=row, column=4, value=a)
        ws.cell(row=row, column=5, value=b)
        if not is_poteau:
            ws.cell(row=row, column=6, value=h)
        # G, H, J vides pour la ligne principale
        self._write_acier_formulas(ws, row)
        _style_row(ws, row, total_cols)
        row += 1

        # --- Semelles ---
        if not is_poteau:
            ferr_x = dims.get("ferr_x", {})
            ferr_y = dims.get("ferr_y", {})
            nb_x = ferr_x.get("nb", 0)
            phi_x = ferr_x.get("phi", 0)
            nb_y = ferr_y.get("nb", 0)
            phi_y = ferr_y.get("phi", 0)

            # Armature INF X
            ws.cell(row=row, column=1, value="Armature INF X")
            ws.cell(row=row, column=7, value=1)  # N° ELE
            ws.cell(row=row, column=8, value=nb_x)
            ws.cell(row=row, column=9, value=phi_x)
            # LONG = (Dim E - enrobage) + 34 * d / 1000
            ws.cell(row=row, column=10,
                    value=f"=(E{row - 1}-{ENROBAGE_FONDATION})+{COEF_ANCRAGE}*I{row}/1000")
            ws.cell(row=row, column=10).font = FONT_FORMULE
            self._write_acier_formulas(ws, row)
            _style_row(ws, row, total_cols)
            row += 1

            # Armature INF Y
            ws.cell(row=row, column=1, value="Armature INF Y")
            ws.cell(row=row, column=7, value=1)
            ws.cell(row=row, column=8, value=nb_y)
            ws.cell(row=row, column=9, value=phi_y)
            # LONG = (Dim D - enrobage) + 34 * d / 1000
            ws.cell(row=row, column=10,
                    value=f"=(D{row - 2}-{ENROBAGE_FONDATION})+{COEF_ANCRAGE}*I{row}/1000")
            ws.cell(row=row, column=10).font = FONT_FORMULE
            self._write_acier_formulas(ws, row)
            _style_row(ws, row, total_cols)
            row += 1

            # Semelle sans armatures cotees : mention explicite (zero mock)
            if nb_x == 0 and nb_y == 0:
                ws.cell(row=row, column=1,
                        value="Armatures non cotées dans la table "
                              "(à vérifier sur coupes)")
                ws.cell(row=row, column=1).font = FONT_LABEL
                row += 1

        # --- Poteaux ---
        else:
            long_bars = dims.get("long_bars", [])
            cadres = dims.get("cadres", {})
            phi_c = cadres.get("phi", 0)
            esp_c = cadres.get("esp", 0.15)
            hauteur = inst.get("hauteur", 3.0)

            # Longitudinaux
            for lb in long_bars:
                phi_l = lb.get("phi", 0)
                nb_l = lb.get("nb", 0)
                ws.cell(row=row, column=1, value="ARM LONG")
                ws.cell(row=row, column=7, value=nb_l)
                ws.cell(row=row, column=8, value=nb_l)
                ws.cell(row=row, column=9, value=phi_l)
                # Long = hauteur + 2 * ancrage
                ws.cell(row=row, column=10,
                        value=f"=(F{row - 1}-0.1-{ENROBAGE_FONDATION})+"
                              f"70*I{row}/1000+18*I{row}/1000")
                ws.cell(row=row, column=10).font = FONT_FORMULE
                self._write_acier_formulas(ws, row)
                _style_row(ws, row, total_cols)
                row += 1

            # Cadres
            ws.cell(row=row, column=1, value="CADRE")
            ws.cell(row=row, column=7, value=f"=ROUNDUP(F{row - len(long_bars) - 1}/{esp_c},0)+2")
            ws.cell(row=row, column=7).font = FONT_FORMULE
            ws.cell(row=row, column=8, value=f"=ROUNDUP(F{row - len(long_bars) - 1}/{esp_c},0)+2")
            ws.cell(row=row, column=8).font = FONT_FORMULE
            ws.cell(row=row, column=9, value=phi_c)
            ref_elem = row - len(long_bars) - 1
            ws.cell(row=row, column=10,
                    value=f"=(2*(D{ref_elem}+E{ref_elem})-{ENROBAGE_FONDATION}+"
                          f"{COEF_CADRE}*I{row}/1000)")
            ws.cell(row=row, column=10).font = FONT_FORMULE
            self._write_acier_formulas(ws, row)
            _style_row(ws, row, total_cols)
            row += 1

            # Epingles
            ws.cell(row=row, column=1, value="EPINGLE")
            ws.cell(row=row, column=7, value=f"=ROUNDUP(F{row - len(long_bars) - 2}/{esp_c},0)+2")
            ws.cell(row=row, column=7).font = FONT_FORMULE
            ws.cell(row=row, column=8, value=f"=ROUNDUP(F{row - len(long_bars) - 2}/{esp_c},0)+2")
            ws.cell(row=row, column=8).font = FONT_FORMULE
            ws.cell(row=row, column=9, value=phi_c)
            ws.cell(row=row, column=10,
                    value=f"=D{row - 2}-{ENROBAGE_FONDATION}+{COEF_EPIGLE}*I{row}/1000")
            ws.cell(row=row, column=10).font = FONT_FORMULE
            self._write_acier_formulas(ws, row)
            _style_row(ws, row, total_cols)
            row += 1

        return row

    def generer(self, ws: Worksheet):
        """Remplit la feuille Armatures."""
        ws.title = "Armatures"
        total_cols = self._get_total_cols()

        # --- Ligne 1 : En-tetes principaux ---
        for c, h in enumerate(self.COL_MAIN_HEADERS, 1):
            ws.cell(row=1, column=c, value=h)
        row_end = self._write_acier_headers(ws, 1)

        # --- Ligne 3 : Poste 21 ---
        row = row_end
        ws.cell(row=row, column=1, value=21).font = FONT_SECTION
        ws.cell(row=row, column=2,
                value="Armature pour béton armé en fondation et en élévation "
                      ).font = FONT_DATA
        row += 1

        # --- Semelles isolees ---
        ws.cell(row=row, column=1, value="semelle isolee").font = FONT_SECTION
        row += 1

        # Sous-titres axe/file
        ws.cell(row=row, column=2, value="axe").font = FONT_LABEL
        ws.cell(row=row, column=3, value="fill").font = FONT_LABEL
        row += 1

        sem_impl = self.implantations.get("semelles", [])
        for inst in sem_impl:
            type_key = inst.get("type", "")
            dims = self.catalogue.get("semelles", {}).get(type_key, {})
            row = self._write_element_block(ws, row, inst, type_key, dims)

        # --- Poteaux ---
        pot_impl = self.implantations.get("poteaux", [])
        if pot_impl:
            row += 1
            ws.cell(row=row, column=1,
                    value="fut poteau").font = FONT_SECTION
            row += 1

            ws.cell(row=row, column=2, value="axe").font = FONT_LABEL
            ws.cell(row=row, column=3, value="fill").font = FONT_LABEL
            row += 1

            for inst in pot_impl:
                type_key = inst.get("type", "")
                dims = self.catalogue.get("poteaux", {}).get(type_key, {})
                row = self._write_element_block(
                    ws, row, inst, type_key, dims, is_poteau=True)

        # --- Poutres ---
        pou_impl = self.implantations.get("poutres", [])
        if pou_impl:
            row += 1
            ws.cell(row=row, column=1,
                    value="poutre").font = FONT_SECTION
            row += 1

            for inst in pou_impl:
                type_key = inst.get("type", "")
                dims = self.catalogue.get("poutres", {}).get(type_key, {})
                b = dims.get("b", 0)
                h = dims.get("h", 0)
                portee = inst.get("portee", 3.0)
                filants_inf = dims.get("filants_inf", [])
                filants_sup = dims.get("filants_sup", [])
                cadres = dims.get("cadres", {})

                # Ligne principale
                ws.cell(row=row, column=1, value=inst.get("id", type_key))
                ws.cell(row=row, column=2, value=inst.get("axe", ""))
                ws.cell(row=row, column=4, value=b)
                ws.cell(row=row, column=5, value=h)
                ws.cell(row=row, column=6, value=portee)
                self._write_acier_formulas(ws, row)
                _style_row(ws, row, total_cols)
                row += 1

                nb_lines = 0
                # Filants inferieurs
                for fi in filants_inf:
                    ws.cell(row=row, column=1, value="Armature INF")
                    ws.cell(row=row, column=7, value=1)
                    ws.cell(row=row, column=8, value=fi.get("nb", 0))
                    ws.cell(row=row, column=9, value=fi.get("phi", 0))
                    ws.cell(row=row, column=10,
                            value=f"=(F{row - nb_lines - 1}+2*{COEF_ANCRAGE}*I{row}/1000)")
                    ws.cell(row=row, column=10).font = FONT_FORMULE
                    self._write_acier_formulas(ws, row)
                    _style_row(ws, row, total_cols)
                    row += 1
                    nb_lines += 1

                # Filants superieurs
                for fs in filants_sup:
                    ws.cell(row=row, column=1, value="Armature SUP")
                    ws.cell(row=row, column=7, value=1)
                    ws.cell(row=row, column=8, value=fs.get("nb", 0))
                    ws.cell(row=row, column=9, value=fs.get("phi", 0))
                    ws.cell(row=row, column=10,
                            value=f"=(F{row - nb_lines - 1}+2*{COEF_ANCRAGE}*I{row}/1000)")
                    ws.cell(row=row, column=10).font = FONT_FORMULE
                    self._write_acier_formulas(ws, row)
                    _style_row(ws, row, total_cols)
                    row += 1
                    nb_lines += 1

                # Cadres
                phi_c = cadres.get("phi", 0)
                esp_c = cadres.get("esp", 0.18)
                ref_elem = row - nb_lines - 1
                ws.cell(row=row, column=1, value="CADRE")
                ws.cell(row=row, column=8,
                        value=f"=ROUNDUP(F{ref_elem}/{esp_c},0)+2")
                ws.cell(row=row, column=8).font = FONT_FORMULE
                ws.cell(row=row, column=9, value=phi_c)
                ws.cell(row=row, column=10,
                        value=f"=(2*(D{ref_elem}+E{ref_elem})-{ENROBAGE_FONDATION}+"
                              f"{COEF_CADRE}*I{row}/1000)")
                ws.cell(row=row, column=10).font = FONT_FORMULE
                self._write_acier_formulas(ws, row)
                _style_row(ws, row, total_cols)
                row += 1

        # --- Totaux en bas ---
        row += 1

        # Ligne LONGUEUR TOTALE
        ws.cell(row=row, column=1, value="LONGUEUR TOTALE")
        for i, d in enumerate(self.diameters):
            col = self._diam_col(i)
            letter = get_column_letter(col)
            ws.cell(row=row, column=col, value=f"=SUM({letter}4:{letter}{row - 1})")
            ws.cell(row=row, column=col).font = FONT_FORMULE
        _style_row(ws, row, total_cols, font=FONT_TOTAL, fill=FILL_TOTAL)
        long_tot_row = row
        row += 1

        # Ligne POIDS / ML
        ws.cell(row=row, column=1, value="POIDS / ML (kg/m)")
        for i, d in enumerate(self.diameters):
            col = self._diam_col(i)
            ws.cell(row=row, column=col, value=f"={d}*{d}/162")
            ws.cell(row=row, column=col).font = FONT_FORMULE
        _style_row(ws, row, total_cols, font=FONT_TOTAL, fill=FILL_TOTAL)
        poids_ml_row = row
        row += 1

        # Ligne POIDS PARTIELS
        ws.cell(row=row, column=1, value="POIDS PARTIELS (kg)")
        for i, d in enumerate(self.diameters):
            col = self._diam_col(i)
            letter = get_column_letter(col)
            ws.cell(row=row, column=col,
                    value=f"={letter}{long_tot_row}*{letter}{poids_ml_row}")
            ws.cell(row=row, column=col).font = FONT_FORMULE
        _style_row(ws, row, total_cols, font=FONT_TOTAL, fill=FILL_TOTAL)
        poids_part_row = row
        row += 1

        # Ligne POIDS TOTAL
        ws.cell(row=row, column=1, value="POIDS TOTAL (KG)")
        first_col = get_column_letter(self._diam_col(0))
        last_col = get_column_letter(self._diam_col(len(self.diameters) - 1))
        ws.cell(row=row, column=10,
                value=f"=SUM({first_col}{poids_part_row}:{last_col}{poids_part_row})")
        ws.cell(row=row, column=10).font = FONT_FORMULE
        _style_row(ws, row, total_cols, font=FONT_TOTAL, fill=FILL_TOTAL)

        # --- Ajuster largeurs ---
        widths = [18, 6, 6, 8, 8, 8, 8, 8, 8, 10] + [8] * len(self.diameters)
        for i, w in enumerate(widths[:total_cols], 1):
            ws.column_dimensions[get_column_letter(i)].width = w


# ============================================================================
# Pont vers le gabarit standardise 5 feuilles (core/populate_modele.py)
# ============================================================================

def resoudre_chemin_template(base_dir=None):
    """Localise le gabarit 5 feuilles (compatible PyInstaller frozen).

    Ordre : dossier templates/ a cote du script (ou du .exe) ->
    output/modele_metre_BA.xlsx -> regeneration fraiche.
    """
    if base_dir is None:
        if getattr(sys, "frozen", False):
            # Mode compile : ressources dans _MEIPASS (jamais %TEMP% manuel)
            meipass = getattr(sys, "_MEIPASS", None)
            base_dir = Path(meipass) if meipass \
                else Path(sys.executable).parent
        else:
            base_dir = Path(__file__).parent
    else:
        base_dir = Path(base_dir)

    candidats = [
        base_dir / "templates" / "modele_metre_BA.xlsx",
        base_dir / "output" / "modele_metre_BA.xlsx",
    ]
    for tpl in candidats:
        if tpl.exists():
            return str(tpl)

    # Regeneration fraiche du gabarit (4 feuilles + catalogue standard)
    cible = base_dir / "templates" / "modele_metre_BA.xlsx"
    cible.parent.mkdir(parents=True, exist_ok=True)
    from generators.modele_metre import generer_modele
    generer_modele(str(cible))
    from generators.add_catalogue_sheet import injecter_catalogue_standard
    wb = openpyxl.load_workbook(cible)
    injecter_catalogue_standard(wb)
    wb.save(cible)
    print(f"Gabarit regenere : {cible}")
    return str(cible)


def adapter_plan_vers_injecteur(plan_data: dict) -> dict:
    """Convertit plan_data (catalogue_types + implantations) vers le format
    plat de l'injecteur : semelles/poteaux/poutres/longrines/chainages/murs/voiles."""
    plan_data = _normalise_plan_data(plan_data)
    projet = plan_data.get("projet", "Projet BTP")
    if isinstance(projet, dict):
        projet = projet.get("nom") or "Projet BTP"
    projet = str(projet)

    catalogue = plan_data.get("catalogue_types", {})
    impl = plan_data.get("implantations", {})

    semelles = []
    cat_sem = catalogue.get("semelles", {})
    for inst in impl.get("semelles", []):
        dims = cat_sem.get(inst.get("type", ""), {})
        fx = dims.get("ferr_x", {}) or {}
        fy = dims.get("ferr_y", {}) or {}
        semelles.append({
            "type": inst.get("type", ""),
            "axe": inst.get("axe", ""),
            "file": inst.get("file", ""),
            "a": dims.get("a", 0),
            "b": dims.get("b", 0),
            "h": dims.get("h", 0),
            "phi": fx.get("phi", 0) or fy.get("phi", 0),
            "nb_x": fx.get("nb", 0),
            "nb_y": fy.get("nb", 0),
        })

    poteaux = []
    cat_pot = catalogue.get("poteaux", {})
    for ref, dims in cat_pot.items():
        dims = _safe_dict(dims)
        poteaux.append({
            "type": ref,
            "axe": dims.get("axe", ""),
            "file": dims.get("file", ""),
            "a": dims.get("a", 0),
            "b": dims.get("b", 0),
            "hauteur": dims.get("hauteur", 3.0),
            "long_bars": dims.get("long_bars", []) or [],
            "cadres": dims.get("cadres", {}) or {},
        })

    poutres = []
    cat_pou = catalogue.get("poutres", {})
    for ref, dims in cat_pou.items():
        dims = _safe_dict(dims)
        # Ignorer les entrees sans dimensions
        if not dims.get("b") and not dims.get("h"):
            continue
        poutres.append({
            "type": ref,
            "axe": dims.get("axe", ""),
            "b": dims.get("b", 0),
            "h": dims.get("h", 0),
            "portee": dims.get("longueur") or dims.get("portee"),
            "filants_inf": dims.get("filants_inf", []) or [],
            "filants_sup": dims.get("filants_sup", []) or [],
            "cadres": dims.get("cadres", {}) or {},
        })

    # Longrines (traitees comme poutres pour le metre)
    longrines = []
    cat_lr = catalogue.get("longrines", {})
    for ref, dims in cat_lr.items():
        dims = _safe_dict(dims)
        b = dims.get("b") or dims.get("a") or 0
        h = dims.get("h") or 0
        if not b:
            continue
        longrines.append({
            "type": ref,
            "axe": dims.get("axe", ""),
            "b": b,
            "h": h,
            "portee": dims.get("longueur") or dims.get("portee"),
        })

    # Chainages (traitees comme poutres pour le metre)
    chainages = []
    cat_ch = catalogue.get("chainages", {})
    for ref, dims in cat_ch.items():
        dims = _safe_dict(dims)
        b = dims.get("b") or dims.get("a") or 0
        h = dims.get("h") or 0
        if not b:
            continue
        chainages.append({
            "type": ref,
            "axe": dims.get("axe", ""),
            "b": b,
            "h": h,
            "portee": dims.get("longueur") or dims.get("portee"),
        })

    # Murs (bandes noyees)
    murs = []
    cat_mur = catalogue.get("murs", {})
    for ref, dims in cat_mur.items():
        dims = _safe_dict(dims)
        b = dims.get("b") or dims.get("a") or 0
        h = dims.get("h") or 0
        if not b:
            continue
        murs.append({
            "type": ref,
            "axe": dims.get("axe", ""),
            "b": b,
            "h": h,
            "portee": dims.get("longueur") or dims.get("portee"),
        })

    # Voiles
    voiles = []
    cat_vol = catalogue.get("voiles", {})
    for ref, dims in cat_vol.items():
        dims = _safe_dict(dims)
        voiles.append({
            "type": ref,
            "ep": dims.get("ep", dims.get("b", 0)),
            "hauteur": dims.get("hauteur", 3.0),
        })

    return {
        "projet": projet,
        "semelles": semelles,
        "poteaux": poteaux,
        "poutres": poutres,
        "longrines": longrines,
        "chainages": chainages,
        "murs": murs,
        "voiles": voiles,
    }


def generer_via_gabarit(plan_data: dict, output_path: str,
                        base_dir=None) -> str:
    """Pont universel : plan_data -> gabarit 5 feuilles.

    Chaine de secours : gabarit existant -> regeneration fraiche ->
    moteur historique (le pipeline ne plante jamais sur le rendu)."""
    from core.populate_modele import injecter_metre_dans_modele
    try:
        template = resoudre_chemin_template(base_dir)
        payload = adapter_plan_vers_injecteur(plan_data)
        result_path = injecter_metre_dans_modele(payload, template, output_path)
        # Ajouter la feuille rapport metier
        _ajouter_feuille_rapport_metier(result_path, plan_data)
        return result_path
    except Exception as e:
        print(f"⚠ Pont gabarit indisponible ({e}) — repli historique.")
        gen = MetreGenerator(plan_data, moteur="historique")
        return gen.generer(output_path)


def _ajouter_feuille_rapport_metier(xlsx_path: str, plan_data: dict):
    """Ajoute une feuille 'Rapport Metier' au classeur Excel."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    rapport = plan_data.get("_meta", {}).get("rapport_metier")
    if not rapport:
        return

    wb = openpyxl.load_workbook(xlsx_path)
    if "05_Rapport_Metier" in wb.sheetnames:
        del wb["05_Rapport_Metier"]

    ws = wb.create_sheet("05_Rapport_Metier")

    # Styles
    header_font = Font(bold=True, size=12, color="FFFFFF")
    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    section_font = Font(bold=True, size=11, color="1F4E79")
    normal_font = Font(size=10)
    warn_font = Font(size=10, color="CC0000")
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"))

    row = 1

    # --- En-tete ---
    ws.cell(row, 1, "RAPPORT METIER — GENIE CIVIL / BETON ARME").font = Font(bold=True, size=14, color="1F4E79")
    row += 1

    if rapport.get("revision_index"):
        ws.cell(row, 1, f"Indice : {rapport['revision_index']}").font = normal_font
        row += 1
    if rapport.get("revision_date"):
        ws.cell(row, 1, f"Date : {rapport['revision_date']}").font = normal_font
        row += 1
    row += 1

    # --- Hypotheses reglementaires ---
    ws.cell(row, 1, "HYPOTHESES REGLEMENTAIRES").font = section_font
    row += 1
    if rapport.get("regulatory_hypotheses"):
        for h in rapport["regulatory_hypotheses"]:
            ws.cell(row, 1, h["category"]).font = normal_font
            ws.cell(row, 2, h["value"]).font = normal_font
            row += 1
    else:
        ws.cell(row, 1, "Aucune hypothese detectee dans le cartouche.").font = normal_font
        row += 1
    row += 1

    # --- Ecarts plan/tableaux ---
    ws.cell(row, 1, "ECARTS PLAN / TABLEAUX RECAPITULATIFS").font = section_font
    row += 1
    if rapport.get("discrepancies"):
        headers = ["Prefixe", "Famille", "Plan (rep.)", "Tableau (lignes)", "Absents tableau", "Absents plan", "Severite"]
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row, c, h)
            cell.font = header_font
            cell.fill = header_fill
            cell.border = thin_border
        row += 1
        for d in rapport["discrepancies"]:
            ws.cell(row, 1, d["prefix"]).font = normal_font
            ws.cell(row, 2, d["family"]).font = normal_font
            ws.cell(row, 3, d["plan_count"]).font = normal_font
            ws.cell(row, 4, d["table_count"]).font = normal_font
            ws.cell(row, 5, ", ".join(d.get("missing_in_table", []))).font = warn_font
            ws.cell(row, 6, ", ".join(d.get("missing_in_plan", []))).font = warn_font
            ws.cell(row, 7, d["severity"]).font = warn_font if d["severity"] == "ERROR" else normal_font
            for c in range(1, 8):
                ws.cell(row, c).border = thin_border
            row += 1
    else:
        ws.cell(row, 1, "Aucun ecart detecte.").font = normal_font
        row += 1
    row += 1

    # --- Lineaire par type ---
    ws.cell(row, 1, "LINEAIRE PAR TYPE").font = section_font
    row += 1
    if rapport.get("linears"):
        headers = ["Reference", "Famille", "Section (cm)", "Niveau", "Nb occ.", "Long. (m)", "Total (m)"]
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row, c, h)
            cell.font = header_font
            cell.fill = header_fill
            cell.border = thin_border
        row += 1
        for lin in rapport["linears"]:
            ws.cell(row, 1, lin["reference"]).font = normal_font
            ws.cell(row, 2, lin["family"]).font = normal_font
            sec = f"{lin['section_cm'][0]}x{lin['section_cm'][1]}" if lin.get("section_cm") else "-"
            ws.cell(row, 3, sec).font = normal_font
            ws.cell(row, 4, lin["level"]).font = normal_font
            ws.cell(row, 5, lin["count"]).font = normal_font
            ws.cell(row, 6, round(lin["length_m"], 2)).font = normal_font
            ws.cell(row, 7, round(lin["length_m"] * lin["count"], 2)).font = normal_font
            for c in range(1, 8):
                ws.cell(row, c).border = thin_border
            row += 1
    row += 1

    # --- Volume beton ---
    ws.cell(row, 1, "VOLUME BETON ESTIME").font = section_font
    row += 1
    if rapport.get("concrete_volumes"):
        headers = ["Reference", "Famille", "Section (cm)", "Niveau", "Nb", "Long. (m)", "Volume (m3)"]
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row, c, h)
            cell.font = header_font
            cell.fill = header_fill
            cell.border = thin_border
        row += 1
        total_vol = 0.0
        for cv in rapport["concrete_volumes"]:
            if cv.get("volume_m3", 0) > 0:
                ws.cell(row, 1, cv["reference"]).font = normal_font
                ws.cell(row, 2, cv["family"]).font = normal_font
                sec = f"{cv['section_cm'][0]}x{cv['section_cm'][1]}" if cv.get("section_cm") else "-"
                ws.cell(row, 3, sec).font = normal_font
                ws.cell(row, 4, cv["level"]).font = normal_font
                ws.cell(row, 5, cv["count"]).font = normal_font
                ws.cell(row, 6, round(cv["length_m"], 2)).font = normal_font
                ws.cell(row, 7, round(cv["volume_m3"], 4)).font = normal_font
                for c in range(1, 8):
                    ws.cell(row, c).border = thin_border
                total_vol += cv["volume_m3"]
                row += 1
        ws.cell(row, 1, "TOTAL").font = Font(bold=True, size=11)
        ws.cell(row, 7, round(total_vol, 3)).font = Font(bold=True, size=11)
        for c in range(1, 8):
            ws.cell(row, c).border = thin_border
        row += 1
    row += 1

    # --- Sections sans ferraillage ---
    ws.cell(row, 1, "SECTIONS SANS DETAIL DE FERRAILLAGE").font = section_font
    row += 1
    if rapport.get("missing_ferraillage"):
        headers = ["Reference", "Famille", "Section", "Niveau"]
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row, c, h)
            cell.font = header_font
            cell.fill = header_fill
            cell.border = thin_border
        row += 1
        for mf in rapport["missing_ferraillage"]:
            ws.cell(row, 1, mf["reference"]).font = normal_font
            ws.cell(row, 2, mf["family"]).font = normal_font
            ws.cell(row, 3, mf["section_text"]).font = normal_font
            ws.cell(row, 4, mf["level"]).font = normal_font
            for c in range(1, 5):
                ws.cell(row, c).border = thin_border
            row += 1
    else:
        ws.cell(row, 1, "Toutes les sections ont un detail de ferraillage.").font = normal_font
        row += 1
    row += 1

    # --- Avertissements ---
    all_warnings = rapport.get("warnings", [])
    if all_warnings:
        ws.cell(row, 1, "AVERTISSEMENTS").font = section_font
        row += 1
        for w in all_warnings[:20]:
            ws.cell(row, 1, w).font = warn_font
            row += 1

    # --- Analyse geometrique ---
    geometry_analysis = plan_data.get("_meta", {}).get("geometry_analysis", {})
    if geometry_analysis and not geometry_analysis.get("error"):
        row += 1
        ws.cell(row, 1, "ANALYSE GEOMETRIQUE (DESSINS)").font = section_font
        row += 1
        ws.cell(row, 1, "Formes detectees").font = normal_font
        ws.cell(row, 2, geometry_analysis.get("total_shapes", 0)).font = normal_font
        row += 1
        by_fam = geometry_analysis.get("by_family", {})
        if by_fam:
            for fam, count in by_fam.items():
                ws.cell(row, 1, f"  {fam}").font = normal_font
                ws.cell(row, 2, count).font = normal_font
                row += 1

    # --- Clustering spatial par niveau ---
    level_clustering = plan_data.get("_meta", {}).get("level_clustering", {})
    if level_clustering and not level_clustering.get("error"):
        row += 1
        ws.cell(row, 1, "CLUSTERING SPATIAL PAR NIVEAU").font = section_font
        row += 1
        ws.cell(row, 1, "Confiance").font = normal_font
        ws.cell(row, 2, f"{level_clustering.get('confidence', 0):.0%}").font = normal_font
        row += 1
        zones = level_clustering.get("zones", [])
        if zones:
            for z in zones:
                if z.get("count", 0) > 0:
                    ws.cell(row, 1, f"  {z['level']}").font = normal_font
                    ws.cell(row, 2, f"{z['count']} elements").font = normal_font
                    row += 1

    # Ajuster largeur colonnes
    for col in range(1, 8):
        letter = get_column_letter(col)
        ws.column_dimensions[letter].width = 20

    wb.save(xlsx_path)
    print(f"Feuille 'Rapport Metier' ajoutee : {xlsx_path}")


# ============================================================================
# Moteur principal
# ============================================================================

class MetreGenerator:
    """Genere le classeur Excel.

    moteur="gabarit" (defaut) : pont vers le gabarit standardise 5 feuilles
    via core/populate_modele.py.
    moteur="historique" : moteur 2 feuilles embarque (Detail + Armatures).
    """

    def __init__(self, plan_data: dict, moteur: str = "gabarit"):
        from core.normalization import normalize_plan_data
        self.plan = _normalise_plan_data(plan_data)
        self.normalized = normalize_plan_data(self.plan)
        self.moteur = moteur
        self.diameters = _detect_diameters(self.plan)
        self.wb = openpyxl.Workbook()

    def generer(self, output_path: str):
        """Point d'entree : genere le classeur complet."""
        if self.moteur == "gabarit":
            return generer_via_gabarit(self.plan, output_path)
        return self._generer_historique(output_path)

    def _generer_historique(self, output_path: str):
        """Moteur historique 2 feuilles (conserve pour compatibilite)."""
        # Feuille 1 : Detail quantitatif fondation
        ws_detail = self.wb.active
        detail = FeuilleDetailFondation(self.plan)
        detail.generer(ws_detail)

        # Feuille 2 : Armatures
        ws_arm = self.wb.create_sheet()
        armatures = FeuilleArmatures(self.plan, self.diameters)
        armatures.generer(ws_arm)

        # Sauvegarder
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        self.wb.save(output_path)
        print(f"Classeur genere : {output_path}")
        return output_path


# ============================================================================
# Main
# ============================================================================

def main():
    ap = argparse.ArgumentParser(description="Generateur de metre BA dynamique")
    ap.add_argument("--plan", "--input", "-i", dest="plan",
                    default="sample_plan_data.json",
                    help="Chemin vers le JSON du plan")
    ap.add_argument("--dxf", dest="plan",
                    help="Alias : plan source AutoCAD DXF (converti en JSON)")
    ap.add_argument("--json", dest="plan",
                    help="Alias : plan source JSON")
    ap.add_argument("--out", "-o", default="output/metre_genere.xlsx",
                    help="Chemin de sortie Excel")
    ap.add_argument("--moteur", choices=["gabarit", "historique"],
                    default="gabarit",
                    help="Moteur de rendu (defaut : gabarit 5 feuilles)")
    args = ap.parse_args()

    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(f"ERREUR : Fichier introuvable -- {plan_path}")
        sys.exit(1)

    with open(plan_path, "r", encoding="utf-8") as f:
        plan_data = json.load(f)

    gen = MetreGenerator(plan_data, moteur=args.moteur)
    gen.generer(args.out)


if __name__ == "__main__":
    main()
