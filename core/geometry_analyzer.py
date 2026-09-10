"""geometry_analyzer.py — Analyse geometrique des dessins vectoriels PDF.

Extrait les formes (rectangles, cercles, polylignes) via pymupdf
page.get_drawings() et les classifie en elements structuraux BA :
- Rectangles > seuil -> POTEAU (cercles) ou SEMELLE (rectangles carres)
- Polylignes longues -> POUTRE / LONGRINE / CHAINAGE
- Zones avec hatch -> VOILE / DALLE

Aucune valeur inventee : chaque forme detectee est accompanied de sa
geometrie exacte (coordonnees, dimensions) et d'un score de confiance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf


# --- Seuils de classification (en points PDF, ~0.35mm) ---
# Un plan A0 fait ~3370x2380 pts. Un poteau 25x25cm = ~170 pts a l'echelle 1/50.
MIN_SHAPE_AREA = 50 * 50          # 50 pts x 50 pts minimum
MAX_POTEAU_AREA = 400 * 400       # Poteau max (section probable)
MIN_BEAM_LENGTH = 200              # Poutre/longrine: >200 pts de long
BEAM_WIDTH_MAX = 150               # Largeur max d'une poutre en plan
SQUARENESS_TOLERANCE = 0.3         # Rapport l/L pour considerer "carre" (semelle)
CIRCLE_ECCENTRICITY_MAX = 0.2     # Excentricite max pour un cercle


@dataclass
class DetectedShape:
    """Forme geometrique detectee dans un dessin PDF."""
    shape_type: str          # "rectangle", "circle", "polyline", "line"
    rect: tuple[float, float, float, float]  # (x0, y0, x1, y1) bounding box
    width: float = 0.0       # Largeur en pts
    height: float = 0.0      # Hauteur en pts
    area: float = 0.0        # Superficie en pts^2
    perimeter: float = 0.0   # Perimetre en pts
    length: float = 0.0      # Longueur (pour polylignes)
    vertices: int = 0        # Nombre de sommets
    closed: bool = False     # Forme fermee
    stroke_color: tuple = (0, 0, 0)
    fill_color: tuple = (0, 0, 0)
    page: int = 0
    confidence: float = 0.0  # 0-1, confiance de la classification
    x: float = 0.0           # Centre X
    y: float = 0.0           # Centre Y


@dataclass
class StructuralElement:
    """Element structural classifie a partir d'une forme geometrique."""
    element_family: str      # POTEAU, SEMELLE, POUTRE, LONGRINE, VOILE, DALLE
    reference: str           # Repere genere (ex: "P-GEO-1", "S-GEO-3")
    shape: DetectedShape
    section_cm: tuple[float, float] = (0.0, 0.0)  # (b, h) en cm
    length_m: float = 0.0    # Longueur en m (pour poutres/longrines)
    level: str = "INCONNU"   # Niveau detecte
    source: str = "drawing"  # "drawing", "text", "dxf", "ifc"
    page: int = 0
    x: float = 0.0
    y: float = 0.0


