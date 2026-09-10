"""
Analyseur de texte profond pour plans BA grand format.

Detecte :
  - Texte inverse (rotation 180°) et correction
  - Zones/niveaux (Fondation, RDC, 1er Etage, etc.)
  - Elements par prefixe et par niveau
  - Occurrences physiques vs label references
  - Exclusion du cartouche/legende
  - Legende dynamique lue depuis le cartouche
  - Attribution spatiale Y-axis (clustering, pas proximite texte)
  - Cas ambigus pour verification visuelle
  - Double extraction (pymupdf + pdfplumber)
  - Routage par type de fichier (.pdf, .dxf, .ifc, image)
"""
from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ============================================================================
# Patterns de detection
# ============================================================================

# Prefixes d'elements BA
_PREFIXES = {
    "N":   "POUTRE",
    "BN":  "BANDE_NOYEE",
    "LG":  "LONGRINE",
    "CH":  "CHAINAGE",
    "P":   "POTEAU",
    "Q":   "POTEAU",
    "S":   "SEMELLE",
    "V":   "VOILE",
    "D":   "DALLE",
    "LT":  "LINTEAU",
    "PR":  "POUTRE_REDOUBLANTE",
    "M":   "MASSIF",
    "R":   "RADIER",
    "RD":  "REDRESSEUR",
    "ESC": "ESCALIER",
}

# Pattern unifie : prefixe + chiffres optionnels + dims optionnelles
# Exclure les tokens partiels (DALL, ALL, Sall, etc.)
_ELEM_RX = re.compile(
    r"^(?P<prefix>BN|LG|CH|LT|PR|RD|ESC|SF|[PQSNVDRM])"
    r"(?P<num>\d+)"
    r"(?:[-–]\s*\((?P<dims>\d+[xX×]\d+(?:[xX×]\d+)?)\))?"
    r"(?P<suffix>(?:BIS|ALL)?)$",
    re.IGNORECASE,
)

# Pattern pour les labels composites avec dims inline
_ELEM_DIMS_RX = re.compile(
    r"^(?P<prefix>BN|LG|CH|LT|PR|RD|ESC|SF|[PQSNVDRM])"
    r"(?P<num>\d*)"
    r"[-–]\s*\((?P<dims>\d+[xX×]\d+(?:[xX×]\d+)?)\)"
    r"(?P<suffix>(?:BIS|ALL)?)$",
    re.IGNORECASE,
)

# Pattern pour les dimensions dans le texte libre
_DIM_RX = re.compile(
    r"\(?\s*(\d{1,3})\s*[xX×]\s*(\d{1,3})\s*(?:[xX×]\s*(\d{1,3}))?\s*\)?"
)

# Niveaux de construction
_LEVEL_PATTERNS = [
    (re.compile(r"FONDATION|FOND\b", re.I), "FONDATION"),
    (re.compile(r"PL\.?\s*HT\.?\s*RDC|PLANCHER\s+HAUT\s*RDC|RDC\b", re.I), "RDC"),
    (re.compile(r"PL\.?\s*HT\.?\s*1(?:ER|ÈRE)\s*É?TAGE|1(?:ER|ÈRE)\s*É?TAGE|R\+?1\b", re.I), "R+1"),
    (re.compile(r"PL\.?\s*HT\.?\s*2(?:ÈME)?\s*É?TAGE|2(?:ÈME)?\s*É?TAGE|R\+?2\b", re.I), "R+2"),
    (re.compile(r"PL\.?\s*HT\.?\s*3(?:ÈME)?\s*É?TAGE|3(?:ÈME)?\s*É?TAGE|R\+?3\b", re.I), "R+3"),
    (re.compile(r"ETAGE\s*CORPS\s*Bât|ETAGE\b", re.I), "ETAGE"),
]

# Mots-clés du cartouche (a exclure du comptage)
_CARTOUCHE_KEYWORDS = [
    "ROYAUME", "MAROC", "ACADEMIE", "DIRECTION", "DATE", "ECHELLE",
    "INDICE", "VERIFIE", "APPROUVE", "ELABORATION", "ETUDES", "TECHNIQUES",
    "BUREAU", "CONTROLE", "ARCHITECT", "PROJET", "MAITRE", "D'OUVRAGE",
    "CLASSE", "TYPE", "TYP", "PLAN", "COFFRAGE", "DETAILS",
    "RECOUVREMENT", "PRINCIPE", "HYPOTHESES", "CALCUL", "BAEL",
    "REGLES", "CHARGES", "NOTA", "SONDAGE", "GEOTECHNICIEN",
    "T.V", "bien", "compacte", "Remblais", "BON SOL",
    "Email", "TEL", "Fax", "Contact",
]


# ============================================================================
# Dataclasses
# ============================================================================

