"""
ElementScanner — scan les vues pour detecter les elements structuraux.
Regex dynamique generee depuis la legende apprise.
"""
from __future__ import annotations
import re
from typing import Optional
from .evidence_store import Observation


class ElementScanner:
    """Scanne les vues pour detecter les repetes d'elements structuraux."""

    # Pattern generique pour les sections entre parentheses
    SECTION_RX = re.compile(
        r"[-\s]?\((\d{1,3})\s*[xX×*]\s*(\d{1,3})\)",
    )

    # Pattern pour les sections apres tiret
    SECTION_DASH_RX = re.compile(
        r"[-\s](\d{1,3})\s*[xX×*]\s*(\d{1,3})",
    )

    # Pattern pour les annotations avec ferraillage
    FERRAILLAGE_RX = re.compile(
        r"(\d+)\s*([THA])\s*(\d+)",
        re.I,
    )

    def __init__(self, legend_regex: re.Pattern = None,
                 bare_prefixes: list[str] = None):
        """Initialisation avec la regex dynamique de la legende."""
        self.legend_regex = legend_regex or re.compile(
            r"\b([A-Za-z]{1,5})(\d{1,3})\b"
        )
        self._bare_prefixes = bare_prefixes or []

    def scan_page(self, page, page_num: int) -> list[Observation]:
        """Scanne une page pour detecter les elements structuraux."""
        observations = []
        obs_counter = 0

        try:
            text_dict = page.get_text("dict")
        except Exception:
            return observations

        blocks = text_dict.get("blocks", [])

        for block in blocks:
            if block.get("type") != 0:
                continue

            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    bbox = span.get("bbox", [0, 0, 0, 0])

                    if not text:
                        continue

                    # Detecter les repetes avec la regex dynamique
                    matches = list(self.legend_regex.finditer(text))

                    for m in matches:
                        prefix = m.group(1)
                        number = m.group(2)
                        repere = f"{prefix}{number}"

                        # Extraire la section si presente
                        section = None
                        section_m = self.SECTION_RX.search(text)
                        if not section_m:
                            section_m = self.SECTION_DASH_RX.search(text)
                        if section_m:
                            section = f"{section_m.group(1)}x{section_m.group(2)}"

                        # Extraire le ferraillage si present
                        ferr = None
                        ferr_m = self.FERRAILLAGE_RX.search(text)
                        if ferr_m:
                            ferr = f"{ferr_m.group(1)}{ferr_m.group(2).upper()}{ferr_m.group(3)}"

                        observations.append(Observation(
                            obs_id=f"p{page_num}_obs{obs_counter}",
                            text=text,
                            page=page_num,
                            x=(bbox[0] + bbox[2]) / 2,
                            y=(bbox[1] + bbox[3]) / 2,
                            x2=bbox[2],
                            y2=bbox[3],
                            obs_type="element_label",
                            raw_data={
                                "repere": repere,
                                "prefix": prefix,
                                "number": number,
                                "section_inline": section,
                                "ferr_inline": ferr,
                            },
                        ))
                        obs_counter += 1

                    # Aussi detecter les labels sans numero (ex: BN, CH)
                    # dans le format "BN-(25X30)" ou "CH-(40X20)"
                    if not matches and self._bare_prefixes:
                        for prefix in self._bare_prefixes:
                            if re.search(r"\b" + re.escape(prefix) + r"\b", text, re.I):
                                section = None
                                sm = self.SECTION_RX.search(text)
                                if not sm:
                                    sm = self.SECTION_DASH_RX.search(text)
                                if sm:
                                    section = f"{sm.group(1)}x{sm.group(2)}"

                                ferr = None
                                fm = self.FERRAILLAGE_RX.search(text)
                                if fm:
                                    ferr = f"{fm.group(1)}{fm.group(2).upper()}{fm.group(3)}"

                                observations.append(Observation(
                                    obs_id=f"p{page_num}_obs{obs_counter}",
                                    text=text,
                                    page=page_num,
                                    x=(bbox[0] + bbox[2]) / 2,
                                    y=(bbox[1] + bbox[3]) / 2,
                                    x2=bbox[2],
                                    y2=bbox[3],
                                    obs_type="element_label",
                                    raw_data={
                                        "repere": prefix,
                                        "prefix": prefix,
                                        "number": "",
                                        "section_inline": section,
                                        "ferr_inline": ferr,
                                    },
                                ))
                                obs_counter += 1
                                break

        return observations

    def scan_document(self, doc) -> list[Observation]:
        """Scanne tout le document."""
        all_obs = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            obs = self.scan_page(page, page_num + 1)
            all_obs.extend(obs)
        return all_obs

    def group_by_repere(self, observations: list[Observation]) -> dict[str, list[Observation]]:
        """Groupe les observations par repere."""
        groups = {}
        for obs in observations:
            repere = obs.raw_data.get("repere", "")
            if repere:
                groups.setdefault(repere, []).append(obs)
        return groups

    def group_by_view(self, observations: list[Observation],
                       view_map: dict[int, str]) -> dict[str, list[Observation]]:
        """Groupe les observations par vue."""
        groups = {}
        for obs in observations:
            vue = view_map.get(obs.page, f"page_{obs.page}")
            groups.setdefault(vue, []).append(obs)
        return groups

    def count_by_prefix(self, observations: list[Observation]) -> dict[str, int]:
        """Compte les occurrences par prefixe."""
        counts = {}
        for obs in observations:
            prefix = obs.raw_data.get("prefix", "")
            if prefix:
                counts[prefix] = counts.get(prefix, 0) + 1
        return counts

    def find_observations_in_zone(self, observations: list[Observation],
                                    x0: float, y0: float,
                                    x1: float, y1: float) -> list[Observation]:
        """Retourne les observations dans une zone geometrique."""
        result = []
        for obs in observations:
            if x0 <= obs.x <= x1 and y0 <= obs.y <= y1:
                result.append(obs)
        return result
