"""
GridLearner — detecte les axes et cotes pour chaque vue.
Construit un systeme de coordonnees propre a CE plan.
"""
from __future__ import annotations
import re
from typing import Any, Optional
from .evidence_store import Evidence, SourceType


class GridLearner:
    """Apprend la grille d'axes et cotes pour chaque vue."""

    # Patterns de detection des axes
    AXIS_LETTER_RX = re.compile(r"^[A-Z]$")
    AXIS_NUM_RX = re.compile(r"^\d{1,2}$")

    # Patterns de detection des cotes
    COTE_RX = re.compile(
        r"(?:^|\s)(\d{2,4}(?:\.\d+)?)\s*(?:cm|m)?(?:\s|$)",
    )

    def learn_grid_from_text(self, text: str, page: int = 1) -> dict[str, Any]:
        """Apprend la grille depuis le texte d'une page."""
        grid = {
            "axes_lettres": [],
            "axes_chiffres": [],
            "cotes_lettres": [],
            "cotes_chiffres": [],
            "positions": {},
        }

        lines = text.split("\n")
        for i, line in enumerate(lines):
            line_stripped = line.strip()

            # Detecter les axes (lettres)
            if self.AXIS_LETTER_RX.match(line_stripped):
                grid["axes_lettres"].append(line_stripped)

            # Detecter les axes (chiffres)
            if self.AXIS_NUM_RX.match(line_stripped):
                grid["axes_chiffres"].append(line_stripped)

            # Detecter les cotes dans les environs
            cotes = self._extract_cotes_near_line(lines, i)
            if cotes:
                if grid["axes_lettres"] and not grid["cotes_lettres"]:
                    grid["cotes_lettres"] = cotes
                elif grid["axes_chiffres"] and not grid["cotes_chiffres"]:
                    grid["cotes_chiffres"] = cotes

        return grid

    def learn_grid_from_table(self, table_data: list) -> dict[str, Any]:
        """Apprend la grille depuis un tableau de cotes (comme Table 0 du plan A0)."""
        grid = {
            "axes_lettres": [],
            "axes_chiffres": [],
            "cotes_lettres": [],
            "cotes_chiffres": [],
            "positions": {},
        }

        for row in table_data:
            for cell in row:
                cell_str = str(cell or "")
                # Detecter les lettres d'axes
                letters = re.findall(r"\b([A-K])\b", cell_str)
                if letters and not grid["axes_lettres"]:
                    grid["axes_lettres"] = sorted(set(letters))

                # Detecter les cotes (nombres dans une sequence)
                cotes = self._extract_cote_sequence(cell_str)
                if cotes and not grid["cotes_lettres"]:
                    grid["cotes_lettres"] = cotes

        return grid

    def learn_grids_per_view(self, doc) -> dict[str, dict]:
        """Apprend les grilles pour chaque vue du document."""
        grids = {}

        for page_num in range(len(doc)):
            page = doc[page_num]
            try:
                text = page.get_text("text")
            except Exception:
                continue

            # Identifier la vue (fondation, RDC, etage...)
            vue = self._identify_view(text, page_num + 1)

            # Extraire la grille
            grid = self.learn_grid_from_text(text, page_num + 1)

            if grid["axes_lettres"] or grid["axes_chiffres"]:
                grids[vue] = grid

        return grids

    def calculate_positions(self, grid: dict, file_x_positions: dict[str, float]) -> dict[str, float]:
        """Calcule les positions reelles des axes en metres."""
        positions = {}

        cotes = grid.get("cotes_lettres", [])
        axes = grid.get("axes_lettres", [])

        if cotes and axes:
            cumulative = 0.0
            for i, axis in enumerate(axes):
                positions[axis] = round(cumulative, 3)
                if i < len(cotes):
                    cumulative += cotes[i] / 100  # cm -> m

        return positions

    def map_occurrence_to_axis(self, occ_x: float, grid: dict,
                                file_x_map: dict[str, float]) -> Optional[str]:
        """Mappe une occurrence a l'axe le plus proche."""
        if not file_x_map:
            return None

        best_axis = None
        best_dist = float("inf")

        for axis, x_pos in file_x_map.items():
            dist = abs(occ_x - x_pos)
            if dist < best_dist:
                best_dist = dist
                best_axis = axis

        return best_axis

    def _extract_cotes_near_line(self, lines: list[str], center_idx: int,
                                   window: int = 3) -> list[float]:
        """Extrait les cotes numeriques pres d'une ligne."""
        cotes = []
        seen = set()

        for i in range(max(0, center_idx - window),
                       min(len(lines), center_idx + window + 1)):
            line = lines[i]
            for m in self.COTE_RX.finditer(line):
                try:
                    val = float(m.group(1))
                    if val not in seen and 100 <= val <= 800:
                        seen.add(val)
                        cotes.append(val)
                except ValueError:
                    pass

        return cotes

    def _extract_cote_sequence(self, cell_text: str) -> list[float]:
        """Extrait une sequence de cotes depuis une cellule de tableau."""
        nums = re.findall(r"\b(\d+(?:\.\d+)?)\b", cell_text)
        if len(nums) < 5:
            return []

        cotes = []
        seen = set()
        for n in nums:
            try:
                val = float(n)
                if 10 <= val <= 800 and val not in seen:
                    seen.add(val)
                    cotes.append(val)
            except ValueError:
                pass

        # Detecter la fin de la sequence (valeur qui se repete)
        if len(cotes) >= 10:
            first = cotes[0]
            for idx in range(1, len(cotes)):
                if cotes[idx] == first:
                    cotes = cotes[:idx]
                    break

        return cotes if len(cotes) >= 3 else []

    def _identify_view(self, text: str, page_num: int) -> str:
        """Identifie la vue d'une page."""
        text_upper = text.upper()

        if "FONDATION" in text_upper:
            return "Fondation"
        elif "RDC" in text_upper or "REZ-DE-CHAUSSEE" in text_upper:
            return "RDC"
        elif "ETAGE" in text_upper or "ÉTAGE" in text_upper:
            # Chercher le numero d'etage
            m = re.search(r"(?:1ER?|2E|3E|4E|5E)\s*(?:ETAGE|ÉTAGE)", text_upper)
            if m:
                return m.group(0).strip()
            return "Etage"
        elif "COUPE" in text_upper:
            return "Coupe"
        elif "DETAIL" in text_upper or "DÉTAIL" in text_upper:
            return "Detail"
        else:
            return f"Page_{page_num}"