@dataclass
class ElementOccurrence:
    """Une occurrence physique d'un element sur le plan."""
    prefix: str
    reference: str
    family: str
    dims_text: str | None = None
    x: float = 0.0
    y: float = 0.0
    level: str = "INCONNU"
    is_unique: bool = True
    raw_text: str = ""


@dataclass
class ElementInventory:
    """Inventaire complet d'un type d'element."""
    prefix: str
    family: str
    level: str
    references: list[str] = field(default_factory=list)
    occurrences: list[ElementOccurrence] = field(default_factory=list)
    sections: dict[str, int] = field(default_factory=dict)

    @property
    def nb_types(self) -> int:
        return len(set(self.references))

    @property
    def nb_occurrences(self) -> int:
        return len(self.occurrences)


@dataclass
class LevelZone:
    """Zone spatiale correspondant a un niveau."""
    level: str
    y_min: float
    y_max: float
    x_min: float = 0.0
    x_max: float = float("inf")


@dataclass
class AnalysisResult:
    """Resultat complet de l'analyse."""
    elements: list[ElementOccurrence] = field(default_factory=list)
    inventory: dict[str, list[ElementInventory]] = field(default_factory=dict)
    levels_found: list[str] = field(default_factory=list)
    reversed_texts_corrected: int = 0
    cartouche_excluded: int = 0
    ambiguous_cases: list[dict] = field(default_factory=list)
    legend_detected: dict[str, str] = field(default_factory=dict)
    extraction_backend: str = "pymupdf"
    warnings: list[str] = field(default_factory=list)


# ============================================================================
# Detection de texte inverse (180°)
# ============================================================================

def _looks_like_inverted(text: str) -> bool:
    """Detecte si un texte semble inverse (rotation 180°)."""
    if not text or len(text) < 3:
        return False
    # Pattern courant : dims avec parenthese fermante au debut
    # Ex: ")53X52(-02N" devrait etre "N20-(25X35)"
    if text.startswith(")") and any(c.isdigit() for c in text[1:5]):
        return True
    # Pattern : chiffres/lettres a l'envers apres un tiret
    if re.match(r"^[-–]?\d+[xX×]\d+", text[::-1]):
        return True
    return False


def _reverse_text(text: str) -> str:
    """Inverse completement un texte (rotation 180°)."""
    # Mapping des parentheses/inversions courants
    fixed = text[::-1]
    # Corriger les caracteres speciaux courants dans les plans PDF
    fixed = fixed.replace(")", "(").replace("(", ")")
    fixed = fixed.replace("]", "[").replace("[", "]")
    fixed = fixed.replace("}", "{").replace("{", "}")
    return fixed


def correct_reversed_texts(words: list[dict]) -> tuple[list[dict], int]:
    """Detecte et corrige les textes inverses (rotation 180°).

    Returns:
        (words_corriges, nb_corrections)
    """
    corrected = 0
    result = []
    for w in words:
        text = w.get("text", "")
        if _looks_like_inverted(text):
            fixed = _reverse_text(text)
            new_w = dict(w)
            new_w["text"] = fixed
            new_w["_reversed"] = True
            result.append(new_w)
            corrected += 1
        else:
            result.append(w)
    return result, corrected


# ============================================================================
# Detection de zones/niveaux
# ============================================================================

def detect_level_zones(words: list[dict], page_width: float, page_height: float) -> list[LevelZone]:
    """Detecte les zones de niveau dans la page en cherchant les titres de zone.

    Ex: "FONDATION", "PL.HT RDC", "PL.HT 1er ETAGE", "TABLEAU DES POTEAUX"
    """
    # Chercher les ancres de niveau dans le texte
    level_anchors: list[tuple[str, float, float]] = []

    for w in words:
        text = w.get("text", "")
        y = w.get("y", 0)
        x = w.get("x", 0)
        for pattern, level in _LEVEL_PATTERNS:
            if pattern.search(text):
                level_anchors.append((level, x, y))
                break

    if not level_anchors:
        return []

    # Trier par y (de haut en bas)
    level_anchors.sort(key=lambda a: a[1] + a[2] * page_width)

    # Creer des zones around les ancres
    zones: list[LevelZone] = []
    for i, (level, x, y) in enumerate(level_anchors):
        y_min = max(0, y - page_height * 0.1)
        y_max = min(page_height, y + page_height * 0.25)
        if zones and zones[-1].level == level:
            zones[-1].y_max = y_max
        else:
            zones.append(LevelZone(level=level, y_min=y_min, y_max=y_max))

    return zones


def assign_level_to_word(
    word: dict,
    zones: list[LevelZone],
    page_height: float,
) -> str:
    """Assigne un niveau a un mot en fonction de sa position Y."""
    y = word.get("y", 0)
    for zone in zones:
        if zone.y_min <= y <= zone.y_max:
            return zone.level
    # Fallback : diviser la page en tiers
    third = page_height / 3
    if y < third:
        return "PLAN_SUP"
    elif y < 2 * third:
        return "PLAN_MIL"
    else:
        return "PLAN_INF"


