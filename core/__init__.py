"""
core — Module métier pour l'extraction et le calcul Béton Armé.
"""
from .calculator import CivilEngine
from .ingestion import UniversalPlanIngestor, IngestionResult, PlanSource
from .schemas import (
    DrawingElement,
    BarreAcierSchema,
    ElementStructureSchema,
    FamilleElement,
    ProjetBAParseOutput,
    RoleArmature,
    TypeBarre,
)
from .dataset_adapters import adapt_file, drawing_elements_from_file, normalize_drawing_elements
from .external_datasets import (
    DatasetSpec,
    load_dataset_specs,
    validate_all_datasets,
    validate_dataset_structure,
)

__all__ = [
    "CivilEngine",
    "UniversalPlanIngestor",
    "IngestionResult",
    "PlanSource",
    "DrawingElement",
    "BarreAcierSchema",
    "ElementStructureSchema",
    "FamilleElement",
    "ProjetBAParseOutput",
    "RoleArmature",
    "TypeBarre",
    "DatasetSpec",
    "load_dataset_specs",
    "validate_all_datasets",
    "validate_dataset_structure",
    "adapt_file",
    "drawing_elements_from_file",
    "normalize_drawing_elements",
]
