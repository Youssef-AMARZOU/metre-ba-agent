"""Generic PNG/PDF adapters for external structural drawing datasets."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from .ingestion import UniversalPlanIngestor
from .schemas import DrawingElement

NormalizedElement = dict[str, Any]

_LABEL = re.compile(
    r"\b(?P<family>POUTRE|BEAM|VOILE|WALL)\s*[-_:]?\s*"
    r"(?P<reference>[A-Z]{0,3}\d+[A-Z]?)\b", re.I
)
_LABEL_REVERSED = re.compile(
    r"\b(?P<reference>[A-Z]{0,3}\d+[A-Z]?)\s*[-_:]?\s*"
    r"(?P<family>POUTRE|BEAM|VOILE|WALL)\b", re.I
)
_DIMENSIONS = re.compile(
    r"(?P<a>\d+(?:[.,]\d+)?)\s*[xX*]\s*"
    r"(?P<b>\d+(?:[.,]\d+)?)(?:\s*[xX*]\s*(?P<h>\d+(?:[.,]\d+)?))?"
)
_BAR = re.compile(r"(?P<count>\d+)\s*(?:HA|T|TOR)\s*(?P<diameter>\d+)", re.I)
_AXIS = re.compile(r"\b(?:axe|axis)\s*[:=-]?\s*([A-Z0-9-]+)", re.I)
_GRID = re.compile(r"\b(?:file|grid)\s*[:=-]?\s*([A-Z0-9-]+)", re.I)


def _number(value: str) -> float:
    value = float(value.replace(",", "."))
    # Dataset drawings commonly use millimetres for sections.
    return value / 1000 if value > 10 else value


def drawing_elements_from_file(path: str | Path) -> list[DrawingElement]:
    """Extract provenance-carrying elements from a PDF or raster image."""
    result = UniversalPlanIngestor(str(path)).ingest()
    elements: list[DrawingElement] = []
    for raw in result.drawing_elements:
        elements.append(DrawingElement.model_validate(raw))
    if not elements and result.images:
        elements.append(DrawingElement(
            id=f"{Path(path).stem}-raster",
            source=result.source.value,
            confidence=0.0,
            provenance={"file": str(path), "pages": result.pages},
            warnings=["raster content requires OCR or supplied annotations"],
        ))
    return elements


def normalize_drawing_elements(
    elements: Iterable[DrawingElement],
    *,
    source: str,
    project: str,
    dataset_id: str,
    family_hint: str | None = None,
) -> list[NormalizedElement]:
    """Map generic drawing text to the repository's normalized contract."""
    normalized: list[NormalizedElement] = []
    for element in elements:
        match = _LABEL.search(element.text) or _LABEL_REVERSED.search(element.text)
        if match:
            family = "POUTRE" if match.group("family").upper() in {"POUTRE", "BEAM"} else "VOILE"
            reference = match.group("reference").upper()
        elif family_hint in {"POUTRE", "VOILE"}:
            family = family_hint
            reference = "INCONNU"
        else:
            continue
        dimensions_match = _DIMENSIONS.search(element.text)
        dimensions = {}
        warnings = list(element.warnings)
        if dimensions_match:
            dimensions = {
                key: _number(value)
                for key, value in dimensions_match.groupdict().items()
                if value is not None
            }
        else:
            warnings.append("dimensions absentes de l'annotation")
        reinforcement = [
            {
                "role": "longitudinal" if family == "POUTRE" else "vertical",
                "count": int(bar.group("count")),
                "diameter_mm": int(bar.group("diameter")),
                "unit": "mm",
            }
            for bar in _BAR.finditer(element.text)
        ]
        if not reinforcement:
            warnings.append("armatures absentes de l'annotation")
        axis = _AXIS.search(element.text)
        grid = _GRID.search(element.text)
        normalized.append({
            "id": element.id,
            "reference": reference,
            "family": family,
            "axis": axis.group(1) if axis else "",
            "grid": grid.group(1) if grid else "",
            "dimensions_m": dimensions,
            "section_m": dimensions,
            "reinforcement": reinforcement,
            "quantity": {"count": 1, "unit": "u"},
            "source": {
                "dataset": dataset_id,
                "source": source,
                "project": project,
                "page": element.page,
                "provenance": element.provenance,
            },
            "bbox": element.bbox,
            "confidence": "high" if dimensions_match else "low",
            "warnings": warnings,
        })
    return normalized


def adapt_file(
    path: str | Path,
    *,
    source: str,
    project: str,
    dataset_id: str,
    family_hint: str | None = None,
) -> list[NormalizedElement]:
    return normalize_drawing_elements(
        drawing_elements_from_file(path),
        source=source,
        project=project,
        dataset_id=dataset_id,
        family_hint=family_hint,
    )