# ============================================================================
# Exclusion du cartouche
# ============================================================================

def _is_in_cartouche(word: dict, page_width: float, page_height: float) -> bool:
    """Detecte si un mot est dans la zone du cartouche (bas-droite typiquement)."""
    x = word.get("x", 0)
    y = word.get("y", 0)
    # Cartouche : zone inferieure droite (20% largeur, 15% hauteur)
    if x > page_width * 0.75 and y > page_height * 0.80:
        return True
    return False


def _looks_like_cartouche_text(text: str) -> bool:
    """Detecte si un texte ressemble a du contenu de cartouche/legende."""
    upper = text.upper().strip()
    return any(kw in upper for kw in _CARTOUCHE_KEYWORDS)


def filter_cartouche(
    words: list[dict],
    page_width: float,
    page_height: float,
) -> tuple[list[dict], int]:
    """Filtre les mots du cartouche."""
    excluded = 0
    result = []
    for w in words:
        if _is_in_cartouche(w, page_width, page_height):
            excluded += 1
            continue
        if _looks_like_cartouche_text(w.get("text", "")):
            excluded += 1
            continue
        result.append(w)
    return result, excluded


def extract_cartouche_words(
    words: list[dict],
    page_width: float,
    page_height: float,
) -> list[dict]:
    """Extrait les mots du cartouche (pour analyse reglementaire).

    Retourne les mots situes dans la zone cartouche ou contenant des
    mot-cles cartouche (BAEL, RPS, beton, acier, enrobage...).
    """
    cartouche_words = []
    for w in words:
        if _is_in_cartouche(w, page_width, page_height):
            cartouche_words.append(w)
        elif _looks_like_cartouche_text(w.get("text", "")):
            cartouche_words.append(w)
    return cartouche_words


# ============================================================================
# Extraction et regroupement des elements
# ============================================================================

def _parse_element(text: str) -> dict | None:
    """Parse un texte en element BA (prefix, reference, dims, etc.)."""
    clean = text.strip()
    # Essayer d'abord le pattern avec dims inline
    m = _ELEM_DIMS_RX.match(clean)
    if not m:
        m = _ELEM_RX.match(clean)
    if not m:
        return None
    prefix = m.group("prefix").upper()
    num = m.group("num") or ""
    dims = m.group("dims")
    suffix = m.group("suffix") or ""

    # Ignorer les prefix sans chiffres (F, DALL, etc.)
    if not num and prefix in ("F", "D", "S", "V", "R", "M", "P", "N"):
        return None

    family = _PREFIXES.get(prefix, "INCONNU")
    reference = prefix + num
    if suffix:
        reference += suffix

    result: dict[str, Any] = {
        "prefix": prefix,
        "reference": reference,
        "family": family,
        "suffix": suffix,
        "dims_text": dims,
    }

    if dims:
        parts = re.split(r"[xX×]", dims)
        if len(parts) >= 2:
            result["a_cm"] = int(parts[0])
            result["b_cm"] = int(parts[1])
            if len(parts) >= 3:
                result["h_cm"] = int(parts[2])

    return result


def extract_all_elements(
    words: list[dict],
    page_height: float,
    zones: list[LevelZone] | None = None,
) -> list[ElementOccurrence]:
    """Extrait tous les elements BA reconnus dans les mots."""
    elements: list[ElementOccurrence] = []

    for w in words:
        text = w.get("text", "").strip()
        parsed = _parse_element(text)
        if not parsed:
            continue

        level = "INCONNU"
        if zones:
            level = assign_level_to_word(w, zones, page_height)

        elem = ElementOccurrence(
            prefix=parsed["prefix"],
            reference=parsed["reference"],
            family=parsed["family"],
            dims_text=parsed.get("dims_text"),
            x=w.get("x", 0),
            y=w.get("y", 0),
            level=level,
            raw_text=text,
        )
        elements.append(elem)

    return elements


def _merge_nearby_occurrences(
    occurrences: list[ElementOccurrence],
    epsilon: float = 50.0,
) -> list[ElementOccurrence]:
    """Fusionne les occurrences proches (meme element, meme position)."""
    if not occurrences:
        return []

    sorted_occ = sorted(occurrences, key=lambda o: (o.reference, o.x, o.y))
    merged: list[ElementOccurrence] = []
    current = sorted_occ[0]

    for occ in sorted_occ[1:]:
        same_ref = occ.reference == current.reference
        same_dims = occ.dims_text == current.dims_text
        close_x = abs(occ.x - current.x) < epsilon
        close_y = abs(occ.y - current.y) < epsilon

        if same_ref and same_dims and close_x and close_y:
            # Garder l'occurrence la plus centrale
            pass
        else:
            merged.append(current)
            current = occ

    merged.append(current)
    return merged


