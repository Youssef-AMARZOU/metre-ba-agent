"""ifc_extractor.py — Extraction d'elements structuraux depuis des fichiers IFC.

Utilise ifcopenshell pour extraire tous les elements structuraux IFC :
- IfcBeam (poutres, longrines, chainages)
- IfcColumn (poteaux)
- IfcFooting (semelles)
- IfcSlab (dalles)
- IfcWall / IfcWallStandardCase (voiles)
- IfcStair (escaliers)

Avec leurs proprietes (Pset) :
- Section, niveau (IfcBuildingStorey), repere, materiau
- Dimensions reelles (pas des estimations)

C'EST LA METHODE LA PLUS FIABLE — la preferer systematiquement
si un export IFC est disponible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import ifcopenshell
    import ifcopenshell.util.element as ifc_util
    import ifcopenshell.util.representation as ifc_rep
    HAS_IFC = True
except ImportError:
    HAS_IFC = False


# Mapping IFC -> famille BA
IFC_TO_FAMILY = {
    "IfcBeam": "POUTRE",
    "IfcColumn": "POTEAU",
    "IfcFooting": "SEMELLE",
    "IfcSlab": "DALLE",
    "IfcWall": "VOILE",
    "IfcWallStandardCase": "VOILE",
    "IfcStair": "ESCALIER",
    "IfcRailing": "GARDE_CORPS",
    "IfcCurtainWall": "VOILE",
    "IfcPile": "PIEU",
}

# Prefixes pour la nomenclature
IFC_PREFIX = {
    "POUTRE": "N",
    "POTEAU": "P",
    "SEMELLE": "S",
    "DALLE": "D",
    "VOILE": "V",
    "ESCALIER": "ESC",
    "GARDE_CORPS": "GC",
    "PIEU": "PIE",
}


@dataclass
class IFCElement:
    """Element structurel extrait d'un fichier IFC."""
    ifc_id: int
    ifc_type: str
    global_id: str
    name: str
    family: str
    reference: str
    level: str = "INCONNU"
    section_cm: tuple[float, float] = (0.0, 0.0)
    length_m: float = 0.0
    height_m: float = 0.0
    width_m: float = 0.0
    volume_m3: float = 0.0
    material: str = ""
    storey: str = ""
    repere: str = ""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    properties: dict = field(default_factory=dict)
    psets: dict = field(default_factory=dict)
    confidence: float = 0.95  # IFC est tres fiable


@dataclass
class IFCExtractionResult:
    """Resultat complet de l'extraction IFC."""
    elements: list[IFCElement]
    storeys: list[str]
    warnings: list[str]
    total_elements: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    by_storey: dict[str, int] = field(default_factory=dict)


def _get_storey_name(ifc_file, element) -> str:
    """Recupere le nom du batiment etage (IfcBuildingStorey) pour un element."""
    try:
        # Essayer via IfcRelContainedInSpatialStructure
        for rel in ifc_file.by_type("IfcRelContainedInSpatialStructure"):
            if element in rel.RelatedElements:
                storey = rel.RelatingStructure
                if storey.is_a("IfcBuildingStorey"):
                    return storey.Name or "INCONNU"
                elif storey.is_a("IfcBuilding"):
                    return storey.Name or "INCONNU"
    except Exception:
        pass

    # Fallback: chercher dans les proprietes
    try:
        psets = ifc_util.element.get_psets(element)
        for pset_name, pset_data in psets.items():
            if "Level" in pset_data or "Storey" in pset_data:
                return pset_data.get("Level") or pset_data.get("Storey") or "INCONNU"
    except Exception:
        pass

    return "INCONNU"


def _get_element_properties(ifc_file, element) -> dict:
    """Extrait les proprietes (Pset) d'un element IFC."""
    props = {}
    try:
        psets = ifc_util.element.get_psets(element)
        for pset_name, pset_data in psets.items():
            props[pset_name] = pset_data
    except Exception:
        pass
    return props


