"""
ZoneSegmenter — segmente le plan en zones fonctionnelles.
Sans savoir ce qu'elles contiennent : cartouche, tableau, legende, vues.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import re


@dataclass
class Zone:
    """Une zone fonctionnelle detectee sur le plan."""
    zone_id: str
    zone_type: str     # cartouche, tableau, legende, vue, inconnue
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str = ""
    cells: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2

    def to_dict(self) -> dict:
        return {
            "id": self.zone_id,
            "type": self.zone_type,
            "page": self.page,
            "bbox": [self.x0, self.y0, self.x1, self.y1],
            "text_preview": self.text[:200] if self.text else "",
            "confidence": self.confidence,
        }


class ZoneSegmenter:
    """Segmente un plan en zones fonctionnelles."""

    # Mots-cles pour classification des zones
    LEGEND_KEYWORDS = re.compile(
        r"(?i)legende|nomenclature|abreviation|symbole|notation|convention",
    )
    TABLE_KEYWORDS = re.compile(
        r"(?i)tableau|caract[eé]ristique|dimension|ferraillage|d[eé]tail",
    )
    CARTOUCHE_KEYWORDS = re.compile(
        r"(?i)cartouche|projet|architecte|bureau|echelle|date|dessin[eé]",
    )
    TITLE_KEYWORDS = re.compile(
        r"(?i)fondation|RDC|[eé]tage|plan\s+(?:type|coupe|d[ée]tail)",
    )

    def segment_page(self, page_num: int, page) -> list[Zone]:
        """Segmente une page en zones."""
        zones = []
        zone_counter = 0

        try:
            text_dict = page.get_text("dict")
        except Exception:
            return zones

        blocks = text_dict.get("blocks", [])

        # Grouper les blocs textuels par proximite spatiale
        text_blocks = []
        for b in blocks:
            if b.get("type") == 0:
                bbox = b.get("bbox", [0, 0, 0, 0])
                text_content = ""
                for line in b.get("lines", []):
                    for span in line.get("spans", []):
                        text_content += span.get("text", "")
                    text_content += "\n"
                text_blocks.append({
                    "bbox": bbox,
                    "text": text_content.strip(),
                    "x0": bbox[0], "y0": bbox[1],
                    "x1": bbox[2], "y1": bbox[3],
                })

        if not text_blocks:
            return zones

        # Detecter les zones par classification de blocs
        classified = self._classify_blocks(text_blocks, page_num)

        # Regrouper les blocs proches en zones
        zones = self._merge_nearby_zones(classified, page_num)

        return zones

    def _classify_blocks(self, blocks: list[dict], page_num: int) -> list[Zone]:
        """Classifie chaque bloc en type de zone."""
        zones = []
        for i, b in enumerate(blocks):
            text = b["text"]
            zone_type = "inconnue"
            confidence = 0.3

            if self.LEGEND_KEYWORDS.search(text):
                zone_type = "legende"
                confidence = 0.9
            elif self.TABLE_KEYWORDS.search(text):
                zone_type = "tableau"
                confidence = 0.8
            elif self.CARTOUCHE_KEYWORDS.search(text):
                zone_type = "cartouche"
                confidence = 0.85
            elif self.TITLE_KEYWORDS.search(text):
                zone_type = "titre_vue"
                confidence = 0.85

            zones.append(Zone(
                zone_id=f"p{page_num}_z{i}",
                zone_type=zone_type,
                page=page_num,
                x0=b["x0"], y0=b["y0"],
                x1=b["x1"], y1=b["y1"],
                text=text,
                confidence=confidence,
            ))

        return zones

    def _merge_nearby_zones(self, zones: list[Zone], page_num: int) -> list[Zone]:
        """Regroupe les zones de meme type proches spatialement."""
        if not zones:
            return []

        # Trier par type puis par position
        merged = []
        used = set()

        for i, z1 in enumerate(zones):
            if i in used:
                continue

            group = [z1]
            for j, z2 in enumerate(zones):
                if j <= i or j in used:
                    continue
                if z1.zone_type == z2.zone_type and z1.zone_type != "inconnue":
                    dist = ((z1.center_x - z2.center_x)**2 +
                            (z1.center_y - z2.center_y)**2) ** 0.5
                    if dist < 300:  # proche spatialement
                        group.append(z2)
                        used.add(j)

            # Fusionner le groupe
            x0 = min(z.x0 for z in group)
            y0 = min(z.y0 for z in group)
            x1 = max(z.x1 for z in group)
            y1 = max(z.y1 for z in group)
            text = "\n".join(z.text for z in group if z.text)

            merged.append(Zone(
                zone_id=f"p{page_num}_z{i}",
                zone_type=z1.zone_type,
                page=page_num,
                x0=x0, y0=y0, x1=x1, y1=y1,
                text=text,
                confidence=max(z.confidence for z in group),
            ))
            used.add(i)

        # Ajouter les zones inclassifiees restantes
        for i, z in enumerate(zones):
            if i not in used:
                merged.append(z)

        return merged

    def segment_document(self, doc) -> list[Zone]:
        """Segmente tout le document."""
        all_zones = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            zones = self.segment_page(page_num + 1, page)
            all_zones.extend(zones)
        return all_zones

    def get_zones_by_type(self, zones: list[Zone], zone_type: str) -> list[Zone]:
        return [z for z in zones if z.zone_type == zone_type]

    def get_table_zones(self, zones: list[Zone]) -> list[Zone]:
        return self.get_zones_by_type(zones, "tableau")

    def get_legend_zones(self, zones: list[Zone]) -> list[Zone]:
        return self.get_zones_by_type(zones, "legende")
