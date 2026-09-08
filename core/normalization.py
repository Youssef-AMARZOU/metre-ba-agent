"""Normalisation commune des extractions BA avant génération des livrables."""
from __future__ import annotations

from typing import Any, TypedDict


class NormalizedElement(TypedDict):
    reference: str
    family: str
    dimensions_m: dict
    reinforcement: list[dict]
    quantity: dict
    source: dict
    bbox: tuple | None
    confidence: str
    warnings: list[str]
    axis: str
    grid: str


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _bars(value: Any, role: str) -> list[dict]:
    bars = []
    items = [value] if isinstance(value, dict) else _as_list(value)
    for item in items:
        bar = _as_dict(item)
        bars.append({
            "role": role,
            "count": max(0, int(_number(bar.get("nb"), 0))),
            "diameter_mm": max(0, int(_number(bar.get("phi"), 0))),
            "unit": "mm",
        })
    return bars


def normalize_plan_data(plan_data: dict) -> dict:
    """Produit un format commun sans supprimer le format historique.

    Chaque élément garde son repère, sa famille, ses dimensions métriques,
    ses quantités, ses armatures, sa source et un niveau de confiance.
    """
    source = _as_dict(plan_data)
    catalogue = _as_dict(source.get("catalogue_types"))
    implantations = _as_dict(source.get("implantations"))
    meta = _as_dict(source.get("_meta"))
    warnings = list(_as_list(meta.get("avertissements")))
    normalized = []

    families = (
        ("semelles", "SEMELLE", "semelles"),
        ("poteaux", "POTEAU", "poteaux"),
        ("poutres", "POUTRE", "poutres"),
    )
    for category, family, output_key in families:
        catalog = _as_dict(catalogue.get(category))
        for placement in _as_list(implantations.get(category)):
            item = _as_dict(placement)
            repere = str(item.get("type") or item.get("id") or "INCONNU")
            raw_dimensions = _as_dict(catalog.get(repere))
            dimensions = raw_dimensions
            dimensions = {
                key: _number(dimensions.get(key))
                for key in ("a", "b", "h", "portee", "hauteur")
                if dimensions.get(key) is not None
            }
            if category == "poteaux":
                dimensions.setdefault("hauteur", _number(
                    item.get("hauteur"), 3.0))
            if category == "poutres":
                dimensions.setdefault("portee", _number(item.get("portee")))

            bars = []
            if category == "semelles":
                bars += _bars(dimensions and catalog.get(repere, {}).get(
                    "ferr_x"), "nappe_x")
                bars += _bars(dimensions and catalog.get(repere, {}).get(
                    "ferr_y"), "nappe_y")
            elif category == "poteaux":
                spec = catalog.get(repere, {})
                bars += _bars(_as_dict(spec).get("long_bars"), "longitudinal")
                bars += _bars([_as_dict(spec).get("cadres")], "cadre")
            else:
                spec = catalog.get(repere, {})
                bars += _bars(_as_dict(spec).get("filants_inf"), "filant_inf")
                bars += _bars(_as_dict(spec).get("filants_sup"), "filant_sup")
                bars += _bars([_as_dict(spec).get("cadres")], "cadre")

            missing = bool(raw_dimensions.get("dimensions_par_defaut")
                          or item.get("position_par_defaut"))
            confidence = "low" if missing else "high"
            item_warnings = []
            if missing:
                item_warnings.append("géométrie ou position par défaut")
            normalized.append({
                "id": str(item.get("id") or repere),
                "repere": repere,
                "reference": repere,
                "family": family,
                "axis": item.get("axe") or "",
                "grid": item.get("file") or "",
                "section_m": dimensions,
                "dimensions_m": dimensions,
                "quantities": {"count": 1, "unit": "u"},
                "quantity": {"count": 1, "unit": "u"},
                "reinforcement": bars,
                "source": {
                    "pages": item.get("pages") or meta.get(
                        "semelles_pages", {}).get(repere, []),
                    "engine": meta.get("moteur", "unknown"),
                    "provenance": item.get("provenance", {}),
                },
                "bbox": item.get("bbox"),
                "confidence": confidence,
                "warnings": item_warnings,
            })

    return {
        "project": _as_dict(source.get("projet")),
        "elements": normalized,
        "warnings": warnings,
        "hypotheses": list(_as_list(meta.get("hypotheses"))),
        "units": {"length": "m", "diameter": "mm", "quantity": "u"},
    }
