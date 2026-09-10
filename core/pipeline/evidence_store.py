"""
EvidenceStore — magasin central de preuves.
Chaque donnee extraite du plan est stockee avec sa source exacte.
Rien n'est suppose, tout est trace.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
import json


class SourceType(Enum):
    """Origine d'une donnee extraite."""
    LU = "lu"                    # explicitement present dans une source
    CALCULE = "calcule"          # obtenu par formule + entrees sourcees
    HYPOTHESE = "hypothese"      # convention ou interpretation non confirmee
    MANQUANT = "manquant"        # information necessaire absente
    AMBIGU = "ambigu"            # plusieurs interpretations possibles
    CONFLIT = "conflit"          # sources incompatibles
    FALLBACK = "fallback"        # convention generale, non confirmee sur ce plan


@dataclass
class Evidence:
    """Une preuve extraite du plan, avec sa source exacte."""
    value: Any
    source_type: SourceType
    source_location: str          # ex: "tableau_poteaux/ligne_P3/col_section"
    page: int = 0
    x: float = 0.0
    y: float = 0.0
    confidence: float = 1.0       # 0.0 a 1.0
    alternatives: list[Any] = field(default_factory=list)  # si ambigu
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "source": self.source_type.value,
            "location": self.source_location,
            "page": self.page,
            "x": self.x,
            "y": self.y,
            "confidence": self.confidence,
            "alternatives": self.alternatives,
            "note": self.note,
        }


@dataclass
class Observation:
    """Une observation brute: texte, symbole, geometrie vue sur le plan."""
    obs_id: str
    text: str
    page: int
    x: float
    y: float
    x2: float = 0.0
    y2: float = 0.0
    obs_type: str = "text"        # text, symbol, geometry, table_cell
    zone_id: str = ""
    raw_data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.obs_id,
            "text": self.text,
            "page": self.page,
            "x": self.x,
            "y": self.y,
            "x2": self.x2,
            "y2": self.y2,
            "type": self.obs_type,
            "zone": self.zone_id,
        }


@dataclass
class PhysicalInstance:
    """Un element physique identifie sur le plan (pas une etiquette)."""
    instance_id: str
    repere: str
    family_type: str              # appris de la legende
    observations: list[str]       # obs_ids qui constituent cet element
    dimensions: dict[str, Evidence] = field(default_factory=dict)
    ferraillage: dict[str, Evidence] = field(default_factory=dict)
    position: dict[str, Any] = field(default_factory=dict)
    vue: str = ""
    niveau: str = ""
    statut: str = "complet"       # complet, manquant, ambigu, conflit

    def to_dict(self) -> dict:
        return {
            "id": self.instance_id,
            "repere": self.repere,
            "type": self.family_type,
            "observations": self.observations,
            "dimensions": {k: v.to_dict() for k, v in self.dimensions.items()},
            "ferraillage": {k: v.to_dict() for k, v in self.ferraillage.items()},
            "position": self.position,
            "vue": self.vue,
            "niveau": self.niveau,
            "statut": self.statut,
        }


@dataclass
class MetreLine:
    """Une ligne de metre finale, avec traçabilite complete."""
    repere: str
    family_type: str
    instance_id: str
    dimensions: dict[str, Any]
    volume_beton_m3: Optional[float] = None
    surface_coffrage_m2: Optional[float] = None
    poids_acier_kg: Optional[float] = None
    longueur_ml: Optional[float] = None
    quantite: Optional[float] = None
    unite: str = ""
    sources: dict[str, str] = field(default_factory=dict)
    statut: str = "complet"
    vue: str = ""
    niveau: str = ""
    formule: str = ""

    def to_dict(self) -> dict:
        return {
            "repere": self.repere,
            "type": self.family_type,
            "instance_id": self.instance_id,
            "dimensions": self.dimensions,
            "volume_beton_m3": self.volume_beton_m3,
            "surface_coffrage_m2": self.surface_coffrage_m2,
            "poids_acier_kg": self.poids_acier_kg,
            "longueur_ml": self.longueur_ml,
            "quantite": self.quantite,
            "unite": self.unite,
            "sources": self.sources,
            "statut": self.statut,
            "vue": self.vue,
            "niveau": self.niveau,
            "formule": self.formule,
        }


class EvidenceStore:
    """Magasin central de preuves. Interface unique entre tous les modules."""

    def __init__(self):
        self.observations: list[Observation] = []
        self.instances: list[PhysicalInstance] = []
        self.metre_lines: list[MetreLine] = []
        self.legend: dict[str, Evidence] = {}
        self.tables: dict[str, dict[str, dict[str, Evidence]]] = {}
        self.grids: dict[str, dict] = {}
        self.warnings: list[str] = []
        self.ecarts: list[dict] = []
        self.scope: dict[str, str] = {}  # portee: dossier/document/feuille
        self.metadata: dict[str, Any] = {}

    def add_observation(self, obs: Observation):
        self.observations.append(obs)

    def add_instance(self, inst: PhysicalInstance):
        self.instances.append(inst)

    def add_metre_line(self, line: MetreLine):
        self.metre_lines.append(line)

    def set_legend(self, legend: dict[str, Evidence]):
        self.legend = legend

    def add_table(self, name: str, data: dict[str, dict[str, Evidence]]):
        self.tables[name] = data

    def set_grid(self, vue: str, grid: dict):
        self.grids[vue] = grid

    def add_warning(self, msg: str):
        self.warnings.append(msg)

    def add_ecart(self, ecart: dict):
        self.ecarts.append(ecart)

    def get_obs_by_id(self, obs_id: str) -> Optional[Observation]:
        for o in self.observations:
            if o.obs_id == obs_id:
                return o
        return None

    def get_instances_by_repere(self, repere: str) -> list[PhysicalInstance]:
        return [i for i in self.instances if i.repere == repere]

    def get_all_reperes(self) -> set[str]:
        return {i.repere for i in self.instances}

    def get_table_reperes(self) -> set[str]:
        reperes = set()
        for table_data in self.tables.values():
            for rep in table_data.keys():
                reperes.add(rep)
        return reperes

    def get_legend_prefixes(self) -> set[str]:
        return set(self.legend.keys())

    def to_json(self) -> str:
        data = {
            "observations": [o.to_dict() for o in self.observations],
            "instances": [i.to_dict() for i in self.instances],
            "metre": [m.to_dict() for m in self.metre_lines],
            "legend": {k: v.to_dict() for k, v in self.legend.items()},
            "tables": {
                name: {
                    rep: {col: ev.to_dict() for col, ev in cols.items()}
                    for rep, cols in data.items()
                }
                for name, data in self.tables.items()
            },
            "grids": self.grids,
            "warnings": self.warnings,
            "ecarts": self.ecarts,
            "scope": self.scope,
            "metadata": self.metadata,
        }
        return json.dumps(data, ensure_ascii=False, indent=2)
