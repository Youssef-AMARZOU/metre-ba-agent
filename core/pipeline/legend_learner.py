"""
LegendLearner — apprend la legende du plan courant.
ZEBRE DICTIONNAIRE EN DUR : tout est lu depuis CE plan.
Fallback = hypothese, jamais un fait confirme.
"""
from __future__ import annotations
import re
from typing import Optional
from .evidence_store import Evidence, SourceType


# Fallback : conventions BA francophones courantes
# MARQUE comme "fallback" — jamais utilise comme source primaire
FALLBACK_LEGEND = {
    "S": "Semelle",
    "P": "Poteau",
    "D": "Dalle",
    "N": "Poutre",
    "LG": "Longrine",
    "CH": "Chainage",
    "BN": "Bande noyee",
    "V": "Voile",
    "R": "Raidisseur",
    "M": "Massif",
    "C": "Console",
    "Lt": "Linteau",
    "RD": "Regard",
    "ESC": "Escalier",
    "PR": "Poutre de redressement",
    "PN": "Poutre",
    "PC": "Poutre de couronnement",
    "SF": "Semelle filante",
}


class LegendLearner:
    """Apprend la legende depuis le plan, sans supposition."""

    # Motifs de detection de la zone legende
    LEGEND_TITLE_RX = re.compile(
        r"(?i)legende|nomenclature|abreviation|symbole|notation|convention",
    )

    # Motifs d'extraction des entrees de legende
    # Format 1: "N = Poutre" ou "N : Poutre" ou "N - Poutre"
    ENTRY_RX_1 = re.compile(
        r"^\s*([A-Za-z]{1,5})\s*[=:–\-]\s*(.+?)\s*$",
    )
    # Format 2: "-N=POUTRE" (sans espace)
    ENTRY_RX_2 = re.compile(
        r"^\s*-?\s*([A-Za-z]{1,5})\s*=\s*(.+?)\s*$",
    )
    # Format 3: "N POUTRE" (espace seul)
    ENTRY_RX_3 = re.compile(
        r"^\s*([A-Za-z]{2,5})\s+([A-Z][A-Za-z\s]{2,30})\s*$",
    )

    def learn_from_text(self, text: str, page: int = 0) -> dict[str, Evidence]:
        """Extrait la legende d'un bloc de texte."""
        legend = {}
        lines = text.split("\n")

        in_legend_block = False
        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue

            # Detecter debut du bloc legende
            if self.LEGEND_TITLE_RX.search(line_stripped):
                in_legend_block = True
                continue

            if in_legend_block:
                entry = self._parse_legend_entry(line_stripped)
                if entry:
                    prefix, meaning = entry
                    legend[prefix] = Evidence(
                        value=meaning,
                        source_type=SourceType.LU,
                        source_location=f"legende_page{page}",
                        page=page,
                        confidence=0.95,
                        note="Extrait de la legende du plan",
                    )
                # Arreter si on sort du bloc (ligne trop longue ou vide)
                elif len(line_stripped) > 100:
                    in_legend_block = False

        return legend

    def learn_from_zones(self, zones: list) -> dict[str, Evidence]:
        """Apprend la legende depuis les zones detectees."""
        legend = {}

        for zone in zones:
            if zone.zone_type == "legende" and zone.text:
                zone_legend = self.learn_from_text(zone.text, zone.page)
                for prefix, evidence in zone_legend.items():
                    if prefix not in legend:
                        legend[prefix] = evidence

        return legend

    def learn_from_table(self, table_data: dict) -> dict[str, Evidence]:
        """Extrait la legende depuis un tableau structure.
        Si un tableau a des entrees comme 'Repere' et 'Type',
        on peut en deduire la correspondance prefixe->type.
        """
        legend = {}

        for repere, cols in table_data.items():
            # Chercher une colonne 'type' ou 'designation' ou 'nom'
            type_val = None
            for col_name in ["type", "designation", "nom", "description", "famille"]:
                if col_name in cols:
                    ev = cols[col_name]
                    if ev.value and isinstance(ev.value, str):
                        type_val = ev.value
                        break

            if type_val and repere:
                # Extraire le prefixe du repere (lettres au debut)
                m = re.match(r"^([A-Za-z]+)", repere)
                if m:
                    prefix = m.group(1).upper()
                    if prefix not in legend:
                        legend[prefix] = Evidence(
                            value=type_val,
                            source_type=SourceType.LU,
                            source_location=f"tableau/{repere}/type",
                            page=0,
                            confidence=0.8,
                            note="Infere depuis le tableau",
                        )

        return legend

    def get_with_fallback(self, learned: dict[str, Evidence]) -> dict[str, Evidence]:
        """Retourne la legende apprise + fallback pour les prefixes manquants.
        Le fallback est MARQUE comme 'fallback', jamais comme 'lu'.
        """
        result = dict(learned)

        for prefix, meaning in FALLBACK_LEGEND.items():
            if prefix not in result:
                result[prefix] = Evidence(
                    value=meaning,
                    source_type=SourceType.FALLBACK,
                    source_location="fallback_conventions_ba",
                    page=0,
                    confidence=0.3,
                    note="Convention generale, non confirmee sur ce plan",
                )

        return result

    def build_dynamic_regex(self, legend: dict[str, Evidence]) -> re.Pattern:
        """Construit une regex dynamique depuis les prefixes de la legende."""
        prefixes = sorted(
            [k for k in legend.keys() if len(k) >= 1],
            key=len,
            reverse=True,
        )
        if not prefixes:
            # Regex ultra-generique si aucune legende
            return re.compile(r"\b([A-Za-z]{1,5})(\d{1,3})\b")

        pattern = r"\b(" + "|".join(re.escape(p) for p in prefixes) + r")(\d{1,3})\b"
        return re.compile(pattern)

    def _parse_legend_entry(self, line: str) -> Optional[tuple[str, str]]:
        """Parse une ligne de legende en (prefixe, signification)."""
        # Mots a ignorer (pas des prefixe d'elements BA)
        ignore = {
            "AXIAL", "BIM", "EMAIL", "TEL", "FAX", "LES", "DES", "TYP",
            "PROJET", "CLASSE", "ROYAUME", "MAROC", "AGENCE", "NOTA",
        }
        for rx in [self.ENTRY_RX_1, self.ENTRY_RX_2, self.ENTRY_RX_3]:
            m = rx.match(line)
            if m:
                prefix = m.group(1).strip()
                meaning = m.group(2).strip()
                # Valider que le prefixe est court (1-5 lettres)
                if 1 <= len(prefix) <= 5 and prefix.isalpha():
                    if prefix.upper() in ignore:
                        continue
                    return prefix.upper(), meaning
        return None

    def validate_legend(self, legend: dict[str, Evidence],
                        observations: list) -> list[str]:
        """Valide la legende en verifiant que les prefixes sont effectivement
        utilises dans les observations du plan."""
        warnings = []
        used_prefixes = set()

        for obs in observations:
            for prefix in legend:
                if obs.text.upper().startswith(prefix):
                    used_prefixes.add(prefix)

        unused = set(legend.keys()) - used_prefixes
        if unused:
            warnings.append(
                f"Prefixes de legende non utilises dans les observations: "
                f"{sorted(unused)}"
            )

        return warnings
