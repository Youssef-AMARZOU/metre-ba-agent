"""dxf_extractor.py — Extraction d'elements structuraux depuis des fichiers DXF.

Utilise ezdxf pour extraire :
- Entites TEXT/MTEXT avec leur calque (layer) et position
- Entites LWPOLYLINE pour la geometrie (longueur, forme)
- Entites INSERT (blocs) pour les symboles de poteaux/semelles
- Entites DIMENSION pour les cotes
- Calques pour rattacher les elements a leur niveau

Ne suppose jamais de legende fixe : lit les calques du DXF pour
construire le dictionnaire prefixe->type d'element.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import ezdxf
    from ezdxf.math import Vec2, Vec3
    HAS_EZDXF = True
except ImportError:
    HAS_EZDXF = False


# Patterns de classification des calques DXF
LAYER_PATTERNS = {
    "poteaux": re.compile(r"(POTEAU|COLONNE|COLUMN|P\d|Q\d)", re.I),
    "poutres": re.compile(r"(POUTRE|BEAM|POUTRE|N\d|BN\d|LG\d)", re.I),
    "semelles": re.compile(r"(SEMELLE|FOOTING|S\d|SF\d)", re.I),
    "longrines": re.compile(r"(LONGRINE|GRADE.?BEAM|LG\d)", re.I),
    "chainages": re.compile(r"(CHAINAGE|TIE.?BEAM|CH\d)", re.I),
    "voiles": re.compile(r"(VOILE|WALL|SHEAR|V\d)", re.I),
    "dalle": re.compile(r"(DALLE|SLAB|D\d)", re.I),
    "ferraillage": re.compile(r"(FERR|REBAR|ARMATURE|ACIER)", re.I),
    "cotes": re.compile(r"(COTE|DIM|DIMENSION)", re.I),
    "axes": re.compile(r"(AXE|AXIS|GRID)", re.I),
    "niveaux": re.compile(r"(NIVEAU|LEVEL|ETAGE|FONDATION|RDC|R\+|R\d|REZ)", re.I),
}

# Patterns de detection de repere dans le texte
REPERE_PATTERNS = [
    re.compile(r"^(P|Q)\s*(\d+[a-z]?)$", re.I),           # P1, Q2a
    re.compile(r"^(N|BN|PN)\s*(\d+[a-z]?)$", re.I),       # N1, BN2
    re.compile(r"^(S|SF)\s*(\d+[a-z]?)$", re.I),           # S1, SF2
    re.compile(r"^(LG|CH)\s*(\d+[a-z]?)$", re.I),          # LG-1, CH-2
    re.compile(r"^(V|VD)\s*(\d+[a-z]?)$", re.I),           # V1, VD2
    re.compile(r"^(D|DS)\s*(\d+[a-z]?)$", re.I),           # D1, DS2
    re.compile(r"^(ESC)\s*(\d+[a-z]?)$", re.I),            # ESC1
    re.compile(r"^(M)\s*(\d+[a-z]?)$", re.I),              # M1
    re.compile(r"^(R|RD)\s*(\d+[a-z]?)$", re.I),           # R1, RD2
]


@dataclass
class DXFElement:
    """Element extrait d'un fichier DXF."""
    reference: str
    family: str
    layer: str
    x: float = 0.0
    y: float = 0.0
    page: int = 1
    dims_text: str | None = None
    level: str = "INCONNU"
    length_m: float = 0.0
    block_name: str | None = None
    entity_type: str = "TEXT"
    confidence: float = 0.5


@dataclass
class DXFExtractionResult:
    """Resultat complet de l'extraction DXF."""
    elements: list[DXFElement]
    layers: list[str]
    layer_mapping: dict[str, str]  # layer -> family
    blocks: list[str]
    warnings: list[str]
    total_entities: int = 0
    text_entities: int = 0
    polyline_entities: int = 0
    insert_entities: int = 0
    dimension_entities: int = 0


def _classify_layer(layer_name: str) -> str | None:
    """Classe un calque DXF en type d'element structural."""
    for family, pattern in LAYER_PATTERNS.items():
        if pattern.search(layer_name):
            return family
    return None


def _extract_repere_from_text(text: str) -> tuple[str, str] | None:
    """Extrait un repere et sa famille depuis un texte DXF."""
    text = text.strip()
    for pattern in REPERE_PATTERNS:
        m = pattern.match(text)
        if m:
            prefix = m.group(1).upper()
            num = m.group(2)
            family_map = {
                "P": "POTEAU", "Q": "POTEAU",
                "N": "POUTRE", "BN": "POUTRE", "PN": "POUTRE",
                "S": "SEMELLE", "SF": "SEMELLE",
                "LG": "LONGRINE", "CH": "CHAINAGE",
                "V": "VOILE", "VD": "VOILE",
                "D": "DALLE", "DS": "DALLE",
                "ESC": "ESCALIER",
                "M": "MUR",
                "R": "RADIER", "RD": "REDRESSEUR",
            }
            family = family_map.get(prefix, "INCONNU")
            return f"{prefix}{num}", family
    return None