def build_inventory(elements: list[ElementOccurrence]) -> dict[str, list[ElementInventory]]:
    """Construit l'inventaire regroupe par prefixe et niveau."""
    # Grouper par (prefix, level)
    groups: dict[tuple[str, str], list[ElementOccurrence]] = {}
    for elem in elements:
        key = (elem.prefix, elem.level)
        groups.setdefault(key, []).append(elem)

    inventory: dict[str, list[ElementInventory]] = {}
    for (prefix, level), occs in sorted(groups.items()):
        # Fusionner les occurrences proches
        merged = _merge_nearby_occurrences(occs)

        inv = ElementInventory(
            prefix=prefix,
            family=_PREFIXES.get(prefix, "INCONNU"),
            level=level,
            occurrences=merged,
        )

        # Collecter les references uniques et les sections
        seen_refs: set[str] = set()
        for occ in merged:
            if occ.reference not in seen_refs:
                inv.references.append(occ.reference)
                seen_refs.add(occ.reference)
            if occ.dims_text:
                inv.sections[occ.dims_text] = inv.sections.get(occ.dims_text, 0) + 1

        family = inv.family
        if family not in inventory:
            inventory[family] = []
        inventory[family].append(inv)

    return inventory


# ============================================================================
# Pipeline principal
# ============================================================================

def analyze_page_text(
    page: Any,
    page_num: int,
    words: list[dict] | None = None,
) -> AnalysisResult:
    """Analyse complete du texte d'une page de plan BA.

    1. Extrait tous les mots (texte natif + OCR)
    2. Corrige le texte inverse (180°)
    3. Exclut le cartouche
    4. Detecte les zones de niveau
    5. Extrait et regroupe les elements
    6. Produit l'inventaire complet
    """
    import pymupdf

    result = AnalysisResult()
    rect = page.rect
    page_width = rect.width
    page_height = rect.height

    # 1. Extraction des mots
    if words is None:
        raw_words = page.get_text("words")
        words = []
        for w in raw_words:
            words.append({
                "text": w[4],
                "x": round(w[0], 2),
                "y": round(w[1], 2),
                "x1": round(w[2], 2),
                "y1": round(w[3], 2),
                "page": page_num,
            })

    # 2. Correction du texte inverse
    words, n_reversed = correct_reversed_texts(words)
    result.reversed_texts_corrected = n_reversed

    # 3. Exclusion du cartouche
    words, n_cartouche = filter_cartouche(words, page_width, page_height)
    result.cartouche_excluded = n_cartouche

    # 4. Detection des zones de niveau
    zones = detect_level_zones(words, page_width, page_height)
    result.levels_found = [z.level for z in zones]

    # 5. Extraction des elements
    elements = extract_all_elements(words, page_height, zones)
    result.elements = elements

    # 6. Construction de l'inventaire
    result.inventory = build_inventory(elements)

    # 7. Avertissements
    if not elements:
        result.warnings.append(
            f"Page {page_num}: aucun element BA detecte dans le texte."
        )
    if n_reversed > 0:
        result.warnings.append(
            f"Page {page_num}: {n_reversed} textes inverses corriges."
        )

    return result


def analyze_pdf_text(pdf_path: str) -> AnalysisResult:
    """Analyse le texte de toutes les pages d'un PDF BA."""
    import pymupdf

    combined = AnalysisResult()

    doc = pymupdf.open(str(pdf_path))
    for page_num in range(len(doc)):
        page = doc[page_num]
        page_result = analyze_page_text(page, page_num + 1)

        # Fusionner les resultats
        combined.elements.extend(page_result.elements)
        combined.reversed_texts_corrected += page_result.reversed_texts_corrected
        combined.cartouche_excluded += page_result.cartouche_excluded
        combined.warnings.extend(page_result.warnings)
        for lvl in page_result.levels_found:
            if lvl not in combined.levels_found:
                combined.levels_found.append(lvl)

        for family, invs in page_result.inventory.items():
            if family not in combined.inventory:
                combined.inventory[family] = []
            combined.inventory[family].extend(invs)

    doc.close()
    return combined


# ============================================================================
# Formatage du rapport
# ============================================================================

