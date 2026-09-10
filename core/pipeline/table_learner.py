"""
TableLearner — detecte et lit les tableaux decaracteristiques.
Detection universelle par en-tetes, pas par index fixe.
"""
from __future__ import annotations
import re
from typing import Any, Optional
from .evidence_store import Evidence, SourceType


class TableLearner:
    """Detecte et lit tous les tableaux du plan, dynamiquement."""

    # Patterns de detection de colonnes par leur contenu
    COLUMN_PATTERNS = {
        "repere": re.compile(r"(?i)rep[eè]re|designation|nom|element|code|ref"),
        "section": re.compile(r"(?i)section|dimension|b\s*[xX×]\s*h|epaisseur"),
        "ferraillage": re.compile(r"(?i)ferraillage|armature|acier|fer|arm|ha\s*\d"),
        "longueur": re.compile(r"(?i)long(?:ueur)?|portee|porte|lg|l\b"),
        "largeur": re.compile(r"(?i)larg(?:ueur)?|l\b"),
        "hauteur": re.compile(r"(?i)haut(?:eur)?|h\b|ep(?:aisseur)?"),
        "nbre": re.compile(r"(?i)nb|nombre|quantit|qty"),
        "type": re.compile(r"(?i)type|famille|classe|categorie"),
        "niveau": re.compile(r"(?i)niveau|etage|rdc|plan|phase"),
        "poids": re.compile(r"(?i)poids|mass|weight"),
        "observation": re.compile(r"(?i)observ|remarque|note|comment"),
    }

    # Patterns de titres de tableaux
    TABLE_TITLE_RX = re.compile(
        r"tableau\s+(?:des?\s+)?(\w+)|"
        r"caract[eé]ristique\s+(?:des?\s+)?(\w+)|"
        r"d[eé]tail\s+(?:des?\s+)?(\w+)",
        re.I,
    )

    def detect_tables_from_pymupdf(self, page) -> list[dict]:
        """Detecte les tableaux avec PyMuPDF find_tables()."""
        tables = []
        try:
            tab_finder = page.find_tables()
            for i, tab in enumerate(tab_finder.tables):
                data = tab.extract()
                if not data or len(data) < 2:
                    continue

                # Extraire les cellules avec positions
                cells_with_pos = []
                for ri, row in enumerate(data):
                    for ci, cell in enumerate(row):
                        cell_text = str(cell or "").strip()
                        if cell_text:
                            bbox = tab.bboxes[ri][ci] if hasattr(tab, 'bboxes') else [0, 0, 0, 0]
                            cells_with_pos.append({
                                "text": cell_text,
                                "row": ri,
                                "col": ci,
                                "bbox": bbox,
                            })

                tables.append({
                    "id": f"table_{i}",
                    "cells": cells_with_pos,
                    "raw_data": data,
                    "page": page.number + 1 if hasattr(page, 'number') else 1,
                })
        except Exception:
            pass

        return tables

    def learn_table(self, raw_table: dict) -> dict[str, dict[str, Evidence]]:
        """Apprend un tableau : detecte les en-tetes et construit le dictionnaire."""
        cells = raw_table.get("cells", [])
        raw_data = raw_table.get("raw_data", [])
        page = raw_table.get("page", 1)

        if not cells and not raw_data:
            return {}

        # Si on a les donnees brutes, travailler avec
        if raw_data:
            # Detecter si c'est un tableau transpose (poteaux)
            if self._is_transposed_table(raw_data):
                return self._learn_transposed_table(raw_data, page)
            return self._learn_from_raw_data(raw_data, page)

        # Sinon, travailler avec les cellules extraites
        return self._learn_from_cells(cells, page)

    def _is_transposed_table(self, raw_data: list) -> bool:
        """Detecte si un tableau est transpose (colonnes = elements)."""
        if len(raw_data) < 2:
            return False
        # La premiere ligne contient des repere (P2, P3, etc.)
        header = raw_data[0]
        repere_count = 0
        for cell in header:
            cell_text = str(cell or "").strip()
            if re.match(r"^[A-Z]\d+$", cell_text):
                repere_count += 1
        return repere_count >= 3

    def _learn_transposed_table(self, raw_data: list, page: int) -> dict[str, dict[str, Evidence]]:
        """Parse un tableau transpose (colonnes = elements, rangees = niveaux)."""
        if len(raw_data) < 2:
            return {}

        # Premiere rangee = en-tetes (repere)
        header_row = raw_data[0]
        repere_cols = {}
        for ci, cell in enumerate(header_row):
            cell_text = str(cell or "").strip()
            m = re.match(r"^([A-Z]\d+)$", cell_text)
            if m:
                repere_cols[ci] = m.group(1)

        if not repere_cols:
            return {}

        result = {}

        for ci, repere in repere_cols.items():
            row_data = {}
            for ri in range(1, len(raw_data)):
                if ci >= len(raw_data[ri]):
                    continue
                cell = str(raw_data[ri][ci] or "")
                if not cell.strip():
                    continue

                # Extraire la section: chercher explicitement les lignes "25\n30" ou "25 30"
                # qui sont les dimensions en cm (apres le ferraillage)
                lines = [l.strip() for l in cell.split("\n") if l.strip()]
                a_cm = None
                b_cm = None
                ferr_text = None

                for li, line in enumerate(lines):
                    # Chercher le ferraillage: "8T12", "12T12", "14T14", etc.
                    fm = re.match(r"^(\d+)\s*([THA])\s*(\d+)", line, re.I)
                    if fm:
                        ferr_text = f"{fm.group(1)}{fm.group(2).upper()}{fm.group(3)}"
                        continue

                    # Chercher la section: nombre a 2 chiffres sur une ligne seule
                    # "25" ou "30" (pas "12" qui est du ferraillage)
                    if re.match(r"^\d{2,3}$", line):
                        val = int(line)
                        if 15 <= val <= 80:  # Dimensions de poteau raisonnables
                            if a_cm is None:
                                a_cm = val
                            elif b_cm is None:
                                b_cm = val

                if a_cm and b_cm:
                    # S'assurer que a <= b (convention)
                    if a_cm > b_cm:
                        a_cm, b_cm = b_cm, a_cm
                    row_data["section"] = Evidence(
                        value=f"{a_cm}x{b_cm}",
                        source_type=SourceType.LU,
                        source_location=f"table_transposed/col{ci}/row{ri}",
                        page=page,
                    )
                    row_data["a"] = Evidence(
                        value=a_cm / 100,
                        source_type=SourceType.CALCULE,
                        source_location=f"table_transposed/col{ci}/a",
                        page=page,
                    )
                    row_data["b"] = Evidence(
                        value=b_cm / 100,
                        source_type=SourceType.CALCULE,
                        source_location=f"table_transposed/col{ci}/b",
                        page=page,
                    )

                if ferr_text:
                    row_data["ferraillage"] = Evidence(
                        value=ferr_text,
                        source_type=SourceType.LU,
                        source_location=f"table_transposed/col{ci}/ferraillage",
                        page=page,
                    )

            if row_data:
                result[repere] = row_data

        return result

    def _learn_from_raw_data(self, raw_data: list, page: int) -> dict[str, dict[str, Evidence]]:
        """Apprend un tableau depuis les donnees brutes PyMuPDF."""
        if len(raw_data) < 2:
            return {}

        # La premiere ligne contient souvent les en-tetes
        header_row = raw_data[0]
        headers = []
        for ci, cell in enumerate(header_row):
            cell_text = str(cell or "").strip()
            if cell_text:
                col_type = self._classify_column(cell_text)
                headers.append({"text": cell_text, "col": ci, "type": col_type})
            else:
                headers.append({"text": "", "col": ci, "type": "unknown"})

        if not headers:
            return {}

        # Trouver la colonne repere
        rep_col = None
        for h in headers:
            if h["type"] == "repere":
                rep_col = h["col"]
                break
        if rep_col is None:
            # Premiere colonne avec des valeurs alpha-numeriques
            for ri, row in enumerate(raw_data[1:], 1):
                for ci, cell in enumerate(row):
                    cell_text = str(cell or "").strip()
                    if re.match(r"^[A-Za-z]+\d+", cell_text):
                        rep_col = ci
                        break
                if rep_col is not None:
                    break

        if rep_col is None:
            return {}

        # Extraire les donnees
        result = {}
        for ri, row in enumerate(raw_data[1:], 1):
            rep_cell = str(row[rep_col] or "").strip() if rep_col < len(row) else ""
            if not rep_cell:
                continue

            row_data = {}
            for h in headers:
                if h["col"] == rep_col:
                    continue
                if h["col"] < len(row):
                    cell_text = str(row[h["col"]] or "").strip()
                    if cell_text:
                        row_data[h["text"]] = Evidence(
                            value=cell_text,
                            source_type=SourceType.LU,
                            source_location=f"table/page{ri+1}/row{ri}/col{h['text']}",
                            page=page,
                            confidence=0.9,
                        )

            if row_data:
                result[rep_cell] = row_data

        return result

    def _learn_from_cells(self, cells: list[dict], page: int) -> dict[str, dict[str, Evidence]]:
        """Apprend un tableau depuis des cellules avec positions."""
        if not cells:
            return {}

        # Trouver les en-tetes (premiere rangee)
        min_row = min(c["row"] for c in cells)
        header_cells = [c for c in cells if c["row"] == min_row]

        headers = []
        for hc in sorted(header_cells, key=lambda c: c["col"]):
            col_type = self._classify_column(hc["text"])
            headers.append({"text": hc["text"], "col": hc["col"], "type": col_type})

        # Trouver colonne repere
        rep_col = None
        for h in headers:
            if h["type"] == "repere":
                rep_col = h["col"]
                break

        if rep_col is None:
            return {}

        # Grouper les cellules par rangee
        rows = {}
        for c in cells:
            if c["row"] == min_row:
                continue
            rows.setdefault(c["row"], {})[c["col"]] = c["text"]

        result = {}
        for row_idx, row_cells in sorted(rows.items()):
            rep = row_cells.get(rep_col, "").strip()
            if not rep:
                continue

            row_data = {}
            for h in headers:
                if h["col"] == rep_col:
                    continue
                cell_text = row_cells.get(h["col"], "").strip()
                if cell_text:
                    row_data[h["text"]] = Evidence(
                        value=cell_text,
                        source_type=SourceType.LU,
                        source_location=f"table/row{row_idx}/col{h['text']}",
                        page=page,
                        confidence=0.9,
                    )

            if row_data:
                result[rep] = row_data

        return result

    def _classify_column(self, header_text: str) -> str:
        """Classifie une colonne par son en-tete."""
        text = header_text.strip().lower()
        for col_type, pattern in self.COLUMN_PATTERNS.items():
            if pattern.search(text):
                return col_type
        return "unknown"

    def extract_section_from_cell(self, cell_text: str) -> Optional[dict[str, float]]:
        """Extrait les dimensions d'une cellule section (ex: 25x40, 0.25x0.40)."""
        if not cell_text:
            return None

        # Pattern: NxM ou NxN (en cm ou m)
        m = re.search(r"(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)", cell_text)
        if m:
            a = float(m.group(1))
            b = float(m.group(2))
            # Normaliser en metres si les valeurs sont > 10 (probablement cm)
            if a > 10:
                a /= 100
            if b > 10:
                b /= 100
            return {"a": a, "b": b}

        return None

    def extract_ferraillage_from_cell(self, cell_text: str) -> Optional[list[dict]]:
        """Extrait le ferraillage d'une cellule (ex: 8T12, 4HA10+2HA8)."""
        if not cell_text:
            return None

        result = []
        # Pattern: NbT/Nb + diametre
        for m in re.finditer(r"(\d+)\s*([THA])\s*(\d+)", cell_text, re.I):
            count = int(m.group(1))
            bar_type = m.group(2).upper()
            diameter = int(m.group(3))
            result.append({
                "nb": count,
                "type": bar_type,
                "diametre": diameter,
            })

        return result if result else None

    def get_all_table_reperes(self, tables: dict[str, dict]) -> set[str]:
        """Retourne tous les repere trouves dans tous les tableaux."""
        reperes = set()
        for table_data in tables.values():
            for rep in table_data.keys():
                reperes.add(rep)
        return reperes