def _extract_dimensions_from_text(text: str) -> str | None:
    """Extrait les dimensions d'un texte (ex: "25X40", "90x90x25")."""
    # Pattern: NxM ou NxMxP (en cm)
    m = re.search(r"(\d+)\s*[xX]\s*(\d+)(?:\s*[xX]\s*(\d+))?", text)
    if m:
        if m.group(3):
            return f"{m.group(1)}x{m.group(2)}x{m.group(3)}"
        return f"{m.group(1)}x{m.group(2)}"
    return None


def _detect_level_from_layer(layer: str, layers_above: list[str]) -> str:
    """Detecte le niveau a partir du calque ou des contexte."""
    layer_upper = layer.upper()
    level_patterns = [
        (r"FONDATION|FOND", "FONDATION"),
        (r"RDC|REZ.?DE.?CHAUSS", "RDC"),
        (r"R\+?\s*1|PREMIER.?ETAGE", "R+1"),
        (r"R\+?\s*2|DEUXIEME.?ETAGE", "R+2"),
        (r"R\+?\s*3|TROISIEME.?ETAGE", "R+3"),
        (r"TOIT|TERRASSE|TOITURE", "TOIT"),
    ]
    for pattern, level in level_patterns:
        if re.search(pattern, layer_upper):
            return level
    return "INCONNU"


