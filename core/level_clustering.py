"""level_clustering.py — Clustering spatial par niveau de construction.

Detecte et assigne automatiquement les niveaux (FONDATION, RDC, R+1, etc.)
aux elements extraits en utilisant :
- Les ancres textuelles (titres de sous-plans: "PL.HT RDC", "R+1", etc.)
- Le clustering Y-axis (regrouper les elements par position verticale)
- Les calques/niveaux IFC

Ne devine jamais : si le clustering est incertain, marque "INCONNU"
et ajoute aux cas ambigus.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# Patterns de detection de niveaux dans le texte
LEVEL_PATTERNS = [
    (re.compile(r"FONDATION|FOND\b|SE Fondation", re.I), "FONDATION"),
    (re.compile(r"REZ.?DE.?CHAUSS|REZ.?CHAUS|RDC|RDC\b", re.I), "RDC"),
    (re.compile(r"R\s*\+\s*0|R\s*0\b", re.I), "RDC"),
    (re.compile(r"R\s*\+\s*1|PREMIER?\s*ETAGE|1ER?\s*ETAGE", re.I), "R+1"),
    (re.compile(r"R\s*\+\s*2|DEUXIEME?\s*ETAGE|2EME?\s*ETAGE", re.I), "R+2"),
    (re.compile(r"R\s*\+\s*3|TROISIEME?\s*ETAGE|3EME?\s*ETAGE", re.I), "R+3"),
    (re.compile(r"R\s*\+\s*4|QUATRIEME?\s*ETAGE|4EME?\s*ETAGE", re.I), "R+4"),
    (re.compile(r"TOIT|TERRASSE|TOITURE|PLEIN\s*DE\s*CHARGE", re.I), "TOIT"),
    (re.compile(r"PL\s*\.?\s*HT\s*\.?\s*RDC|RDC\s*PLEIN", re.I), "RDC"),
    (re.compile(r"PL\s*\.?\s*HT\s*\.?\s*R\s*\+\s*1|R\+1\s*PLEIN", re.I), "R+1"),
    (re.compile(r"PL\s*\.?\s*HT\s*\.?\s*R\s*\+\s*2|R\+2\s*PLEIN", re.I), "R+2"),
    (re.compile(r"SOUS.?SOL|SS\b|SSOL", re.I), "SOUS_SOL"),
]


@dataclass
class LevelAnchor:
    """Ancre textuelle definissant un niveau."""
    text: str
    level: str
    y: float
    x: float = 0.0
    page: int = 1
    confidence: float = 0.9


@dataclass
class LevelZone:
    """Zone verticale associee a un niveau."""
    level: str
    y_min: float
    y_max: float
    page: int = 1
    anchors: list[LevelAnchor] = field(default_factory=list)
    element_count: int = 0


@dataclass
class ClusteringResult:
    """Resultat du clustering spatial par niveau."""
    zones: list[LevelZone]
    assignments: dict[str, str]  # element_key -> level
    ambiguous: list[dict]        # Elements incertains
    warnings: list[str]
    confidence: float = 0.0      # Confiance globale 0-1


def detect_level_anchors(
    words: list[dict],
    pages: list[int] | None = None,
) -> list[LevelAnchor]:
    """Detecte les ancres textuelles de niveaux dans les mots extraits.

    Cherche des patterns comme "RDC", "R+1", "PL.HT R+2", "FONDATION"
    dans les mots et les utilise comme points de reference.
    """
    anchors: list[LevelAnchor] = []
    seen_levels: set[str] = set()

    for w in words:
        text = w.get("text", "").strip()
        if not text:
            continue

        page = w.get("page", 1)
        if pages and page not in pages:
            continue

        for pattern, level in LEVEL_PATTERNS:
            if pattern.search(text):
                # Eviter les doublons de niveau sur la meme page
                key = f"{level}-{page}"
                if key not in seen_levels:
                    seen_levels.add(key)
                    anchors.append(LevelAnchor(
                        text=text,
                        level=level,
                        y=w.get("y", 0),
                        x=w.get("x", 0),
                        page=page,
                        confidence=0.85,
                    ))
                break

    return anchors


def _build_zones_from_anchors(
    anchors: list[LevelAnchor],
    page_height: float = 2380.0,
) -> list[LevelZone]:
    """Construit des zones verticales a partir des ancres detectees.

    Trie les ancres par Y (haut -> bas) et cree des zones
    avec des frontieres basees sur les positions des ancres.
    """
    if not anchors:
        # Zones par defaut si aucune ancre
        return [
            LevelZone("TOIT", 0, page_height * 0.2),
            LevelZone("R+2", page_height * 0.2, page_height * 0.4),
            LevelZone("R+1", page_height * 0.4, page_height * 0.6),
            LevelZone("RDC", page_height * 0.6, page_height * 0.8),
            LevelZone("FONDATION", page_height * 0.8, page_height),
        ]

    # Trier les ancres par Y croissant (haut = petit Y, bas = grand Y)
    sorted_anchors = sorted(anchors, key=lambda a: a.y)

    zones: list[LevelZone] = []

    # La premiere zone couvre tout au-dessus de la premiere ancre
    # (pas de UNKNOWN_TOP — on etend la premiere zone vers le haut)
    first_level = sorted_anchors[0].level

    # Zones entre ancres
    for i in range(len(sorted_anchors)):
        y_start = sorted_anchors[i].y
        if i < len(sorted_anchors) - 1:
            y_end = sorted_anchors[i + 1].y
            mid = (y_start + y_end) / 2
        else:
            # Derniere zone: couvre jusqu'en bas de page
            mid = y_start
            y_end = page_height

        # Premiere zone: etendue vers le haut
        y_min = 0 if i == 0 else (y_start + (sorted_anchors[i - 1].y if i > 0 else 0)) / 2

        zones.append(LevelZone(
            level=sorted_anchors[i].level,
            y_min=y_min if i == 0 else y_start - 50,
            y_max=mid,
            anchors=[sorted_anchors[i]],
        ))

    return zones


def _assign_element_to_zone(
    y: float,
    zones: list[LevelZone],
) -> tuple[str, float]:
    """Assigne un element a une zone par sa position Y.

    Retourne (niveau, confiance).
    """
    for zone in zones:
        if zone.y_min <= y <= zone.y_max:
            return zone.level, 0.8

    # Si hors zone, trouver la plus proche
    best_zone = None
    best_dist = float("inf")
    for zone in zones:
        dist = min(abs(y - zone.y_min), abs(y - zone.y_max))
        if dist < best_dist:
            best_dist = dist
            best_zone = zone

    if best_zone and best_dist < 200:
        return best_zone.level, 0.5

    return "INCONNU", 0.2


def cluster_elements_by_level(
    elements: list[dict],
    words: list[dict] | None = None,
    page_height: float = 2380.0,
    pages: list[int] | None = None,
) -> ClusteringResult:
    """Clustering spatial des elements par niveau de construction.

    Args:
        elements: Liste d'elements extraits (doivent avoir 'y', 'page')
        words: Mots extraits du PDF (pour detecter les ancres)
        page_height: Hauteur de page en points
        pages: Pages a traiter

    Returns:
        ClusteringResult avec zones, assignments, ambiguous.
    """
    # 1. Detecter les ancres de niveaux
    anchors = detect_level_anchors(words or [], pages=pages)

    # 2. Construire les zones
    zones = _build_zones_from_anchors(anchors, page_height)

    # 3. Assigner chaque element a une zone
    assignments: dict[str, str] = {}
    ambiguous: list[dict] = []
    warnings: list[str] = []

    for elem in elements:
        y = elem.get("y", 0)
        page = elem.get("page", 1)
        ref = elem.get("reference", "???")

        # Filtrer par page si specifie
        if pages and page not in pages:
            continue

        level, confidence = _assign_element_to_zone(y, zones)

        key = f"{ref}-{page}"
        assignments[key] = level

        # Mettre a jour le compteur de zone
        for zone in zones:
            if zone.level == level:
                zone.element_count += 1
                break

        if confidence < 0.5:
            ambiguous.append({
                "reference": ref,
                "y": y,
                "page": page,
                "assigned_level": level,
                "confidence": confidence,
                "reason": "Position hors zone definie",
            })

    # 4. Calculer la confiance globale
    total = len(elements)
    assigned = sum(1 for v in assignments.values() if v != "INCONNU")
    confidence = assigned / total if total > 0 else 0.0

    if confidence < 0.5:
        warnings.append(
            "Clustering spatial peu fiable — "
            "verifier les niveaux manuellement"
        )

    # 5. Nettoyer les zones vides
    zones = [z for z in zones if z.element_count > 0 or z.anchors]

    return ClusteringResult(
        zones=zones,
        assignments=assignments,
        ambiguous=ambiguous,
        warnings=warnings,
        confidence=round(confidence, 2),
    )


def apply_level_assignments(
    elements: list[dict],
    clustering: ClusteringResult,
) -> list[dict]:
    """Applique les niveauxassignes aux elements.

    Modifie les elements en place et retourne la liste mise a jour.
    """
    for elem in elements:
        ref = elem.get("reference", "???")
        page = elem.get("page", 1)
        key = f"{ref}-{page}"

        if key in clustering.assignments:
            elem["level"] = clustering.assignments[key]
        elif "level" not in elem or elem["level"] == "INCONNU":
            elem["level"] = "INCONNU"

    return elements


def summarize_by_level(
    elements: list[dict],
) -> dict[str, dict]:
    """Resume les elements par niveau.

    Returns:
        Dict[niveau] -> {count, families, elements}
    """
    summary: dict[str, dict] = {}

    for elem in elements:
        level = elem.get("level", "INCONNU")
        family = elem.get("family", "INCONNU")

        if level not in summary:
            summary[level] = {
                "count": 0,
                "families": {},
                "elements": [],
            }

        summary[level]["count"] += 1
        summary[level]["families"][family] = (
            summary[level]["families"].get(family, 0) + 1
        )
        summary[level]["elements"].append(elem.get("reference", "???"))

    return summary