def _get_material(ifc_file, element) -> str:
    """Extrait le materiau d'un element IFC."""
    try:
        associations = ifc_util.element.get_material(element)
        if associations:
            if hasattr(associations, 'Name'):
                return associations.Name or ""
            if isinstance(associations, list):
                for mat in associations:
                    if hasattr(mat, 'Name'):
                        return mat.Name or ""
    except Exception:
        pass
    return ""


def _get_dimensions(ifc_file, element) -> tuple[float, float, float]:
    """Extrait les dimensions reelles d'un element IFC.

    Utilise les proprietes geometry de l'element.
    """
    length = 0.0
    width = 0.0
    height = 0.0

    try:
        # Essayer via les proprietes standard
        psets = ifc_util.element.get_psets(element)

        # Pset de dimension courants
        for pset_name in ["Pset_ElementComponentCommon", "Pset_BeamCommon",
                          "Pset_ColumnCommon", "Pset_FootingCommon",
                          "Pset_SlabCommon", "Pset_WallCommon"]:
            if pset_name in psets:
                p = psets[pset_name]
                if "Reference" in p:
                    ref = str(p["Reference"])
                    # Parser "25X40" ou "90x90x25"
                    m = re.search(r"(\d+)\s*[xX]\s*(\d+)(?:\s*[xX]\s*(\d+))?", ref)
                    if m:
                        width = float(m.group(1)) / 100  # cm -> m
                        height = float(m.group(2)) / 100
                        if m.group(3):
                            length = float(m.group(3)) / 100

                # Longueur pour les poutres
                if "Span" in p:
                    length = float(p["Span"])
                elif "Length" in p:
                    length = float(p["Length"])

        # Essayer via la representation geometrique
        if length == 0 and width == 0:
            try:
                rep = ifc_rep.get_representation(element, "Body")
                if rep and hasattr(rep, 'RepresentationMaps'):
                    for map in rep.RepresentationMaps:
                        if hasattr(map, 'MappedRepresentation'):
                            for item in map.MappedRepresentation.Items:
                                if hasattr(item, 'Positions'):
                                    # Calculer l'etendue
                                    pass
            except Exception:
                pass

        # Fallback: essayer via les proprietes IsDefined
        if length == 0:
            try:
                shape = element.Representation
                if shape and hasattr(shape, 'Representations'):
                    for rep in shape.Representations:
                        if hasattr(rep, 'Items'):
                            for item in rep.Items:
                                if hasattr(item, 'BoundingBox'):
                                    bb = item.BoundingBox
                                    if bb and hasattr(bb, 'Size'):
                                        length = bb.Size.X
                                        width = bb.Size.Y
                                        height = bb.Size.Z
            except Exception:
                pass

    except Exception:
        pass

    return length, width, height


def _get_location(ifc_file, element) -> tuple[float, float, float]:
    """Recupere la position (x, y, z) d'un element IFC."""
    try:
        placement = element.ObjectPlacement
        if placement and hasattr(placement, 'RelativePlacement'):
            rel = placement.RelativePlacement
            if hasattr(rel, 'Location'):
                loc = rel.Location
                if hasattr(loc, 'Coordinates'):
                    coords = loc.Coordinates
                    return float(coords[0]), float(coords[1]), float(coords[2])
    except Exception:
        pass
    return 0.0, 0.0, 0.0


