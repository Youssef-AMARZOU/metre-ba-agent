"""
core/schemas.py — Modèles de données stricts pour l'extraction de plans BA.

Toutes les données extraites passent par ces schémas Pydantic.
Aucune valeur hardcodée de projet antérieur n'est autorisée ici.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field


class DrawingElement(BaseModel):
    """Représentation canonique d'un élément lu sur un dessin.

    Le modèle reste indépendant du moteur (PDF, OCR ou DAO) afin de
    conserver une provenance exploitable jusqu'aux livrables.
    """
    id: str
    text: str = ""
    bbox: Optional[Tuple[float, float, float, float]] = None
    page: Optional[int] = Field(None, ge=1)
    source: str = "unknown"
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)

    model_config = ConfigDict(frozen=True)


# ============================================================================
# Enums — familles d'éléments et rôles d'armatures
# ============================================================================

class FamilleElement(str, Enum):
    """Familles d'éléments structuraux BA."""
    SEMELLE = "SEMELLE"
    POTEAU = "POTEAU"
    POUTRE = "POUTRE"
    VOILE = "VOILE"
    LONGRINE = "LONGRINE"
    CHAINAGE = "CHAINAGE"
    DALLE = "DALLE"
    MASSIF = "MASSIF"


class RoleArmature(str, Enum):
    """Rôles des barres d'acier dans un élément."""
    NAPPE_X = "nappe_x"
    NAPPE_Y = "nappe_y"
    LONGITUDINAL = "longitudinal"
    CADRE = "cadre"
    EPIGLE = "epingle"
    ANCRAGE = "ancrage"
    MONTANT = "montant"
    CEINTURE = "ceinture"


class TypeBarre(str, Enum):
    """Types de barres d'acier."""
    HA = "HA"   # Haute adhérence
    RL = "RL"   # Rond lisse
    FE = "FE"   # Acier FeE


# ============================================================================
# Schémas d'armatures
# ============================================================================

class BarreAcierSchema(BaseModel):
    """Une barre d'acier ou un ensemble de barres identiques."""
    diametre: int = Field(..., description="Diamètre en mm (6, 8, 10, 12, 14, 16, 20, 25, 32)")
    type_barre: TypeBarre = Field(default=TypeBarre.HA, description="Type de barre")
    role: RoleArmature = Field(..., description="Rôle dans l'élément")
    nombre: int = Field(..., ge=0, description="Nombre de barres")
    longueur_unitaire_m: Optional[float] = Field(None, ge=0, description="Longueur unitaire en m")
    longueur_unitaire_formule: Optional[str] = Field(
        None, description="Formule textuelle si non calculable (ex: '=B-0.05+34*d/1000')"
    )
    poids_unitaire_kg: Optional[float] = Field(None, ge=0, description="Poids unitaire en kg/m")

    model_config = ConfigDict(frozen=True)
# ============================================================================

class ElementStructureSchema(BaseModel):
    """Un élément structuraux BA générique (semelle, poteau, poutre, etc.)."""
    famille: FamilleElement = Field(..., description="Famille de l'élément")
    repere: str = Field(..., description="Repère unique (ex: S1, P2, N1, LG1, CH1)")
    axe: Optional[str] = Field(None, description="Axe ou intersection (ex: A, C-D, A1)")
    file: Optional[str] = Field(None, description="Numéro de file (ex: 1, 2-3)")
    dimensions: Dict[str, float] = Field(
        ..., description="Dimensions en mètres (ex: {'a': 1.50, 'b': 1.50, 'h': 0.40})"
    )
    armatures: List[BarreAcierSchema] = Field(default_factory=list, description="Liste des armatures")
    notes: Optional[str] = Field(None, description="Notes ou observations")

    model_config = ConfigDict(frozen=True)


# ============================================================================
# Schéma de sortie globale
# ============================================================================

class ProjetBAParseOutput(BaseModel):
    """Sortie complète de l'extraction d'un plan BA."""
    nom_projet: Optional[str] = Field(None, description="Nom du projet (extrait du plan)")
    elements: List[ElementStructureSchema] = Field(
        default_factory=list, description="Liste des éléments extraits"
    )

    def by_famille(self, famille: FamilleElement) -> List[ElementStructureSchema]:
        """Filtre les éléments par famille."""
        return [e for e in self.elements if e.famille == famille]

    def count_by_famille(self) -> Dict[str, int]:
        """Compte les éléments par famille."""
        counts: Dict[str, int] = {}
        for e in self.elements:
            counts[e.famille.value] = counts.get(e.famille.value, 0) + 1
        return counts

    model_config = ConfigDict(frozen=True)