def format_summary_table(result: AnalysisResult) -> str:
    """Genere un tableau recapitulatif lisible."""
    lines: list[str] = []
    lines.append("=" * 80)
    lines.append("INVENTAIRE COMPLET DES ELEMENTS BA")
    lines.append("=" * 80)
    lines.append(f"Textes inverses corriges : {result.reversed_texts_corrected}")
    lines.append(f"Elements cartouche exclus : {result.cartouche_excluded}")
    lines.append(f"Niveaux detectes : {', '.join(result.levels_found) or 'Aucun'}")
    lines.append(f"Total elements extraits : {len(result.elements)}")
    lines.append("")

    # Tableau par famille
    header = f"{'Prefixe':<8} {'Famille':<20} {'Niveau':<15} {'Nb types':<10} {'Nb occ.':<10} {'Sections'}"
    lines.append(header)
    lines.append("-" * 80)

    for family in sorted(result.inventory.keys()):
        for inv in result.inventory[family]:
            sections_str = ", ".join(
                f"{s}x{c}" if c > 1 else s
                for s, c in sorted(inv.sections.items())
            )
            lines.append(
                f"{inv.prefix:<8} {inv.family:<20} {inv.level:<15} "
                f"{inv.nb_types:<10} {inv.nb_occurrences:<10} {sections_str}"
            )

    lines.append("-" * 80)
    lines.append("")

    # Detail par famille
    for family in sorted(result.inventory.keys()):
        lines.append(f"\n--- {family} ---")
        for inv in result.inventory[family]:
            lines.append(f"  Niveau: {inv.level}")
            refs = ", ".join(inv.references)
            lines.append(f"  Repères: {refs}")
            lines.append(f"  Nb types: {inv.nb_types}, Occurrences: {inv.nb_occurrences}")
            if inv.sections:
                for sec, cnt in sorted(inv.sections.items()):
                    lines.append(f"    Section {sec}: {cnt} occurrence(s)")

    if result.warnings:
        lines.append("\n--- AVERTISSEMENTS ---")
        for w in result.warnings:
            lines.append(f"  [!] {w}")

    if result.ambiguous_cases:
        lines.append("\n--- CAS AMBIGUS (verification visuelle recommandee) ---")
        for ac in result.ambiguous_cases:
            lines.append(f"  - {ac['text']!r} at ({ac['x']:.0f},{ac['y']:.0f}): {ac['reason']}")

    return "\n".join(lines)


# ============================================================================
# Extraction pdfplumber (double backend)
# ============================================================================