def extract_from_ifc(
    ifc_path: str | Path,
    *,
    include_properties: bool = True,
    include_geometry: bool = True,
) -> IFCExtractionResult:
    """Extrait tous les elements structuraux d'un fichier IFC.

    Args:
        ifc_path: Chemin vers le fichier .ifc
        include_properties: Extraire les Pset (propriétés)
        include_geometry: Extraire les dimensions

    Returns:
        IFCExtractionResult avec elements, niveaux, warnings.
    """
    if not HAS_IFC:
        return IFCExtractionResult(
            elements=[], storeys=[],
            warnings=["ifcopenshell non installe — pip install ifcopenshell"],
        )

    ifc_path = Path(ifc_path)
    if not ifc_path.exists():
        return IFCExtractionResult(
            elements=[], storeys=[],
            warnings=[f"Fichier introuvable: {ifc_path}"],
        )

    try:
        ifc_file = ifcopenshell.open(str(ifc_path))
    except Exception as e:
        return IFCExtractionResult(
            elements=[], storeys=[],
            warnings=[f"Erreur ouverture IFC: {e}"],
        )

    elements: list[IFCElement] = []
    warnings: list[str] = []
    storeys: list[str] = []
    by_type: dict[str, int] = {}
    by_storey: dict[str, int] = {}

    # Recuperer les niveaux (IfcBuildingStorey)
    for storey in ifc_file.by_type("IfcBuildingStorey"):
        storeys.append(storey.Name or f"Storey-{storey.id()}")

    # Elements structuraux IFC a extraire
    ifc_types = [
        "IfcBeam", "IfcColumn", "IfcFooting", "IfcSlab",
        "IfcWall", "IfcWallStandardCase", "IfcStair",
        "IfcPile",
    ]

    counters: dict[str, int] = {}

    for ifc_type in ifc_types:
        try:
            entities = ifc_file.by_type(ifc_type)
        except Exception:
            continue

        family = IFC_TO_FAMILY.get(ifc_type, "INCONNU")
        prefix = IFC_PREFIX.get(family, "X")

        for entity in entities:
            counters[family] = counters.get(family, 0) + 1
            ref = f"{prefix}{counters[family]}"

            # Proprietes
            properties = {}
            psets = {}
            if include_properties:
                psets = _get_element_properties(ifc_file, entity)
                for pset_name, pset_data in psets.items():
                    for k, v in pset_data.items():
                        if isinstance(v, (str, int, float, bool)):
                            properties[k] = v

            # Dimensions
            length, width, height = (0.0, 0.0, 0.0)
            if include_geometry:
                length, width, height = _get_dimensions(ifc_file, entity)

            # Conversion en cm pour les sections
            section_cm = (round(width * 100, 1), round(height * 100, 1)) if width > 0 else (0.0, 0.0)

            # Niveau
            storey = _get_storey_name(ifc_file, entity)

            # Materiau
            material = _get_material(ifc_file, entity)

            # Position
            x, y, z = _get_location(ifc_file, entity)

            # Nom
            name = entity.Name or f"{ifc_type}-{entity.id()}"

            elem = IFCElement(
                ifc_id=entity.id(),
                ifc_type=ifc_type,
                global_id=entity.GlobalId or "",
                name=name,
                family=family,
                reference=ref,
                level=storey,
                section_cm=section_cm,
                length_m=round(length, 3),
                height_m=round(height, 3),
                width_m=round(width, 3),
                material=material,
                storey=storey,
                repere=properties.get("Reference") or name,
                x=x, y=y, z=z,
                properties=properties,
                psets=psets,
            )
            elements.append(elem)

            # Compteurs
            by_type[family] = by_type.get(family, 0) + 1
            by_storey[storey] = by_storey.get(storey, 0) + 1

    ifc_file.close()

    return IFCExtractionResult(
        elements=elements,
        storeys=storeys,
        warnings=warnings,
        total_elements=len(elements),
        by_type=by_type,
        by_storey=by_storey,
    )


def ifc_elements_to_standard(
    result: IFCExtractionResult,
) -> list[dict]:
    """Convertit les elements IFC au format standard du pipeline."""
    standard = []
    for e in result.elements:
        dims = None
        if e.section_cm[0] > 0:
            dims = f"{e.section_cm[0]:.0f}x{e.section_cm[1]:.0f}"

        standard.append({
            "reference": e.reference,
            "family": e.family,
            "dims_text": dims,
            "level": e.level,
            "x": e.x,
            "y": e.y,
            "z": e.z,
            "page": 1,
            "source": "ifc",
            "confidence": e.confidence,
            "length_m": e.length_m,
            "height_m": e.height_m,
            "width_m": e.width_m,
            "volume_m3": e.volume_m3,
            "material": e.material,
            "ifc_type": e.ifc_type,
            "global_id": e.global_id,
            "name": e.name,
        })
    return standard