def extract_from_dxf(
    dxf_path: str | Path,
    *,
    include_geometry: bool = True,
    include_text: bool = True,
    include_blocks: bool = True,
    include_dimensions: bool = True,
) -> DXFExtractionResult:
    """Extrait tous les elements structuraux d'un fichier DXF.

    Args:
        dxf_path: Chemin vers le fichier .dxf
        include_geometry: Extraire les polylignes (longueur)
        include_text: Extraire les entites TEXT/MTEXT
        include_blocks: Extraire les blocs INSERT
        include_dimensions: Extraire les cotes DIMENSION

    Returns:
        DXFExtractionResult avec elements, calques, blocs, warnings.
    """
    if not HAS_EZDXF:
        return DXFExtractionResult(
            elements=[], layers=[], layer_mapping={}, blocks=[],
            warnings=["ezdxf non installe — extraction DXF impossible"],
        )

    dxf_path = Path(dxf_path)
    if not dxf_path.exists():
        return DXFExtractionResult(
            elements=[], layers=[], layer_mapping={}, blocks=[],
            warnings=[f"Fichier introuvable: {dxf_path}"],
        )

    try:
        doc = ezdxf.readfile(str(dxf_path))
    except Exception as e:
        return DXFExtractionResult(
            elements=[], layers=[], layer_mapping={}, blocks=[],
            warnings=[f"Erreur lecture DXF: {e}"],
        )

    msp = doc.modelspace()
    elements: list[DXFElement] = []
    warnings: list[str] = []
    layer_set: set[str] = set()
    block_names: list[str] = []

    total = 0
    text_count = 0
    poly_count = 0
    insert_count = 0
    dim_count = 0

    # 1. Collecter tous les calques et les classifier
    layer_mapping: dict[str, str] = {}
    for layer in doc.layers:
        layer_name = layer.dxf.name
        layer_set.add(layer_name)
        family = _classify_layer(layer_name)
        if family:
            layer_mapping[layer_name] = family

    # 2. Extraire les blocs (INSERT)
    if include_blocks:
        for block in doc.blocks:
            if block.name.startswith("*"):  # Blocs anonymes
                continue
            block_names.append(block.name)
            # Classifier le bloc
            family = _classify_layer(block.name)
            if family:
                for insert in msp.query("INSERT"):
                    if insert.dxf.name == block.name:
                        ins_point = insert.dxf.insert
                        ref = _extract_repere_from_text(block.name)
                        elements.append(DXFElement(
                            reference=ref[0] if ref else f"BLOC-{block.name}",
                            family=ref[1] if ref else family.upper(),
                            layer=insert.dxf.layer,
                            x=ins_point.x,
                            y=ins_point.y,
                            block_name=block.name,
                            entity_type="INSERT",
                            confidence=0.6,
                        ))
                        insert_count += 1

    # 3. Extraire les entites texte
    if include_text:
        for entity in msp.query("TEXT MTEXT"):
            text = entity.dxf.text if hasattr(entity.dxf, 'text') else ""
            if not text or len(text.strip()) < 2:
                continue

            layer = entity.dxf.layer
            pos = entity.dxf.insert if hasattr(entity.dxf, 'insert') else (0, 0, 0)
            x = pos[0] if hasattr(pos, '__getitem__') else 0
            y = pos[1] if hasattr(pos, '__getitem__') else 0

            # Extraire repere
            ref_info = _extract_repere_from_text(text.strip())
            if ref_info:
                ref, family = ref_info
                level = _detect_level_from_layer(layer, list(layer_set))
                dims = _extract_dimensions_from_text(text)

                elements.append(DXFElement(
                    reference=ref,
                    family=family,
                    layer=layer,
                    x=x, y=y,
                    dims_text=dims,
                    level=level,
                    entity_type="TEXT",
                    confidence=0.7,
                ))
                text_count += 1
            elif layer_mapping.get(layer) in ("cotes", "axes", "niveaux"):
                # Texte sur calque de cote/axe/niveau -> contexte
                pass

    # 4. Extraire les polylignes (geometrie)
    if include_geometry:
        for entity in msp.query("LWPOLYLINE POLYLINE LINE"):
            layer = entity.dxf.layer
            family_from_layer = layer_mapping.get(layer)

            if entity.dxftype() in ("LWPOLYLINE", "POLYLINE"):
                try:
                    points = list(entity.get_points(format="xy"))
                except Exception:
                    continue

                if len(points) < 2:
                    continue

                # Calculer la longueur
                length = 0.0
                for i in range(len(points) - 1):
                    dx = points[i + 1][0] - points[i][0]
                    dy = points[i + 1][1] - points[i][1]
                    length += (dx ** 2 + dy ** 2) ** 0.5

                # Bounding box
                xs = [p[0] for p in points]
                ys = [p[1] for p in points]
                cx = (min(xs) + max(xs)) / 2
                cy = (min(ys) + max(ys)) / 2
                w = max(xs) - min(xs)
                h = max(ys) - min(ys)

                if family_from_layer and family_from_layer not in ("cotes", "axes", "niveaux", "ferraillage"):
                    elements.append(DXFElement(
                        reference=f"GEO-{poly_count + 1}",
                        family=family_from_layer.upper(),
                        layer=layer,
                        x=cx, y=cy,
                        length_m=round(length * 0.001, 2),  # Assumption: mm -> m
                        entity_type="LWPOLYLINE",
                        confidence=0.4,
                    ))
                    poly_count += 1

            elif entity.dxftype() == "LINE":
                try:
                    start = entity.dxf.start
                    end = entity.dxf.end
                    length = ((end.x - start.x) ** 2 + (end.y - start.y) ** 2) ** 0.5
                    cx = (start.x + end.x) / 2
                    cy = (start.y + end.y) / 2

                    if family_from_layer and family_from_layer not in ("cotes", "axes", "niveaux", "ferraillage"):
                        if length > 500:  # Seulement les lignes significatives
                            elements.append(DXFElement(
                                reference=f"GEO-{poly_count + 1}",
                                family=family_from_layer.upper(),
                                layer=layer,
                                x=cx, y=cy,
                                length_m=round(length * 0.001, 2),
                                entity_type="LINE",
                                confidence=0.3,
                            ))
                            poly_count += 1
                except Exception:
                    continue

    # 5. Extraire les dimensions
    if include_dimensions:
        for entity in msp.query("DIMENSION"):
            layer = entity.dxf.layer
            try:
                text = entity.dxf.text if hasattr(entity.dxf, 'text') else ""
                if text:
                    dims = _extract_dimensions_from_text(text)
                    if dims:
                        dim_count += 1
            except Exception:
                continue

    total = text_count + poly_count + insert_count + dim_count

    return DXFExtractionResult(
        elements=elements,
        layers=sorted(layer_set),
        layer_mapping=layer_mapping,
        blocks=block_names,
        warnings=warnings,
        total_entities=total,
        text_entities=text_count,
        polyline_entities=poly_count,
        insert_entities=insert_count,
        dimension_entities=dim_count,
    )


def dxf_elements_to_standard(
    result: DXFExtractionResult,
) -> list[dict]:
    """Convertit les elements DXF au format standard du pipeline.

    Retourne une liste de dictionnaires compatibles avec
    VectorPlanExtractor.extract_from_text_blocks().
    """
    standard = []
    for e in result.elements:
        standard.append({
            "reference": e.reference,
            "family": e.family,
            "dims_text": e.dims_text,
            "level": e.level,
            "x": e.x,
            "y": e.y,
            "page": e.page,
            "source": "dxf",
            "confidence": e.confidence,
            "length_m": e.length_m,
            "layer": e.layer,
            "entity_type": e.entity_type,
        })
    return standard