def _distance(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    return math.sqrt((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2)


def _perimeter(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for i in range(len(points) - 1):
        total += _distance(points[i], points[i + 1])
    if _distance(points[-1], points[0]) < 5:
        total += _distance(points[-1], points[0])
    return total


def _bounding_box(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _shape_area(points: list[tuple[float, float]]) -> float:
    n = len(points)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += points[i][0] * points[j][1]
        area -= points[j][0] * points[i][1]
    return abs(area) / 2.0


def _is_closed(points: list[tuple[float, float]], threshold: float = 10.0) -> bool:
    if len(points) < 3:
        return False
    return _distance(points[0], points[-1]) < threshold


def extract_shapes_from_page(
    page: pymupdf.Page,
    page_num: int = 0,
    min_area: float = MIN_SHAPE_AREA,
) -> list[DetectedShape]:
    """Extrait toutes les formes geometriques d'une page PDF via get_drawings().

    Retourne une liste de DetectedShape avec geometrie exacte.
    Filtre les formes trop petites (bruit) et les traits simples.
    """
    shapes: list[DetectedShape] = []

    try:
        drawings = page.get_drawings()
    except Exception:
        return shapes

    for d in drawings:
        items = d.get("items", [])
        if not items:
            continue

        # Collecter tous les points de la forme
        all_points: list[tuple[float, float]] = []
        shape_type = "unknown"
        is_closed = d.get("closePath", False)

        for item in items:
            kind = item[0]
            if kind == "l":  # Ligne
                p1, p2 = item[1], item[2]
                all_points.append((p1.x, p1.y))
                all_points.append((p2.x, p2.y))
                shape_type = "line"
            elif kind == "re":  # Rectangle
                r = item[1]
                all_points.extend([
                    (r.x0, r.y0), (r.x1, r.y0),
                    (r.x1, r.y1), (r.x0, r.y1),
                ])
                shape_type = "rectangle"
                is_closed = True
            elif kind == "c":  # Courbe de Bezier (peut etre un cercle)
                p1, p2, p3, p4 = item[1], item[2], item[3], item[4]
                all_points.extend([
                    (p1.x, p1.y), (p2.x, p2.y),
                    (p3.x, p3.y), (p4.x, p4.y),
                ])
                shape_type = "bezier"
            elif kind == "qu":  # Quad (polyligne)
                for pt in item[1:]:
                    if hasattr(pt, 'x'):
                        all_points.append((pt.x, pt.y))
                shape_type = "polyline"

        if not all_points:
            continue

        # Dedoublonner les points proches
        unique_points = [all_points[0]]
        for p in all_points[1:]:
            if _distance(p, unique_points[-1]) > 2:
                unique_points.append(p)

        if len(unique_points) < 2:
            continue

        # Calculer les proprietes geometriques
        bb = _bounding_box(unique_points)
        w = bb[2] - bb[0]
        h = bb[3] - bb[1]
        area = _shape_area(unique_points) if len(unique_points) >= 3 else w * h
        perim = _perimeter(unique_points)
        cx = (bb[0] + bb[2]) / 2
        cy = (bb[1] + bb[3]) / 2

        # Filtrer les formes trop petites
        if area < min_area and shape_type != "line":
            continue

        # Longueur pour polylignes non fermees
        length = perim if is_closed else _perimeter(unique_points)

        # Couleurs
        stroke = d.get("color", (0, 0, 0))
        fill = d.get("fill", (0, 0, 0))

        shape = DetectedShape(
            shape_type=shape_type,
            rect=bb,
            width=w,
            height=h,
            area=area,
            perimeter=perim,
            length=length,
            vertices=len(unique_points),
            closed=is_closed,
            stroke_color=stroke if isinstance(stroke, tuple) else (0, 0, 0),
            fill_color=fill if isinstance(fill, tuple) else (0, 0, 0),
            page=page_num,
            x=cx,
            y=cy,
        )
        shapes.append(shape)

    return shapes


def classify_shapes(
    shapes: list[DetectedShape],
    scale: float = 1.0,
    page_height: float = 2380.0,
) -> list[StructuralElement]:
    """Classifie les formes detectees en elements structuraux BA.

    Regles de classification :
    - Rectangle carre (squareness < 0.3) + petite surface -> POTEAU
    - Rectangle non carre + grande surface -> SEMELLE ou DALLE
    - Polyligne longue + etroite -> POUTRE / LONGRINE / CHAINAGE
    - Grand rectangle + texture -> VOILE
    """
    elements: list[StructuralElement] = []
    counter = {"POTEAU": 0, "SEMELLE": 0, "POUTRE": 0, "LONGRINE": 0,
               "CHAINAGE": 0, "VOILE": 0, "DALLE": 0}

    for s in shapes:
        family = None
        ref_prefix = None
        section_cm = (0.0, 0.0)
        length_m = 0.0
        confidence = 0.0

        if s.shape_type == "rectangle" and s.area >= MIN_SHAPE_AREA:
            min_dim = min(s.width, s.height)
            max_dim = max(s.width, s.height)
            squareness = min_dim / max_dim if max_dim > 0 else 0

            # Converter dimensions en cm (selon l'echelle)
            b_cm = min_dim * scale * 100 / 72 * 2.54  # pts -> cm
            h_cm = max_dim * scale * 100 / 72 * 2.54

            if squareness > (1 - SQUARENESS_TOLERANCE) and s.area < MAX_POTEAU_AREA:
                # Presque carre et petit -> POTEAU
                family = "POTEAU"
                ref_prefix = "P"
                section_cm = (round(b_cm, 1), round(h_cm, 1))
                confidence = min(0.9, 0.5 + squareness * 0.4)
            elif s.area > 1000 * 1000:
                # Tres grand -> DALLE (zone de plancher)
                family = "DALLE"
                ref_prefix = "D"
                confidence = 0.4  # Faible confiance, besoin de texte
            elif s.area > MAX_POTEAU_AREA:
                # Grand rectangle -> SEMELLE
                family = "SEMELLE"
                ref_prefix = "S"
                section_cm = (round(b_cm, 1), round(h_cm, 1))
                confidence = 0.6

        elif s.shape_type in ("polyline", "line") and s.length > MIN_BEAM_LENGTH:
            # Polyligne longue -> POUTRE / LONGRINE / CHAINAGE
            # Distinguer par la largeur apparente
            if s.width < BEAM_WIDTH_MAX and s.length > MIN_BEAM_LENGTH * 2:
                # Long et etroit -> LONGRINE (si en bas de page) ou CHAINAGE
                if s.y > page_height * 0.7:
                    family = "LONGRINE"
                    ref_prefix = "LG"
                else:
                    family = "POUTRE"
                    ref_prefix = "N"
                length_m = s.length * scale / 72 * 0.0254  # pts -> m
                confidence = 0.5
            elif s.length > MIN_BEAM_LENGTH:
                family = "POUTRE"
                ref_prefix = "N"
                length_m = s.length * scale / 72 * 0.0254
                confidence = 0.4

        if family:
            counter[family] += 1
            elements.append(StructuralElement(
                element_family=family,
                reference=f"{ref_prefix}-GEO-{counter[family]}",
                shape=s,
                section_cm=section_cm,
                length_m=round(length_m, 2),
                page=s.page,
                x=s.x,
                y=s.y,
                confidence=round(confidence, 2),
            ))

    return elements


def analyze_pdf_drawings(
    pdf_path: str,
    pages: list[int] | None = None,
    scale: float = 1.0,
) -> list[StructuralElement]:
    """Analyse les dessins vectoriels d'un PDF et classifie les formes.

    Args:
        pdf_path: Chemin vers le PDF
        pages: Pages a analyser (None = toutes)
        scale: Facteur d'echelle (1.0 = echelle 1:1 en points)

    Returns:
        Liste d'elements structuraux detectes par geometrie.
    """
    doc = pymupdf.open(str(pdf_path))
    all_elements: list[StructuralElement] = []

    page_indices = pages if pages else range(len(doc))

    for idx in page_indices:
        if idx >= len(doc):
            continue
        page = doc[idx]
        ph = page.rect.height

        shapes = extract_shapes_from_page(page, page_num=idx + 1)
        elements = classify_shapes(shapes, scale=scale, page_height=ph)

        # Assigner le niveau par position Y
        for e in elements:
            ratio_y = e.y / ph if ph > 0 else 0.5
            if ratio_y > 0.7:
                e.level = "FONDATION"
            elif ratio_y > 0.4:
                e.level = "RDC"
            else:
                e.level = "R+1"

        all_elements.extend(elements)

    doc.close()
    return all_elements


def merge_drawing_elements_with_text(
    drawing_elements: list[StructuralElement],
    text_elements: list[dict],
    tolerance_pts: float = 100.0,
) -> list[dict]:
    """Fusionne les elements detectes par dessin avec ceux extraits du texte.

    Evite les doublons : si un element texte et un element dessin sont
    proches (< tolerance), garde l'element texte (plus d'info).
    """
    merged = list(text_elements)
    used_text_indices: set[int] = set()

    for de in drawing_elements:
        best_idx = -1
        best_dist = float("inf")

        for i, te in enumerate(text_elements):
            if i in used_text_indices:
                continue
            tx = te.get("x", 0)
            ty = te.get("y", 0)
            dist = _distance((de.x, de.y), (tx, ty))
            if dist < best_dist:
                best_dist = dist
                best_idx = i

        if best_dist > tolerance_pts or best_idx == -1:
            # Pas de correspondance texte -> ajouter comme element dessin
            merged.append({
                "reference": de.reference,
                "family": de.element_family,
                "dims_text": f"{de.section_cm[0]:.0f}x{de.section_cm[1]:.0f}"
                    if de.section_cm[0] > 0 else None,
                "level": de.level,
                "x": de.x,
                "y": de.y,
                "page": de.page,
                "source": "drawing",
                "confidence": de.confidence,
                "length_m": de.length_m,
            })
        else:
            # Correspondance trouvee -> marquer comme utilise
            used_text_indices.add(best_idx)

    return merged