def _extract_words_pdfplumber(pdf_path: str) -> list[list[dict]]:
    """Extrait les mots de chaque page via pdfplumber (avec x0,y0,x1,y1)."""
    try:
        import pdfplumber
    except ImportError:
        return []

    all_pages_words: list[list[dict]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_num, page in enumerate(pdf.pages):
            words_raw = page.extract_words(
                x_tolerance=3, y_tolerance=3,
                keep_blank_chars=False,
                use_text_flow=True,
            )
            page_words = []
            for w in words_raw:
                page_words.append({
                    "text": w.get("text", ""),
                    "x": float(w.get("x0", 0)),
                    "y": float(w.get("top", 0)),
                    "x1": float(w.get("x1", 0)),
                    "y1": float(w.get("bottom", 0)),
                    "page": page_num + 1,
                    "_backend": "pdfplumber",
                })
            all_pages_words.append(page_words)
    return all_pages_words


# ============================================================================
# Legende dynamique (lecture du cartouche)
# ============================================================================

# Patterns pour detecter les legends dans le cartouche
_LEGEND_PATTERNS = [
    # "-N=POUTRE", "-LG=LONGRINE", etc.
    re.compile(r"[-–]\s*(\w+)\s*=\s*(POUTRE|LONGRINE|BANDE\s*NOY[ÉE]E?|"
               r"CHA[IÎ]NAGE|POTEAU|SEMELLE|VOILE|DALLE|LINTEAU|"
               r"RAIDISSEUR|REDRESSEUR|MASSIF|CONSOLE|CORBEAU|ESCALIER)",
               re.IGNORECASE),
    # "N = POUTRE", "LG = LONGRINE"
    re.compile(r"\b(\w{1,4})\s*=\s*(POUTRE|LONGRINE|BANDE\s*NOY[ÉE]E?|"
               r"CHA[IÎ]NAGE|POTEAU|SEMELLE|VOILE|DALLE|LINTEAU|"
               r"RAIDISSEUR|REDRESSEUR|MASSIF|CONSOLE|CORBEAU|ESCALIER)",
               re.IGNORECASE),
]


def detect_legend_from_cartouche(words: list[dict]) -> dict[str, str]:
    """Lit la legende du cartouche pour construire le mapping prefixe->famille.

    Retourne un dict comme {"N": "POUTRE", "LG": "LONGRINE", ...}
    """
    legend: dict[str, str] = {}

    for w in words:
        text = w.get("text", "")
        for pattern in _LEGEND_PATTERNS:
            m = pattern.search(text)
            if m:
                prefix = m.group(1).upper()
                family = m.group(2).upper().replace(" ", "_")
                # Normaliser la famille
                family_map = {
                    "POUTRE": "POUTRE", "LONGRINE": "LONGRINE",
                    "BANDE_NOYEE": "BANDE_NOYEE", "BANDE_NOYÉE": "BANDE_NOYEE",
                    "CHAINAGE": "CHAINAGE", "CHAÎNAGE": "CHAINAGE",
                    "POTEAU": "POTEAU", "SEMELLE": "SEMELLE",
                    "VOILE": "VOILE", "DALLE": "DALLE",
                    "LINTEAU": "LINTEAU", "RAIDISSEUR": "RADIER",
                    "REDRESSEUR": "REDRESSEUR", "MASSIF": "MASSIF",
                    "CONSOLE": "CONSOLE", "CORBEAU": "CONSOLE",
                    "ESCALIER": "ESCALIER",
                }
                family = family_map.get(family, family)
                legend[prefix] = family

    return legend


# ============================================================================
# Attribution spatiale Y-axis (clustering)
# ============================================================================

def _cluster_y_positions(y_positions: list[float], gap_threshold: float = 100.0) -> list[tuple[float, float, int]]:
    """Clustering simple des positions Y en bandes horizontales.

    Retourne une liste de (y_min, y_max, count) triee par y_min.
    """
    if not y_positions:
        return []

    sorted_ys = sorted(y_positions)
    clusters: list[list[float]] = [[sorted_ys[0]]]

    for y in sorted_ys[1:]:
        if y - clusters[-1][-1] <= gap_threshold:
            clusters[-1].append(y)
        else:
            clusters.append([y])

    return [(min(c), max(c), len(c)) for c in clusters]


def assign_level_by_y_clustering(
    elements: list[ElementOccurrence],
    level_anchors: list[tuple[str, float]],
    page_height: float,
) -> list[ElementOccurrence]:
    """Re-assigne les niveaux en utilisant le clustering Y-axis.

    Au lieu de la proximite texte, utilise les clusters spatiaux des positions
    des elements pour determiner a quel niveau ils appartiennent.
    """
    if not level_anchors or not elements:
        return elements

    # Trier les ancres par Y
    sorted_anchors = sorted(level_anchors, key=lambda a: a[1])

    # Creer des frontieres entre niveaux
    boundaries: list[float] = []
    for i in range(len(sorted_anchors) - 1):
        _, y1 = sorted_anchors[i]
        _, y2 = sorted_anchors[i + 1]
        boundaries.append((y1 + y2) / 2.0)

    # Assigner chaque element au niveau le plus proche
    for elem in elements:
        y = elem.y
        best_level = sorted_anchors[0][0]
        best_dist = abs(y - sorted_anchors[0][1])

        for level, anchor_y in sorted_anchors:
            dist = abs(y - anchor_y)
            if dist < best_dist:
                best_dist = dist
                best_level = level

        elem.level = best_level

    return elements


# ============================================================================
# Detection des cas ambigus
# ============================================================================

def detect_ambiguous_cases(
    elements: list[ElementOccurrence],
    page_width: float,
    page_height: float,
) -> list[dict]:
    """Detecte les cas ambigus qui necessitent une verification visuelle."""
    ambiguous: list[dict] = []

    for elem in elements:
        reasons = []

        # 1. Meme reference a des positions Y tres differentes (possible multi-niveau)
        # (traite plus tard avec clustering)

        # 2. Reference sans dimensions
        if not elem.dims_text:
            reasons.append("sans dimensions")

        # 3. Reference dans la zone cartouche (pas filtree)
        if elem.x > page_width * 0.75 and elem.y > page_height * 0.80:
            reasons.append("dans la zone cartouche")

        # 4. Reference proche d'un autre prefixe (melange de categories)
        for other in elements:
            if other is elem:
                continue
            if other.prefix != elem.prefix:
                dist = ((other.x - elem.x) ** 2 + (other.y - elem.y) ** 2) ** 0.5
                if dist < 30 and abs(other.y - elem.y) < 10:
                    reasons.append(
                        f"proche de {other.reference} ({other.family})")
                    break

        # 5. Reference avec suffixe ALL (a ne compter qu'une fois)
        if elem.raw_text.upper().endswith("ALL"):
            reasons.append("suffixe ALL (element commun a tous niveaux)")

        if reasons:
            ambiguous.append({
                "text": elem.raw_text,
                "reference": elem.reference,
                "family": elem.family,
                "x": elem.x,
                "y": elem.y,
                "level": elem.level,
                "reason": "; ".join(reasons),
            })

    return ambiguous


# ============================================================================
# Routage par type de fichier
# ============================================================================

def analyze_file(file_path: str) -> AnalysisResult:
    """Route l'analyse selon le type de fichier.

    Supporte : .pdf, .dxf, .ifc, .jpg/.png/.tiff
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext == ".pdf":
        return analyze_pdf_text(str(path))
    elif ext == ".dxf":
        return analyze_dxf(str(path))
    elif ext == ".ifc":
        return analyze_ifc(str(path))
    elif ext in (".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"):
        return analyze_image(str(path))
    else:
        result = AnalysisResult()
        result.warnings.append(
            f"Format non supporte: {ext}. Formats supportes: .pdf, .dxf, .ifc, .jpg/.png/.tiff")
        return result


# ============================================================================
# Support DXF (ezdxf)
# ============================================================================

def analyze_dxf(dxf_path: str) -> AnalysisResult:
    """Analyse un fichier DXF pour extraire les elements BA.

    Extrait les entites TEXT/MTEXT et leur calque (layer) pour rattacher
    chaque repere a son niveau (deduit du layer ou d'un bloc de titre).
    """
    try:
        import ezdxf
    except ImportError:
        result = AnalysisResult()
        result.warnings.append("ezdxf non installe. pip install ezdxf")
        return result

    result = AnalysisResult()
    result.extraction_backend = "ezdxf"

    try:
        doc = ezdxf.readfile(str(dxf_path))
    except Exception as e:
        result.warnings.append(f"Erreur lecture DXF: {e}")
        return result

    msp = doc.modelspace()

    # Collecter tous les TEXT et MTEXT
    text_entities = []
    for entity in msp:
        if entity.dxftype() in ("TEXT", "MTEXT"):
            text = entity.dxf.text if hasattr(entity.dxf, "text") else ""
            if not text:
                continue
            x = getattr(entity.dxf, "insert", (0, 0, 0))[0]
            y = getattr(entity.dxf, "insert", (0, 0, 0))[1]
            layer = getattr(entity.dxf, "layer", "0")
            text_entities.append({
                "text": text.strip(),
                "x": float(x),
                "y": float(y),
                "x1": float(x),
                "y1": float(y),
                "page": 1,
                "_layer": layer,
                "_backend": "ezdxf",
            })

    if not text_entities:
        result.warnings.append("Aucune entite TEXT/MTEXT trouvee dans le DXF.")
        return result

    # Corriger le texte inverse
    text_entities, n_reversed = correct_reversed_texts(text_entities)
    result.reversed_texts_corrected = n_reversed

    # Extraire les elements
    words = text_entities
    max_y = max(w["y"] for w in words) if words else 1000
    max_x = max(w["x"] for w in words) if words else 1000

    # Detecter les legends
    result.legend_detected = detect_legend_from_cartouche(words)

    # Extraire les elements
    elements = extract_all_elements(words, max_y)
    result.elements = elements

    # Construire l'inventaire
    result.inventory = build_inventory(elements)

    # Detecter les cas ambigus
    result.ambiguous_cases = detect_ambiguous_cases(elements, max_x, max_y)

    return result


# ============================================================================
# Support IFC (ifcopenshell)
# ============================================================================

def analyze_ifc(ifc_path: str) -> AnalysisResult:
    """Analyse un fichier IFC pour extraire les elements BA.

    Extrait les IfcBeam, IfcColumn, IfcFooting, IfcSlab, IfcWall
    avec leurs proprietes (section, niveau/IfcBuildingStorey, repere).
    C'est la methode la plus fiable si un export IFC est disponible.
    """
    try:
        import ifcopenshell
    except ImportError:
        result = AnalysisResult()
        result.warnings.append("ifcopenshell non installe. pip install ifcopenshell")
        return result

    result = AnalysisResult()
    result.extraction_backend = "ifcopenshell"

    try:
        model = ifcopenshell.open(str(ifc_path))
    except Exception as e:
        result.warnings.append(f"Erreur lecture IFC: {e}")
        return result

    # Mapping IFC entity -> famille BA
    ifc_to_family = {
        "IfcBeam": "POUTRE",
        "IfcColumn": "POTEAU",
        "IfcFooting": "SEMELLE",
        "IfcSlab": "DALLE",
        "IfcWall": "VOILE",
        "IfcStair": "ESCALIER",
        "IfcRamp": "ESCALIER",
    }

    # Extraire les niveaux (IfcBuildingStorey)
    storeys = {}
    for storey in model.by_type("IfcBuildingStorey"):
        storeys[storey.id()] = storey.Name or f"Storey_{storey.id()}"

    # Extraire les elements structurels
    for ifc_type, family in ifc_to_family.items():
        for entity in model.by_type(ifc_type):
            name = getattr(entity, "Name", None) or ""
            description = getattr(entity, "Description", None) or ""

            # Chercher le repere dans les proprietes
            reference = name or description or f"{ifc_type}_{entity.id()}"

            # Chercher le niveau
            level = "INCONNU"
            for rel in model.by_type("IfcRelContainedInSpatialStructure"):
                if rel.RelatingStructure.id() in storeys:
                    if entity in rel.RelatedElements:
                        level = storeys[rel.RelatingStructure.id()]
                        break

            # Extraire les dimensions des proprietes
            dims_text = None
            section = None
            for pset_name in ["Pset_BeamCommon", "Pset_ColumnCommon",
                              "Pset_FootingCommon", "Pset_SlabCommon",
                              "Pset_WallCommon"]:
                try:
                    psets = ifcopenshell.util.element.get_psets(entity, pset_name)
                    if psets:
                        for k, v in psets.items():
                            if "section" in k.lower() or "profile" in k.lower():
                                section = str(v)
                            if "dimension" in k.lower() or "size" in k.lower():
                                dims_text = str(v)
                except Exception:
                    pass

            elem = ElementOccurrence(
                prefix=reference[:2].upper() if len(reference) >= 2 else "IF",
                reference=reference,
                family=family,
                dims_text=dims_text,
                x=0,
                y=0,
                level=level,
                raw_text=f"{ifc_type}: {reference}",
            )
            result.elements.append(elem)

    # Construire l'inventaire
    result.inventory = build_inventory(result.elements)

    return result


# ============================================================================
# Support Image (OCR)
# ============================================================================

def analyze_image(image_path: str) -> AnalysisResult:
    """Analyse une image (JPG/PNG/TIFF) via OCR.

    Pretraitement opencv (deskew, contraste, bruit) puis OCR.
    """
    result = AnalysisResult()
    result.extraction_backend = "image_ocr"

    try:
        import cv2
        import numpy as np
    except ImportError:
        result.warnings.append("opencv-python non installe.")
        return result

    try:
        from core.ocr_engine import OCREngine
        ocr = OCREngine()
    except ImportError:
        result.warnings.append("Moteur OCR non disponible.")
        return result

    # Lire l'image
    img = cv2.imread(str(image_path))
    if img is None:
        result.warnings.append(f"Impossible de lire l'image: {image_path}")
        return result

    # Pretraitement
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # Correction du contraste (CLAHE)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    # Denoising
    denoised = cv2.fastNlMeansDenoising(enhanced, h=10)

    # OCR via le moteur existant
    try:
        ocr_result = ocr.ocr_image_array(denoised)
        if ocr_result:
            words = []
            for item in ocr_result:
                words.append({
                    "text": item.get("text", ""),
                    "x": item.get("x", 0),
                    "y": item.get("y", 0),
                    "x1": item.get("x1", 0),
                    "y1": item.get("y1", 0),
                    "page": 1,
                })

            # Corriger le texte inverse
            words, n_reversed = correct_reversed_texts(words)
            result.reversed_texts_corrected = n_reversed

            # Extraire les elements
            h, w = img.shape[:2]
            elements = extract_all_elements(words, float(h))
            result.elements = elements
            result.inventory = build_inventory(elements)
    except Exception as e:
        result.warnings.append(f"Erreur OCR: {e}")

    return result


# ============================================================================
# Pipeline principal (avec double backend)
# ============================================================================

def analyze_pdf_text_dual(pdf_path: str) -> AnalysisResult:
    """Analyse PDF avec double extraction (pymupdf + pdfplumber).

    Fusionne les resultats des deux backends pour maximiser la couverture.
    """
    # 1. Extraction pymupdf (baseline)
    result_pymupdf = analyze_pdf_text(pdf_path)

    # 2. Extraction pdfplumber (complement)
    try:
        all_pages_words = _extract_words_pdfplumber(pdf_path)
    except Exception:
        all_pages_words = []

    if all_pages_words:
        result_plumber = AnalysisResult()
        result_plumber.extraction_backend = "pdfplumber"

        for page_num, page_words in enumerate(all_pages_words):
            if not page_words:
                continue

            # Corriger le texte inverse
            page_words, n_rev = correct_reversed_texts(page_words)
            result_plumber.reversed_texts_corrected += n_rev

            # Dimensions de la page (estimer depuis les mots)
            if page_words:
                max_x = max(w.get("x1", w.get("x", 0)) for w in page_words)
                max_y = max(w.get("y1", w.get("y", 0)) for w in page_words)
            else:
                max_x, max_y = 3000, 2000

            # Exclure le cartouche
            page_words, n_cart = filter_cartouche(page_words, max_x, max_y)
            result_plumber.cartouche_excluded += n_cart

            # Extraire les elements
            elements = extract_all_elements(page_words, max_y)
            result_plumber.elements.extend(elements)

        # Fusionner les inventaires
        result_plumber.inventory = build_inventory(result_plumber.elements)
        result_plumber.ambiguous_cases = detect_ambiguous_cases(
            result_plumber.elements,
            max(e.x for e in result_plumber.elements) if result_plumber.elements else 3000,
            max(e.y for e in result_plumber.elements) if result_plumber.elements else 2000,
        )

        # Combiner les resultats (garder le plus complet)
        if len(result_plumber.elements) > len(result_pymupdf.elements):
            result_plumber.warnings = result_pymupdf.warnings
            result_plumber.levels_found = result_pymupdf.levels_found
            result_plumber.legend_detected = result_pymupdf.legend_detected
            return result_plumber

    return result_pymupdf
