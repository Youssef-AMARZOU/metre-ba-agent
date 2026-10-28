#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/dimension_resolver.py -- Resolution des dimensions non ecrites (PlanBA).

But
---
Retrouver L, l (et h) d'un element de fondation a partir des **cotations** du
plan, calculer surface / volume, et restituer le resultat avec sa methode, sa
confiance et son statut -- dans l'overlay comme dans le classeur Excel.

Cascade (priorite decroissante)
-------------------------------
M1 DIRECT  ``AxB`` / ``AxBxH`` / ``b=.. h=.. L=..`` (regex tolerante).
M2 SOMME   chaine de cotations contigues entre deux axes (graphe 1D X / Y).
M3 SOUS    (a) cote manquante = total - partielles connues
           (b) longueur nette = entre-axes - demi-largeurs d'appui.
M4 ECHELLE regression (longueur dessin -> valeur cotee) + mesure bbox.
M5 POLY    surface d'un contour ferme (HATCH / LWPOLYLINE), trous retires,
           arcs / bulges discretises, facteur d'echelle au carre.

Regles
------
* Aucune valeur inventee : sans resolution -> ``NON_RESOLU`` + raison.
* Des qu'une 2e methode existe -> recoupement ; ecart > tolerance
  (2 % ou 2 cm par defaut) -> ``A_VERIFIER`` avec les **deux** valeurs.
  Aucun ecrasement silencieux.
* La confiance reutilise le mecanisme historique de l'overlay : ratio
  mesure / declare, seuil 0.90. Le label overlay garde le meme gabarit.
* Unites explicites et configurables (cm par defaut), sorties en m2 / m3.
* Graphe 1D par direction : noeuds = positions snappees, aretes = cotes.
  Systeme determine -> solution exacte ; surdetermine -> moindres carres +
  residu ; sous-determine -> repli M4 (statut ESTIME).
* Chaque decision est journalisee (methode, cotes utilisees).
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "Cote",
    "Axis",
    "AxisGraph",
    "ClosureControl",
    "DimensionResolver",
    "DirectDims",
    "ElementSpec",
    "Method",
    "OverlayAnnotation",
    "PlanResolution",
    "ResolvedElement",
    "ResolverConfig",
    "ScaleModel",
    "SpanResolution",
    "Status",
    "Term",
    "calibrate_scale",
    "cotations_from_dxf",
    "cotations_from_number_centers",
    "format_fr",
    "overlay_annotation",
    "overlay_label",
    "parse_direct",
    "polygon_area",
    "polygon_area_with_holes",
    "shoelace",
]


# ============================================================================
# 1. Unites, statuts, methodes
# ============================================================================

UNIT_TO_M: Dict[str, float] = {"mm": 0.001, "cm": 0.01, "dm": 0.1, "m": 1.0}
AXIS_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class Status(str, Enum):
    """Statut de restitution d'une valeur (aligne sur l'overlay historique)."""

    OK = "OK"
    ESTIME = "ESTIME"
    A_VERIFIER = "A_VERIFIER"
    NON_RESOLU = "NON_RESOLU"

    @property
    def label(self) -> str:
        """Libelle affiche dans l'overlay (meme orthographe que l'existant)."""
        return {
            Status.OK: "OK",
            Status.ESTIME: "ESTIME",
            Status.A_VERIFIER: "A VERIFIER",
            Status.NON_RESOLU: "NON RESOLU",
        }[self]


class Method(str, Enum):
    """Code de methode affiche dans l'overlay et la feuille Elements."""

    DIRECT = "DIR"
    SOMME = "SOM"
    SOUSTRACTION = "SOUS"
    ECHELLE = "ECH"
    POLYGONE = "POLY"
    AUCUNE = "--"


#: Confiance de base par methode quand aucun recoupement n'est possible.
BASE_CONFIDENCE: Dict[Method, float] = {
    Method.DIRECT: 1.00,
    Method.SOMME: 1.00,
    Method.SOUSTRACTION: 0.95,
    Method.ECHELLE: 0.85,
    Method.POLYGONE: 0.90,
    Method.AUCUNE: 0.00,
}

#: Categories dont la surface au sens "plan" = L x l.
FOOTING_CATEGORIES = ("semelle", "massif", "radier", "dalle_pleine", "dalle")
#: Categories linaires : L vient de la cotation, l/h de la section ecrite.
LINEAR_CATEGORIES = ("longrine", "chainage", "poutre", "poutre_ba", "nervure")
#: Categories dont la surface au sens "section" = b x h.
SECTION_CATEGORIES = ("poteau", "voile", "poteau_ba")


# ============================================================================
# 2. Configuration
# ============================================================================

@dataclass
class ResolverConfig:
    """Parametres de resolution (tous explicites, aucun implicite cache).

    Les longueurs internes sont exprimees dans ``unit`` (cm par defaut).
    Les tolérances sont dans la meme unite.
    """

    unit: str = "cm"
    snap_tol: float = 2.0            # tolerance de snap des extremites de cotes
    tol_abs: float = 2.0             # ecart absolu admis (2 cm par defaut)
    tol_pct: float = 0.02            # ecart relatif admis (2 %)
    conf_threshold: float = 0.90     # seuil vert/orange de l'overlay
    net_types: Tuple[str, ...] = ("LG", "CH", "LG2", "LR", "longrine", "chainage")
    length_mode: str = "auto"        # "brut" | "net" | "auto" (par type)
    round_len: int = 2
    round_m2: int = 2
    round_m3: int = 3
    min_scale_points: int = 3
    scale_outlier_pct: float = 0.05
    bulge_segments: int = 16

    def __post_init__(self) -> None:
        self.unit = (self.unit or "cm").lower()
        if self.unit not in UNIT_TO_M:
            raise ValueError(f"unite inconnue : {self.unit!r} (attendu {list(UNIT_TO_M)})")
        if self.length_mode not in ("auto", "brut", "net"):
            raise ValueError("length_mode doit valoir 'auto', 'brut' ou 'net'")

    # -- conversions -------------------------------------------------------
    @property
    def unit_m(self) -> float:
        """Facteur unite source -> metre."""
        return UNIT_TO_M[self.unit]

    def to_m(self, value: Optional[float]) -> Optional[float]:
        return None if value is None else value * self.unit_m

    def m2_from_unit2(self, area: Optional[float]) -> Optional[float]:
        return None if area is None else area * self.unit_m ** 2

    def m3_from_unit3(self, vol: Optional[float]) -> Optional[float]:
        return None if vol is None else vol * self.unit_m ** 3

    def tolerance_for(self, reference: float) -> float:
        """Tolerance d'ecart : max(2 cm, 2 % de la valeur)."""
        return max(self.tol_abs, abs(reference) * self.tol_pct)

    def is_net_type(self, category: str, label: str = "") -> bool:
        if self.length_mode == "brut":
            return False
        if self.length_mode == "net":
            return True
        haystack = f"{category} {label}".upper()
        return any(str(t).upper() in haystack for t in self.net_types)


# ============================================================================
# 3. Cotations
# ============================================================================

@dataclass(frozen=True)
class Cote:
    """Une cotation lineaire du plan.

    ``value``   : valeur cotee dans l'unite de travail (cm par defaut).
    ``start`` / ``end`` : positions 1D sur l'axe (unite dessin : px, mm DXF...).
    ``row``     : ligne de cotation (y pour un axe X, x pour un axe Y) ; sert a
                  ne pas melanger deux chaines paralleles.
    """

    value: float
    start: float
    end: float
    direction: str = "X"
    label: str = ""
    source: str = "txt"
    axis_start: str = ""
    axis_end: str = ""
    row: float = 0.0
    id: str = ""

    def __post_init__(self) -> None:
        if self.value is None or not math.isfinite(float(self.value)):
            raise ValueError("valeur de cote non finie")
        if not math.isfinite(float(self.start)) or not math.isfinite(float(self.end)):
            raise ValueError("extremite de cote non finie")
        if abs(self.end - self.start) < 1e-9:
            raise ValueError("cote de longueur nulle")

    # -- geometrie ---------------------------------------------------------
    @property
    def lo(self) -> float:
        return min(self.start, self.end)

    @property
    def hi(self) -> float:
        return max(self.start, self.end)

    @property
    def drawing_length(self) -> float:
        return abs(self.end - self.start)

    @property
    def mid(self) -> float:
        return (self.start + self.end) / 2.0

    def covers(self, lo: float, hi: float, tol: float) -> bool:
        return self.lo <= lo + tol and self.hi >= hi - tol

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "direction": self.direction,
            "valeur": round(self.value, 6),
            "longueur_dessin": round(self.drawing_length, 6),
            "axe_debut": self.axis_start,
            "axe_fin": self.axis_end,
            "label": self.label,
            "source": self.source,
            "row": round(self.row, 6),
        }


@dataclass
class Axis:
    """Position cumulee d'un axe dans une direction."""

    direction: str
    name: str
    position: float
    determined: bool = True
    cote_ids: List[str] = field(default_factory=list)

    @property
    def status(self) -> Status:
        return Status.OK if self.determined else Status.ESTIME


# ============================================================================
# 4. Formulaire lisible (termes + formule Excel)
# ============================================================================

@dataclass(frozen=True)
class Term:
    """Un terme d'une formule : +cote, -cote, +litteral/diviseur."""

    sign: int = 1
    cote_id: str = ""
    value: float = 0.0
    divisor: float = 1.0
    label: str = ""

    @property
    def operand(self) -> float:
        return self.value / self.divisor if self.divisor else self.value

    def readable(self) -> str:
        body = _num(self.value)
        if not self.cote_id and self.divisor not in (1, 1.0):
            body += "/" + _num(self.divisor)
        return body


def formula_value(terms: Sequence[Term]) -> float:
    return sum(t.sign * t.operand for t in terms)


def formula_text(terms: Sequence[Term]) -> str:
    """``=725+238-120/2`` (texte lisible, jamais evalue par Excel)."""
    if not terms:
        return ""
    parts: List[str] = []
    for i, t in enumerate(terms):
        if i == 0:
            parts.append(("-" if t.sign < 0 else "") + t.readable())
        else:
            parts.append(("-" if t.sign < 0 else "+") + t.readable())
    return "=" + "".join(parts)


# ============================================================================
# 5. Graphe 1D : noeuds snappes, aretes = cotes, moindres carres
# ============================================================================

@dataclass
class GraphSolution:
    positions: List[Optional[float]]
    determined: bool
    residuals: Dict[str, float]
    max_residual: float
    rank: int
    n_unknowns: int


@dataclass
class AxisGraph:
    """Graphe 1D d'une direction (X ou Y).

    Les noeuds sont les extremites de cotes regroupees par proximite
    (``snap_tol``). Le noeud le plus a gauche est ancre a 0 ; les autres
    positions sont resolues par moindres carres.
    """

    direction: str
    cotes: List[Cote] = field(default_factory=list)
    snap_tol: float = 2.0
    names: Dict[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._positions: List[float] = []
        self._edges: List[Tuple[int, int, float, Cote]] = []
        self._clusters: List[float] = []
        self._solution: Optional[GraphSolution] = None
        self._assign: Dict[float, int] = {}

    # -- construction ------------------------------------------------------
    def build(self) -> "AxisGraph":
        if not self.cotes:
            return self
        tol = self.snap_tol
        raw = sorted({round(c.start, 6) for c in self.cotes}
                     | {round(c.end, 6) for c in self.cotes})
        clusters: List[float] = []
        for pos in raw:
            if clusters and pos - clusters[-1] <= tol:
                continue
            clusters.append(pos)
        # rattachement (la borne la plus proche, a tolerance)
        assign: Dict[float, int] = {}
        for pos in raw:
            idx = min(range(len(clusters)), key=lambda i: abs(clusters[i] - pos))
            if abs(clusters[idx] - pos) > tol:
                clusters.append(pos)
                clusters.sort()
                assign = {}
                for p2 in raw:
                    i2 = min(range(len(clusters)), key=lambda i: abs(clusters[i] - p2))
                    assign[p2] = i2
                break
            assign[pos] = idx
        self._clusters = clusters
        self._assign = assign
        edges: List[Tuple[int, int, float, Cote]] = []
        for c in self.cotes:
            i = assign[round(c.start, 6)]
            j = assign[round(c.end, 6)]
            if i == j:
                logger.warning("Cote %s ignoree : extremites confondues apres snap", c.id)
                continue
            edges.append((i, j, c.value, c))
        self._edges = edges
        return self

    # -- resolution --------------------------------------------------------
    def solve(self) -> GraphSolution:
        if self._solution is not None:
            return self._solution
        if not self.cotes:
            self._solution = GraphSolution([], True, {}, 0.0, 0, 0)
            return self._solution
        if not self._clusters:
            self.build()
        n = len(self._clusters)
        positions: List[Optional[float]] = [None] * n

        parent = list(range(n))

        def find(a: int) -> int:
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for i, j, _v, _c in self._edges:
            union(i, j)

        comps: Dict[int, List[int]] = {}
        for k in range(n):
            comps.setdefault(find(k), []).append(k)

        anchor = min(range(n), key=lambda k: self._clusters[k])
        anchor_root = find(anchor)
        residuals: Dict[str, float] = {}
        rank_total = 0
        unknown_total = 0
        determined = True

        for root, nodes in comps.items():
            edges = [e for e in self._edges if e[0] in nodes and e[1] in nodes]
            is_anchored = root == anchor_root
            solved = self._solve_component(nodes, edges, anchor if is_anchored else None)
            rank_total += solved[1]
            unknown_total += max(0, len(nodes) - (1 if is_anchored else 0))
            if solved[0] is None:
                determined = False
                # repli arborescence : positions relatives, statut ESTIME
                for k, p in self._tree_positions(nodes, edges, anchor if is_anchored else None):
                    positions[k] = p
            else:
                vals, _rank = solved
                for k, p in zip(nodes, vals):
                    positions[k] = p
                if not is_anchored:
                    determined = False
            for i, j, v, c in edges:
                if positions[i] is not None and positions[j] is not None:
                    residuals[c.id or f"{i}-{j}"] = positions[j] - positions[i] - v

        # positions relatives a 0 pour le noeud le plus a gauche
        base = positions[anchor]
        if base is not None:
            positions = [None if p is None else p - base for p in positions]

        max_res = max((abs(r) for r in residuals.values()), default=0.0)
        self._solution = GraphSolution(
            positions=positions,
            determined=determined,
            residuals=residuals,
            max_residual=max_res,
            rank=rank_total,
            n_unknowns=unknown_total,
        )
        return self._solution

    def _solve_component(
        self,
        nodes: List[int],
        edges: List[Tuple[int, int, float, Cote]],
        anchor: Optional[int],
    ) -> Tuple[Optional[List[float]], int]:
        """Moindres carres du composant ; ``None`` si sous-determine."""
        if anchor is None:
            # composant non ancre : positions relatives, non deterministes
            return None, 0
        free = [k for k in nodes if k != anchor]
        idx = {node: k for k, node in enumerate(free)}
        nf = len(free)
        if nf == 0:
            return [0.0], 0
        rows: List[List[float]] = []
        rhs: List[float] = []
        for i, j, v, _c in edges:
            row = [0.0] * nf
            if j != anchor:
                row[idx[j]] += 1.0
            if i != anchor:
                row[idx[i]] -= 1.0
            rows.append(row)
            rhs.append(v)
        if not rows:
            return None, 0
        # equations normales A^T A x = A^T b
        ata = [[0.0] * nf for _ in range(nf)]
        atb = [0.0] * nf
        for row, b in zip(rows, rhs):
            for r in range(nf):
                if row[r] == 0.0:
                    continue
                atb[r] += row[r] * b
                for c in range(nf):
                    if row[c]:
                        ata[r][c] += row[r] * row[c]
        x, rank = _solve_linear(ata, atb)
        if x is None:
            return None, rank
        out = [0.0] * len(nodes)
        for node in nodes:
            out[nodes.index(node)] = 0.0 if node == anchor else x[idx[node]]
        return out, rank

    def _tree_positions(
        self,
        nodes: List[int],
        edges: List[Tuple[int, int, float, Cote]],
        anchor: Optional[int],
    ) -> List[Tuple[int, float]]:
        """Parcours en largeur : positions coherentes avec l'arbre, ESTIME."""
        if not nodes:
            return []
        root = anchor if anchor is not None else nodes[0]
        known: Dict[int, float] = {root: 0.0}
        queue = [root]
        adj: Dict[int, List[Tuple[int, float]]] = {}
        for i, j, v, _c in edges:
            adj.setdefault(i, []).append((j, v))
            adj.setdefault(j, []).append((i, -v))
        while queue:
            cur = queue.pop(0)
            for nxt, v in sorted(adj.get(cur, []), key=lambda t: t[0]):
                if nxt in known:
                    continue
                known[nxt] = known[cur] + v
                queue.append(nxt)
        return [(k, known[k]) for k in nodes if k in known]

    # -- acces -------------------------------------------------------------
    def position_of(self, pos: float) -> Optional[float]:
        """Position resolue du noeud le plus proche de ``pos`` (dans snap_tol)."""
        if not self._clusters:
            self.build()
        if not self._clusters:
            return None
        idx = min(range(len(self._clusters)), key=lambda i: abs(self._clusters[i] - pos))
        if abs(self._clusters[idx] - pos) > self.snap_tol:
            return None
        return self.solve().positions[idx]

    def node_index(self, pos: float) -> Optional[int]:
        if not self._clusters:
            self.build()
        if not self._clusters:
            return None
        idx = min(range(len(self._clusters)), key=lambda i: abs(self._clusters[i] - pos))
        return idx if abs(self._clusters[idx] - pos) <= self.snap_tol else None

    def axes(self) -> List[Axis]:
        if not self._clusters:
            self.build()
        sol = self.solve()
        out: List[Axis] = []
        for k, pos in enumerate(sol.positions):
            if pos is None:
                continue
            name = self.names.get(k) or f"{self.direction}{k + 1}"
            cote_ids = [c.id for c in self.cotes
                        if self._assign.get(round(c.start, 6)) == k
                        or self._assign.get(round(c.end, 6)) == k]
            out.append(Axis(self.direction, name, round(pos, 6),
                            determined=sol.determined, cote_ids=cote_ids))
        out.sort(key=lambda a: a.position)
        return out

    # -- chaines -----------------------------------------------------------
    def chain_between(self, a: float, b: float, row: Optional[float] = None
                      ) -> Optional[List[Cote]]:
        """Chaine de cotes contigues couvrant exactement [a, b]."""
        tol = self.snap_tol
        lo, hi = min(a, b), max(a, b)
        if hi - lo <= tol:
            return None
        pool = [c for c in self.cotes if c.lo >= lo - tol and c.hi <= hi + tol]
        if not pool:
            return None
        rows = sorted({round(c.row, 6) for c in pool})
        best: Optional[List[Cote]] = None
        for r in rows:
            chain = self._greedy_chain(pool, lo, hi, r)
            if chain and (best is None or len(chain) < len(best)):
                best = chain
        if best is None:
            best = self._greedy_chain(pool, lo, hi, None)
        return best

    def _greedy_chain(self, pool: Sequence[Cote], lo: float, hi: float,
                      row: Optional[float]) -> Optional[List[Cote]]:
        tol = self.snap_tol
        cands = [c for c in pool
                 if row is None or abs(round(c.row, 6) - row) <= tol]
        if not cands:
            return None
        chain: List[Cote] = []
        cur = lo
        guard = 0
        while hi - cur > tol:
            options = [c for c in cands if abs(c.lo - cur) <= tol and c.hi > cur + tol]
            if not options:
                return None
            options.sort(key=lambda c: (-c.hi, c.value, c.id))
            pick = options[0]
            chain.append(pick)
            cur = pick.hi
            guard += 1
            if guard > len(cands) + 2:
                return None
        return chain

    def gaps_within(self, cote: Cote) -> Tuple[List[Tuple[float, float]], List[Cote]]:
        """Trous non couverts a l'interieur d'une cote (cote manquante)."""
        tol = self.snap_tol
        inner = [c for c in self.cotes
                 if c is not cote and c.lo >= cote.lo - tol and c.hi <= cote.hi + tol
                 and not (c.lo <= cote.lo + tol and c.hi >= cote.hi - tol)]
        inner.sort(key=lambda c: (c.lo, c.hi))
        gaps: List[Tuple[float, float]] = []
        used: List[Cote] = []
        cur = cote.lo
        for c in inner:
            if c.hi <= cur + tol:
                continue
            if c.lo > cur + tol:
                gaps.append((cur, c.lo))
            if c.lo >= cur - tol:
                used.append(c)
                cur = max(cur, c.hi)
        if cote.hi - cur > tol:
            gaps.append((cur, cote.hi))
        return gaps, used


# ============================================================================
# 6. Algebre lineaire (Gauss-Jordan, pivot partiel)
# ============================================================================

def _solve_linear(a: List[List[float]], b: List[float]
                  ) -> Tuple[Optional[List[float]], int]:
    """Resout ``A x = b``. Retourne (x, rang) ; ``x=None`` si singulier."""
    n = len(a)
    if n == 0:
        return [], 0
    m = [list(row) + [b[i]] for i, row in enumerate(a)]
    scale = max((abs(v) for row in a for v in row), default=0.0) or 1.0
    eps = 1e-9 * scale
    pivots: List[int] = []
    row = 0
    for col in range(n):
        best = max(range(row, n), key=lambda r: abs(m[r][col]))
        if abs(m[best][col]) <= eps:
            continue
        m[row], m[best] = m[best], m[row]
        piv = m[row][col]
        for r in range(n):
            if r == row:
                continue
            f = m[r][col] / piv
            if f:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[row][c]
        pivots.append(col)
        row += 1
        if row == n:
            break
    rank = len(pivots)
    if rank < n:
        return None, rank
    x = [0.0] * n
    for i, c in enumerate(pivots):
        x[c] = m[i][n] / m[i][c]
    return x, rank


# ============================================================================
# 7. M1 -- lecture directe du texte
# ============================================================================

NUM = r"\d{1,4}(?:[.,]\d{1,3})?"
RE_XYZ = re.compile(rf"({NUM})\s*[xX×*]\s*({NUM})(?:\s*[xX×*]\s*({NUM}))?")
RE_KEY = {
    "L": re.compile(rf"\bL\s*[=:]\s*({NUM})", re.I),
    "b": re.compile(rf"\bb\s*[=:]\s*({NUM})", re.I),
    "h": re.compile(rf"\bh\s*[=:]\s*({NUM})", re.I),
    "a": re.compile(rf"\ba\s*[=:]\s*({NUM})", re.I),
    "e": re.compile(rf"\b(?:e|ep)\s*[=:]\s*({NUM})", re.I),
}
RE_UNIT = re.compile(rf"({NUM})\s*(mm|cm|dm|m)\b", re.I)


def _f(value: str) -> float:
    return float(value.replace(",", "."))


def _num(value: float) -> str:
    """Nombre court et deterministe pour une formule lisible."""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def format_fr(value: Optional[float], nd: int = 2) -> str:
    """Format francais : 4,25."""
    if value is None:
        return "-"
    return f"{value:.{nd}f}".replace(".", ",")


@dataclass
class DirectDims:
    """Dimensions lues dans un texte, dans l'unite declaree."""

    values: List[float]
    unit: str
    raw: str
    keyed: Dict[str, float] = field(default_factory=dict)

    def convert(self, target_unit: str) -> "DirectDims":
        if self.unit == target_unit:
            return self
        f = UNIT_TO_M[self.unit] / UNIT_TO_M[target_unit]
        return DirectDims([v * f for v in self.values], target_unit, self.raw,
                          {k: v * f for k, v in self.keyed.items()})

    def interpret(self, category: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """Retourne (L, l, h) selon la categorie.

        - semelle / massif / dalle : AxBxH -> (L, l, h)
        - longrine / chainage / poutre : bxh -> (None, b, h)
        - poteau / voile : bxh -> (None, b, h)
        """
        cat = (category or "").lower()
        keyed = self.keyed
        if cat in SECTION_CATEGORIES or cat in LINEAR_CATEGORIES:
            b = keyed.get("b") or keyed.get("a")
            h = keyed.get("h") or keyed.get("e")
            L = keyed.get("L")
            vals = list(self.values)
            if b is None and vals:
                b = vals[0]
            if h is None and len(vals) >= 2:
                h = vals[1]
            if L is None and len(vals) >= 3:
                L = vals[2]
            return L, b, h
        # footing / dalle
        a = keyed.get("L") or keyed.get("a")
        b = keyed.get("b")
        h = keyed.get("h") or keyed.get("e")
        vals = list(self.values)
        if a is None and vals:
            a = vals[0]
        if b is None and len(vals) >= 2:
            b = vals[1]
        if h is None and len(vals) >= 3:
            h = vals[2]
        return a, b, h


def parse_direct(text: str, default_unit: str = "cm") -> Optional[DirectDims]:
    """M1 : extrait des dimensions ecrites ; ``None`` si rien de lisible."""
    if not text:
        return None
    raw = text
    unit = default_unit
    m_unit = RE_UNIT.search(text)
    if m_unit:
        unit = m_unit.group(2).lower()
    keyed: Dict[str, float] = {}
    for key, rx in RE_KEY.items():
        m = rx.search(text)
        if m:
            keyed[key] = _f(m.group(1))
    values: List[float] = []
    m = RE_XYZ.search(text)
    if m:
        values = [_f(g) for g in m.groups() if g]
    if not values and not keyed:
        return None
    if not values:
        values = [keyed[k] for k in ("L", "b", "h") if k in keyed]
    # un texte "S4(150x150x40)" sans suffixe garde l'unite par defaut
    return DirectDims(values=values, unit=unit, raw=raw, keyed=keyed)


# ============================================================================
# 8. M4 -- echelle par regression robuste
# ============================================================================

@dataclass
class ScaleModel:
    """Modele lineaire ``valeur = facteur * longueur_dessin``."""

    factor: float                 # unites source par unite dessin
    r2: float = 0.0
    n_used: int = 0
    n_rejected: int = 0
    rejected: List[int] = field(default_factory=list)
    source: str = "regression"

    def measure(self, drawing_length: float) -> float:
        return drawing_length * self.factor

    def to_dict(self) -> Dict[str, Any]:
        return {
            "facteur": round(self.factor, 6),
            "r2": round(self.r2, 5),
            "n_utilises": self.n_used,
            "n_rejetes": self.n_rejected,
            "source": self.source,
        }


def calibrate_scale(pairs: Iterable[Tuple[float, float]],
                    outlier_pct: float = 0.05,
                    min_points: int = 3) -> Optional[ScaleModel]:
    """Calibre l'echelle sur des couples (longueur dessin, valeur cotee).

    Regression passant par l'origine + rejet iteratif des outliers (MAD).
    """
    data = [(float(x), float(y)) for x, y in pairs if x > 1e-9 and y > 1e-9]
    if len(data) < max(2, min_points):
        if len(data) < 2:
            return None
    kept = list(range(len(data)))
    for _ in range(3):
        ratios = [data[i][1] / data[i][0] for i in kept]
        med = _median(ratios)
        if med <= 0:
            return None
        mad = _median([abs(r - med) for r in ratios])
        band = max(outlier_pct * med, 3.0 * 1.4826 * mad)
        new_kept = [i for i in kept if abs(data[i][1] / data[i][0] - med) <= band]
        if len(new_kept) < max(2, min_points):
            break
        if new_kept == kept:
            break
        kept = new_kept
    sxy = sum(data[i][0] * data[i][1] for i in kept)
    sxx = sum(data[i][0] ** 2 for i in kept)
    if sxx <= 0:
        return None
    factor = sxy / sxx
    ss_res = sum((data[i][1] - factor * data[i][0]) ** 2 for i in kept)
    ss_tot = sum(data[i][1] ** 2 for i in kept)
    r2 = 0.0 if ss_tot <= 0 else max(0.0, 1.0 - ss_res / ss_tot)
    rejected = [i for i in range(len(data)) if i not in kept]
    return ScaleModel(factor=factor, r2=r2, n_used=len(kept),
                      n_rejected=len(rejected), rejected=rejected)


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


# ============================================================================
# 9. M5 -- surfaces polygonales (lacet, trous, bulges)
# ============================================================================

def shoelace(points: Sequence[Tuple[float, float]]) -> float:
    """Aire signee (formule du lacet)."""
    if len(points) < 3:
        return 0.0
    total = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def polygon_area(points: Sequence[Tuple[float, float]]) -> float:
    return abs(shoelace(points))


def polygon_area_with_holes(rings: Sequence[Sequence[Tuple[float, float]]]) -> float:
    """Aire du contour exterieur moins les trous (tous en valeur absolue)."""
    if not rings:
        return 0.0
    outer = polygon_area(rings[0])
    holes = sum(polygon_area(r) for r in rings[1:])
    return max(0.0, outer - holes)


def arc_points(p1: Tuple[float, float], p2: Tuple[float, float],
               bulge: float, segments: int = 16) -> List[Tuple[float, float]]:
    """Points intermediaires d'un arc LWPOLYLINE (bulge = tan(theta/4))."""
    if abs(bulge) < 1e-12 or segments < 2:
        return [p1, p2]
    x1, y1 = p1
    x2, y2 = p2
    dx, dy = x2 - x1, y2 - y1
    chord = math.hypot(dx, dy)
    if chord <= 1e-12:
        return [p1, p2]
    theta = 4.0 * math.atan(bulge)
    mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    nx, ny = -dy / chord, dx / chord
    h = (chord / 2.0) / math.tan(theta / 2.0) if abs(math.tan(theta / 2.0)) > 1e-12 else 0.0
    cx, cy = mx + nx * h, my + ny * h
    radius = math.hypot(x1 - cx, y1 - cy)
    start = math.atan2(y1 - cy, x1 - cx)
    pts: List[Tuple[float, float]] = []
    for k in range(segments + 1):
        ang = start + theta * (k / segments)
        pts.append((cx + radius * math.cos(ang), cy + radius * math.sin(ang)))
    return pts


def polyline_area(vertices: Sequence[Tuple[float, float, float]]
                  ) -> float:
    """Aire d'une polyligne ``(x, y, bulge)`` (bulge = segment vers le suivant)."""
    if len(vertices) < 3:
        return 0.0
    pts: List[Tuple[float, float]] = []
    n = len(vertices)
    for i in range(n):
        x1, y1, b = vertices[i]
        x2, y2, _ = vertices[(i + 1) % n]
        seg = arc_points((x1, y1), (x2, y2), b)
        pts.extend(seg[:-1])
    return polygon_area(pts)


# ============================================================================
# 10. Lectures de cotations (DXF / centres de texte)
# ============================================================================

def cotations_from_dxf(path: str | Path, *,
                       unit: str = "cm",
                       dxf_unit: str = "cm",
                       layer: str = "") -> List[Cote]:
    """Lit les entites DIMENSION d'un DXF (points de definition, pas le texte).

    ``unit`` : unite de travail des valeurs ; ``dxf_unit`` : unite du dessin.
    Les positions restent dans l'unite du dessin (pour l'echelle M4).
    """
    try:
        import ezdxf  # noqa: PLC0415  (import lourd et optionnel)
    except ImportError:  # pragma: no cover
        logger.warning("ezdxf absent : lecture DXF impossible")
        return []
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    factor = UNIT_TO_M[dxf_unit] / UNIT_TO_M[unit]
    out: List[Cote] = []
    for dim in msp.query("DIMENSION"):
        if layer and str(dim.dxf.get("layer", "")) != layer:
            continue
        p2 = dim.dxf.get("defpoint2", None)
        p3 = dim.dxf.get("defpoint3", None)
        if p2 is None or p3 is None:
            continue
        x1, y1 = float(p2[0]), float(p2[1])
        x2, y2 = float(p3[0]), float(p3[1])
        horizontal = abs(x2 - x1) >= abs(y2 - y1)
        value: Optional[float] = None
        try:
            meas = dim.get_measurement()
            if isinstance(meas, (int, float)) and abs(float(meas)) > 1e-9:
                value = float(meas) * factor
        except Exception:  # pragma: no cover - ezdxf peut lever selon la version
            value = None
        text = str(dim.dxf.get("text", "") or "").strip()
        if value is None and text:
            m = re.search(rf"({NUM})", text)
            if m:
                value = _f(m.group(1)) * factor
        if value is None:
            continue
        dp = dim.dxf.get("defpoint", None)
        row = (float(dp[1]) if horizontal else float(dp[0])) if dp else (y1 if horizontal else x1)
        out.append(Cote(
            value=value,
            start=x1 if horizontal else y1,
            end=x2 if horizontal else y2,
            direction="X" if horizontal else "Y",
            label=text or f"{_num(value)}",
            source="dxf",
            row=row,
        ))
    logger.info("DXF %s : %d cotation(s) lue(s)", Path(path).name, len(out))
    return out


def cotations_from_number_centers(
    items: Iterable[Tuple[float, float, float]],
    *,
    direction: str = "X",
    tol: float = 12.0,
    source: str = "ocr",
    min_sep: float = 4.0,
) -> List[Cote]:
    """Reconstruit une chaine de cotations a partir de centres de nombres.

    ``items`` : ``(valeur, cx, cy)`` en coordonnees dessin. Les nombres sont
    regroupes par ligne (cy pour X, cx pour Y), puis les separations
    (traits de cote) sont estimees aux milieux des centres voisins.

    Heuristique assumee : elle sert a l'echelle M4 et au chainage, pas a
    remplacer une lecture DIMENSION. A verifier sur plans non homogenes.
    """
    data = [(float(v), float(x), float(y)) for v, x, y in items]
    if len(data) < 2:
        return []
    cross = 2 if direction.upper() == "X" else 1
    along = 1 if direction.upper() == "X" else 2
    rows: List[List[Tuple[float, float, float]]] = []
    for item in sorted(data, key=lambda t: t[cross]):
        if rows and abs(item[cross] - rows[-1][0][cross]) <= tol:
            rows[-1].append(item)
        else:
            rows.append([item])
    out: List[Cote] = []
    for row in rows:
        if len(row) < 2:
            continue
        row.sort(key=lambda t: t[along])
        centers = [t[along] for t in row]
        # ecarte les nombres colles (indices d'axes, etiquettes)
        kept = [0]
        for k in range(1, len(row)):
            if centers[k] - centers[kept[-1]] >= min_sep:
                kept.append(k)
        row = [row[k] for k in kept]
        if len(row) < 2:
            continue
        centers = [t[along] for t in row]
        ticks = [centers[0] - (centers[1] - centers[0]) / 2.0]
        for k in range(1, len(row)):
            ticks.append((centers[k - 1] + centers[k]) / 2.0)
        ticks.append(centers[-1] + (centers[-1] - centers[-2]) / 2.0)
        row_cross = sum(t[cross] for t in row) / len(row)
        for k, (v, _cx, _cy) in enumerate(row):
            start, end = ticks[k], ticks[k + 1]
            if abs(end - start) <= 1e-9:
                continue
            out.append(Cote(
                value=v, start=start, end=end, direction=direction.upper(),
                label=_num(v), source=source, row=row_cross,
            ))
    return out


# ============================================================================
# 11. Resultats de resolution
# ============================================================================

@dataclass
class SpanResolution:
    """Resolution d'une portee entre deux axes."""

    value: Optional[float]
    method: Method = Method.AUCUNE
    terms: List[Term] = field(default_factory=list)
    cote_ids: List[str] = field(default_factory=list)
    residual: float = 0.0
    status: Status = Status.NON_RESOLU
    note: str = ""

    @property
    def formula(self) -> str:
        return formula_text(self.terms)


@dataclass
class SecondaryCheck:
    """Valeur de recoupement (2e methode) et ecart mesure."""

    method: Method
    value: float
    gap: float
    gap_pct: float
    ok: bool
    primary: float = 0.0
    note: str = ""


@dataclass
class ResolvedElement:
    """Element dont les dimensions ont ete resolues (ou non)."""

    element_id: str
    category: str = "semelle"
    label: str = ""
    axis_start: str = ""
    axis_end: str = ""
    L: Optional[float] = None              # unite de travail (cm)
    l: Optional[float] = None
    h: Optional[float] = None
    surface_m2: Optional[float] = None
    volume_m3: Optional[float] = None
    surface_kind: Optional[str] = None     # "plan" | "section" | None
    method: Method = Method.AUCUNE
    status: Status = Status.NON_RESOLU
    confidence: float = 0.0
    unit: str = "cm"
    mode: str = "brut"                     # brut axe-a-axe | net entre nus
    terms: List[Term] = field(default_factory=list)
    formula: str = ""
    cote_ids: List[str] = field(default_factory=list)
    secondary: Optional[SecondaryCheck] = None
    notes: List[str] = field(default_factory=list)

    # -- sorties -----------------------------------------------------------
    @property
    def ok(self) -> bool:
        return self.status is Status.OK

    def label_overlay(self) -> str:
        return overlay_label(self)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.element_id,
            "type": self.category,
            "libelle": self.label,
            "axe_debut": self.axis_start,
            "axe_fin": self.axis_end,
            "L": None if self.L is None else round(self.L, 2),
            "l": None if self.l is None else round(self.l, 2),
            "h": None if self.h is None else round(self.h, 2),
            "surface_m2": None if self.surface_m2 is None else round(self.surface_m2, 2),
            "volume_m3": None if self.volume_m3 is None else round(self.volume_m3, 3),
            "methode": self.method.value,
            "formule": self.formula,
            "confiance": round(self.confidence, 2),
            "statut": self.status.value,
            "mode": self.mode,
            "controle": None if self.secondary is None else {
                "methode": self.secondary.method.value,
                "valeur": round(self.secondary.value, 2),
                "ecart": round(self.secondary.gap, 2),
                "ecart_pct": round(self.secondary.gap_pct, 4),
                "ok": self.secondary.ok,
            },
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class OverlayAnnotation:
    """Label pret a dessiner (texte + couleur BGR, meme charte que l'existant)."""

    text: str
    status: str
    color_bgr: Tuple[int, int, int]


def _status_color(status: Status) -> Tuple[int, int, int]:
    return {
        Status.OK: (0, 180, 0),
        Status.A_VERIFIER: (0, 140, 255),
        Status.ESTIME: (0, 140, 255),
        Status.NON_RESOLU: (0, 0, 255),
    }[status]


def overlay_label(res: ResolvedElement) -> str:
    """``S=4,25 m2 [SOM OK 1.00]`` (memes codes couleur que l'overlay)."""
    if res.surface_m2 is not None:
        head = f"S={format_fr(res.surface_m2, 2)} m²"
    elif res.L is not None:
        head = f"L={format_fr(res.L * UNIT_TO_M.get(res.unit, 0.01), 2)} m"
    elif res.volume_m3 is not None:
        head = f"V={format_fr(res.volume_m3, 3)} m³"
    else:
        head = "NON RESOLU"
    return f"{head} [{res.method.value} {res.status.label} {res.confidence:.2f}]"


def overlay_annotation(res: ResolvedElement) -> OverlayAnnotation:
    return OverlayAnnotation(overlay_label(res), res.status.value,
                             _status_color(res.status))


@dataclass
class ClosureControl:
    """Controle de fermeture d'une chaine de cotations."""

    chain: str
    declared: float
    computed: float
    tolerance: float
    detail: str = ""

    @property
    def gap(self) -> float:
        return self.declared - self.computed

    @property
    def ok(self) -> bool:
        return abs(self.gap) <= self.tolerance

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chaine": self.chain,
            "total": round(self.declared, 3),
            "somme": round(self.computed, 3),
            "ecart": round(self.gap, 3),
            "tolerance": round(self.tolerance, 3),
            "verdict": "OK" if self.ok else "A VERIFIER",
            "detail": self.detail,
        }


# ============================================================================
# 12. Specification d'un element a resoudre
# ============================================================================

@dataclass
class ElementSpec:
    """Entree du resolveur pour un element de fondation."""

    label: str
    category: str = "semelle"
    declared_text: str = ""          # "S4(150x150x40)" ou "150x150x40"
    element_id: str = ""
    axis_start: str = ""
    axis_end: str = ""
    direction: str = "X"
    pos_start: Optional[float] = None   # position dessin sur l'axe (px / mm DXF)
    pos_end: Optional[float] = None
    row: Optional[float] = None
    width_start: Optional[float] = None  # largeur d'appui (unite de travail)
    width_end: Optional[float] = None
    height: Optional[float] = None
    width: Optional[float] = None        # l impose par l'appelant (ex. emprise)
    length: Optional[float] = None       # L impose par l'appelant
    bbox: Optional[Tuple[float, float, float, float]] = None  # (x, y, w, h) dessin
    polygon: Optional[Sequence[Sequence[Tuple[float, float]]]] = None
    weight: int = 1

    def __post_init__(self) -> None:
        self.category = (self.category or "semelle").strip().lower()
        self.element_id = self.element_id or self.label or "?"


# ============================================================================
# 13. Resolveur
# ============================================================================

class DimensionResolver:
    """Resout les dimensions d'elements a partir des cotations d'un plan."""

    def __init__(self, config: Optional[ResolverConfig] = None,
                 axis_names: Optional[Dict[str, Sequence[str]]] = None) -> None:
        self.config = config or ResolverConfig()
        self._cotes: List[Cote] = []
        self._graphs: Dict[str, AxisGraph] = {}
        self._scale: Optional[ScaleModel] = None
        self._counters: Dict[str, int] = {}
        self._axis_names = {k.upper(): list(v) for k, v in (axis_names or {}).items()}
        self.log: List[str] = []

    # -- cotations ---------------------------------------------------------
    def add_cote(self, cote: Cote) -> Cote:
        direction = (cote.direction or "X").upper()
        if direction not in ("X", "Y"):
            raise ValueError(f"direction inconnue : {cote.direction!r}")
        self._counters[direction] = self._counters.get(direction, 0) + 1
        cid = cote.id or f"{direction}{self._counters[direction]}"
        c = Cote(value=cote.value, start=cote.start, end=cote.end,
                 direction=direction, label=cote.label, source=cote.source,
                 axis_start=cote.axis_start, axis_end=cote.axis_end,
                 row=cote.row, id=cid)
        self._cotes.append(c)
        self._graphs.pop(direction, None)
        return c

    def add_cotes(self, cotations: Iterable[Cote]) -> List[Cote]:
        return [self.add_cote(c) for c in cotations]

    @property
    def cotes(self) -> List[Cote]:
        return list(self._cotes)

    def graph(self, direction: str = "X") -> AxisGraph:
        d = direction.upper()
        if d not in self._graphs:
            g = AxisGraph(direction=d,
                          cotes=[c for c in self._cotes if c.direction == d],
                          snap_tol=self.config.snap_tol)
            if d in self._axis_names:
                g.names = {i: name for i, name in enumerate(self._axis_names[d])}
            g.build()
            self._graphs[d] = g
        return self._graphs[d]

    def axes(self, direction: str = "X") -> List[Axis]:
        return self.graph(direction).axes()

    def axis_position(self, direction: str, pos: float) -> Optional[float]:
        return self.graph(direction).position_of(pos)

    # -- M4 ----------------------------------------------------------------
    def set_scale(self, model: Optional[ScaleModel]) -> None:
        self._scale = model

    @property
    def scale(self) -> Optional[ScaleModel]:
        return self._scale

    def calibrate(self, pairs: Optional[Iterable[Tuple[float, float]]] = None
                  ) -> Optional[ScaleModel]:
        """Calibre l'echelle sur les cotes lues (longueur dessin / valeur)."""
        if pairs is None:
            pairs = [(c.drawing_length, c.value) for c in self._cotes]
        self._scale = calibrate_scale(
            pairs, outlier_pct=self.config.scale_outlier_pct,
            min_points=self.config.min_scale_points)
        if self._scale is None:
            self._note("Echelle M4 non calibrable : moins de 2 cotes exploitables.")
        else:
            self._note(
                f"Echelle M4 : {self._scale.factor:.4f} {self.config.unit}/unite dessin "
                f"(r2={self._scale.r2:.4f}, {self._scale.n_used} cotes, "
                f"{self._scale.n_rejected} rejetee(s)).")
        return self._scale

    # -- M2 / M3a ----------------------------------------------------------
    def unit_factor(self) -> float:
        """Facteur unite de travail / unite dessin (1.0 si echelle inconnue)."""
        return self._scale.factor if self._scale is not None else 1.0

    def resolve_span(self, direction: str, pos_start: float, pos_end: float,
                     row: Optional[float] = None) -> SpanResolution:
        """Resout la portee entre deux positions (chainage M2, manquante M3a)."""
        graph = self.graph(direction)
        tol = self.config.snap_tol
        a, b = (pos_start, pos_end) if pos_start <= pos_end else (pos_end, pos_start)
        if b - a <= tol:
            return SpanResolution(None, note="Portee nulle apres snap.")
        chain = graph.chain_between(a, b, row=row)
        if chain:
            total = sum(c.value for c in chain)
            terms = [Term(sign=1, cote_id=c.id, value=c.value, label=c.label) for c in chain]
            expected = (b - a) * self.unit_factor()
            residual = abs(total - expected)
            status = Status.OK if residual <= self.config.tolerance_for(total) else Status.A_VERIFIER
            note = "chaine de " + " + ".join(f"{_num(c.value)}" for c in chain)
            if status is Status.A_VERIFIER:
                note += f" | ECART chaine/positions = {residual:.2f} {self.config.unit}"
            return SpanResolution(value=total, method=Method.SOMME, terms=terms,
                                  cote_ids=[c.id for c in chain], residual=residual,
                                  status=status, note=note)
        missing = self.resolve_missing(direction, a, b)
        if missing.value is not None:
            return missing
        pa = graph.position_of(a)
        pb = graph.position_of(b)
        if pa is not None and pb is not None:
            value = abs(pb - pa) * self.unit_factor()
            return SpanResolution(value=value, method=Method.SOMME,
                                  residual=0.0, status=Status.OK,
                                  note="positions issues du graphe (sans chaine contigue)")
        return SpanResolution(None, note="Aucune chaine de cotations ne couvre la portee.")

    def resolve_missing(self, direction: str, pos_start: float, pos_end: float
                        ) -> SpanResolution:
        """M3a : cote manquante = total - partielles connues."""
        graph = self.graph(direction)
        tol = self.config.snap_tol
        a, b = (pos_start, pos_end) if pos_start <= pos_end else (pos_end, pos_start)
        totals = [c for c in self._cotes
                  if c.direction == direction.upper() and c.covers(a, b, tol)
                  and c.hi - c.lo >= (b - a) - tol]
        if not totals:
            return SpanResolution(None, note="Pas de cote totale englobante.")
        totals.sort(key=lambda c: (c.hi - c.lo, c.id))
        total = totals[0]
        gaps, knowns = graph.gaps_within(total)
        if len(gaps) != 1:
            return SpanResolution(None,
                                  note=f"{len(gaps)} trou(s) dans la cote {total.id} : manque non unique.")
        known_sum = sum(c.value for c in knowns)
        value = total.value - known_sum
        if value <= 0:
            return SpanResolution(None, note="Reste negatif : cotation incoherente.")
        terms = [Term(sign=1, cote_id=total.id, value=total.value, label=total.label)]
        terms += [Term(sign=-1, cote_id=c.id, value=c.value, label=c.label) for c in knowns]
        return SpanResolution(
            value=value, method=Method.SOUSTRACTION, terms=terms,
            cote_ids=[total.id] + [c.id for c in knowns],
            status=Status.OK,
            note=(f"cote manquante dans {total.id} : {_num(total.value)} - "
                  + " - ".join(_num(c.value) for c in knowns)))

    def subtract_total(self, total: SpanResolution, known: Sequence[float]) -> SpanResolution:
        """M3a generique : total - partielles fournies explicitement."""
        value = (total.value or 0.0) - sum(known)
        terms = list(total.terms) + [Term(sign=-1, value=k) for k in known]
        return SpanResolution(value=value if value > 0 else None,
                              method=Method.SOUSTRACTION, terms=terms,
                              cote_ids=list(total.cote_ids),
                              status=Status.OK if value > 0 else Status.NON_RESOLU)

    # -- M3b ---------------------------------------------------------------
    def net_length(self, between_axes: float,
                   width_start: Optional[float],
                   width_end: Optional[float],
                   cote_ids: Sequence[str] = (),
                   base_terms: Sequence[Term] = ()) -> SpanResolution:
        """Longueur nette = entre-axes - demi-largeurs des appuis aux deux bouts."""
        if width_start is None or width_end is None:
            return SpanResolution(between_axes, Method.SOMME,
                                  status=Status.A_VERIFIER,
                                  note="largeur d'appui manquante : brut conserve",
                                  residual=0.0, terms=list(base_terms))
        value = between_axes - width_start / 2.0 - width_end / 2.0
        terms = list(base_terms) or [Term(sign=1, value=between_axes)]
        terms += [Term(sign=-1, value=width_start, divisor=2.0, label="appui gauche"),
                  Term(sign=-1, value=width_end, divisor=2.0, label="appui droit")]
        return SpanResolution(
            value=value, method=Method.SOUSTRACTION, terms=terms,
            cote_ids=list(cote_ids), status=Status.OK,
            note=(f"net = {_num(between_axes)} - {_num(width_start)}/2 "
                  f"- {_num(width_end)}/2"))

    # -- resolution d'un element -------------------------------------------
    def resolve_element(self, spec: ElementSpec) -> ResolvedElement:
        cfg = self.config
        res = ResolvedElement(
            element_id=spec.element_id, category=spec.category, label=spec.label,
            axis_start=spec.axis_start, axis_end=spec.axis_end, unit=cfg.unit,
        )
        notes = res.notes

        # --- M1 -----------------------------------------------------------
        direct = parse_direct(spec.declared_text, default_unit=cfg.unit)
        L = l = h = None
        L_source: Optional[str] = None
        if direct is not None:
            d = direct.convert(cfg.unit)
            L, l, h = d.interpret(spec.category)
            if L is not None:
                L_source = "DIR"
            if L is not None or l is not None or h is not None:
                res.method = Method.DIRECT
                notes.append(f"M1 DIRECT sur « {spec.declared_text.strip()} »")
        if spec.height is not None:
            h = spec.height

        # --- M2 / M3 : portee cotee ---------------------------------------
        span: Optional[SpanResolution] = None
        if spec.pos_start is not None and spec.pos_end is not None:
            span = self.resolve_span(spec.direction, spec.pos_start, spec.pos_end,
                                     row=spec.row)
        if L is None and span is not None and span.value is not None:
            L = span.value
            L_source = "SPAN"
            res.method = span.method
            res.terms = list(span.terms)
            res.cote_ids = list(span.cote_ids)
            notes.append(f"{span.method.value} portee {spec.axis_start}->{spec.axis_end} : {span.note}")
            if span.status is Status.A_VERIFIER:
                notes.append(f"ECART chaine : {span.residual:.2f} {cfg.unit} "
                             f"(cotes {_num(span.value)} vs positions)")
        elif L is not None and span is not None and span.value is not None:
            # M1 deja lu : la cote sert de recoupement (jamais d'ecrasement)
            gap = abs(span.value - L)
            tol = cfg.tolerance_for(L)
            res.secondary = SecondaryCheck(
                method=span.method, value=span.value, gap=gap,
                gap_pct=gap / L if L else 0.0, ok=gap <= tol, primary=L,
                note=f"cote {spec.axis_start}->{spec.axis_end}")
            if not res.secondary.ok:
                notes.append(f"ECART M1/M2 : {_num(L)} vs {_num(span.value)} "
                             f"(ecart {_num(gap)}, tolerance {_num(tol)})")

        # --- longueur imposee par l'appelant ------------------------------
        if L is None and spec.length is not None:
            L = spec.length
            L_source = "SPEC"
            if res.method is Method.AUCUNE:
                res.method = Method.DIRECT

        # --- mode brut / net ----------------------------------------------
        mode = "net" if cfg.is_net_type(spec.category, spec.label) else "brut"
        if span is not None and span.value is not None and mode == "net":
            if spec.width_start is None or spec.width_end is None:
                mode = "brut"
                notes.append("largeurs d'appui inconnues : mode brut retenu (net impossible)")
            else:
                net = self.net_length(span.value, spec.width_start, spec.width_end,
                                      cote_ids=span.cote_ids, base_terms=span.terms)
                if net.value is not None:
                    L = net.value
                    res.terms = list(net.terms)
                    res.cote_ids = list(net.cote_ids)
                    res.method = Method.SOUSTRACTION
                    notes.append(f"mode net : {net.note}")
        res.mode = mode

        # --- largeur imposee ----------------------------------------------
        if l is None and spec.width is not None:
            l = spec.width

        # --- M4 : mesure a l'echelle --------------------------------------
        measured_L = measured_l = None
        if self._scale is not None and spec.bbox is not None:
            w_px, h_px = float(spec.bbox[2]), float(spec.bbox[3])
            if w_px > 0:
                measured_L = self._scale.measure(w_px)
            if h_px > 0:
                measured_l = self._scale.measure(h_px)
        if L is None and measured_L is not None:
            L = measured_L
            L_source = "M4"
            res.method = Method.ECHELLE
            notes.append("M4 : portee estimee a l'echelle (statut ESTIME)")
        if l is None and measured_l is not None and spec.category in FOOTING_CATEGORIES:
            l = measured_l
        if L_source in ("DIR", "SPAN", "SPEC") and L is not None and measured_L is not None \
                and res.secondary is None:
            gap = abs(measured_L - L)
            tol = cfg.tolerance_for(L)
            res.secondary = SecondaryCheck(
                method=Method.ECHELLE, value=measured_L, gap=gap,
                gap_pct=gap / L if L else 0.0, ok=gap <= tol, primary=L,
                note="bbox x echelle")
            if not res.secondary.ok:
                notes.append(f"ECART resolution/M4 : {_num(L)} vs {_num(measured_L)}")

        # --- M5 : surface polygonale --------------------------------------
        poly_area_unit2: Optional[float] = None
        if spec.polygon:
            factor = self._scale.factor if self._scale is not None else 1.0
            poly_area_unit2 = polygon_area_with_holes(spec.polygon) * factor ** 2
            notes.append(f"M5 polygone : {poly_area_unit2:.2f} {cfg.unit}2 (facteur {factor:.4f})")

        # --- surface / volume ---------------------------------------------
        res.L, res.l, res.h = L, l, h
        if spec.category in SECTION_CATEGORIES:
            res.surface_kind = "section"
            if l is not None and h is not None:
                res.surface_m2 = cfg.m2_from_unit2(l * h)
            height = L if L is not None else None
            if res.surface_m2 is not None and height is not None:
                res.volume_m3 = res.surface_m2 * cfg.to_m(height)
        elif spec.category in LINEAR_CATEGORIES:
            res.surface_kind = None
            if L is not None and l is not None and h is not None:
                res.volume_m3 = cfg.m3_from_unit3(L * l * h)
        else:  # footing / dalle
            res.surface_kind = "plan"
            area_unit2 = None
            if L is not None and l is not None:
                area_unit2 = L * l
            if poly_area_unit2 is not None:
                if area_unit2 is not None:
                    gap = abs(poly_area_unit2 - area_unit2)
                    tol = cfg.tolerance_for(area_unit2)
                    if res.secondary is None or gap > res.secondary.gap:
                        res.secondary = SecondaryCheck(
                            method=Method.POLYGONE, value=poly_area_unit2,
                            gap=gap, gap_pct=gap / area_unit2 if area_unit2 else 0.0,
                            ok=gap <= tol, primary=area_unit2, note="polygone vs L x l")
                    if gap > tol:
                        notes.append(
                            f"ECART Lxl/POLY : {_num(area_unit2)} vs {_num(poly_area_unit2)} {cfg.unit}2")
                else:
                    area_unit2 = poly_area_unit2
                    res.method = Method.POLYGONE
            if area_unit2 is not None:
                res.surface_m2 = round(cfg.m2_from_unit2(area_unit2), cfg.round_m2)
            if res.surface_m2 is not None and h is not None:
                res.volume_m3 = round(res.surface_m2 * cfg.to_m(h), cfg.round_m3)

        # --- confiance / statut -------------------------------------------
        res.confidence = self._confidence(res)
        res.status = self._status(res, span)
        res.formula = formula_text(res.terms)
        if res.status is Status.NON_RESOLU and not res.notes:
            res.notes.append("Aucune methode n'a pu aboutir (pas de cote ni d'echelle).")
        for detail in res.notes:
            self._note(f"  {res.element_id} : {detail}")
        self._note(f"{res.element_id} {res.label}: {res.method.value} "
                   f"S={res.surface_m2} V={res.volume_m3} "
                   f"conf={res.confidence:.2f} statut={res.status.value}")
        return res

    # -- interne -----------------------------------------------------------
    def _confidence(self, res: ResolvedElement) -> float:
        """Confiance = min(base methode, ratio d'accord mesure/declare).

        Le ratio reprend le mecanisme historique de l'overlay (1.00 = accord
        parfait, seuil vert a 0.90).
        """
        conf = BASE_CONFIDENCE.get(res.method, 0.0)
        if res.secondary is not None:
            primary = res.secondary.primary
            secondary = res.secondary.value
            if primary and secondary:
                ratio = min(abs(primary), abs(secondary)) / max(abs(primary), abs(secondary))
            else:
                ratio = 0.0
            conf = min(conf, max(0.0, ratio))
        return round(max(0.0, min(1.0, conf)), 2)

    def _status(self, res: ResolvedElement, span: Optional[SpanResolution]) -> Status:
        if res.L is None and res.l is None and res.surface_m2 is None \
                and res.volume_m3 is None:
            return Status.NON_RESOLU
        if res.method is Method.ECHELLE and res.secondary is None:
            return Status.ESTIME
        if res.secondary is not None and not res.secondary.ok:
            return Status.A_VERIFIER
        if span is not None and span.status is Status.A_VERIFIER:
            return Status.A_VERIFIER
        if res.confidence < self.config.conf_threshold:
            return Status.A_VERIFIER
        return Status.OK

    # -- plan complet ------------------------------------------------------
    def resolve_plan(self, specs: Iterable[ElementSpec]) -> "PlanResolution":
        elements = [self.resolve_element(s) for s in specs]
        return PlanResolution(
            config=self.config,
            elements=elements,
            axes={d: self.graph(d).axes() for d in ("X", "Y") if self.graph(d).cotes},
            cotes=self.cotes,
            controls=self.closure_controls(),
            scale=self._scale,
            log=list(self.log),
        )

    def closure_controls(self) -> List[ClosureControl]:
        out: List[ClosureControl] = []
        for direction in ("X", "Y"):
            graph = self.graph(direction)
            if not graph.cotes:
                continue
            sol = graph.solve()
            tol = self.config.tolerance_for(
                max((c.value for c in graph.cotes), default=0.0))
            for cid, residual in sorted(sol.residuals.items()):
                if abs(residual) > tol:
                    cote = next((c for c in graph.cotes if c.id == cid), None)
                    out.append(ClosureControl(
                        chain=f"COTE {cid} ({cote.label or '?'})",
                        declared=cote.value if cote else 0.0,
                        computed=(cote.value + residual) if cote else 0.0,
                        tolerance=tol,
                        detail=("residu graphe (moindres carres) — cotation "
                                "incompatible avec le chainage")))
            # fermeture globale : cote totale (si elle existe) ou somme de la
            # chaine contigue, comparee a l'etendue convertie en unite de travail
            lo = min(c.lo for c in graph.cotes)
            hi = max(c.hi for c in graph.cotes)
            extent = (hi - lo) * self.unit_factor()
            totals = [c for c in graph.cotes
                      if c.lo <= lo + graph.snap_tol and c.hi >= hi - graph.snap_tol]
            chain = graph.chain_between(lo, hi)
            if totals:
                for total in totals:
                    out.append(ClosureControl(
                        chain=f"FERMETURE {direction}",
                        declared=total.value, computed=extent, tolerance=tol,
                        detail=f"cote totale {total.id} vs etendue des extremites"))
            elif chain:
                computed = sum(c.value for c in chain)
                out.append(ClosureControl(
                    chain=f"FERMETURE {direction}",
                    declared=computed, computed=extent, tolerance=tol,
                    detail=("somme de la chaine contigue "
                            f"({len(chain)} cotes) vs etendue des extremites")))
        return out

    def _note(self, message: str) -> None:
        self.log.append(message)
        logger.debug(message)


# ============================================================================
# 14. Plan resolu + export Excel (formules vivantes)
# ============================================================================

@dataclass
class PlanResolution:
    """Resultat complet : elements, axes, controles, echelle, journal."""

    config: ResolverConfig
    elements: List[ResolvedElement]
    axes: Dict[str, List[Axis]] = field(default_factory=dict)
    cotes: List[Cote] = field(default_factory=list)
    controls: List[ClosureControl] = field(default_factory=list)
    scale: Optional[ScaleModel] = None
    log: List[str] = field(default_factory=list)

    # -- vues --------------------------------------------------------------
    def annotations(self) -> List[OverlayAnnotation]:
        return [overlay_annotation(e) for e in self.elements]

    def by_status(self, status: Status) -> List[ResolvedElement]:
        return [e for e in self.elements if e.status is status]

    def total_surface_m2(self) -> float:
        return round(sum(e.surface_m2 or 0.0 for e in self.elements
                         if e.surface_kind == "plan"), 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unite": self.config.unit,
            "elements": [e.to_dict() for e in self.elements],
            "axes": {d: [{"nom": a.name, "position": a.position,
                          "statut": a.status.value} for a in axes]
                     for d, axes in self.axes.items()},
            "cotations": [c.to_dict() for c in self.cotes],
            "controles": [c.to_dict() for c in self.controls],
            "echelle": None if self.scale is None else self.scale.to_dict(),
            "journal": list(self.log),
        }

    def to_workbook(self, path: str | Path) -> str:
        return DimensionWorkbookWriter(self).write(path)


class DimensionWorkbookWriter:
    """Classeur Excel 5 feuilles, formules vivantes, couleurs de l'overlay."""

    SHEETS = ("Elements", "Cotations", "Axes", "Controles", "A_verifier")

    ELEMENTS_COLS = (
        ("id", 10), ("type", 12), ("libelle", 18), ("axe_debut", 9),
        ("axe_fin", 9), ("L", 9), ("l", 9), ("h", 9), ("surface_m2", 11),
        ("volume_m3", 11), ("methode", 9), ("formule", 30), ("confiance", 9),
        ("statut", 12), ("mode", 8), ("note", 52),
    )

    def __init__(self, plan: PlanResolution) -> None:
        self.plan = plan
        self.cfg = plan.config

    # -- styles ------------------------------------------------------------
    def _styles(self) -> Dict[str, Any]:
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

        thin = Side(border_style="thin", color="CBD5E0")
        return {
            "Font": Font, "PatternFill": PatternFill, "Alignment": Alignment,
            "border": Border(left=thin, right=thin, top=thin, bottom=thin),
            "title": Font(name="Calibri", size=13, bold=True, color="FFFFFF"),
            "header": Font(name="Calibri", size=10, bold=True, color="FFFFFF"),
            "normal": Font(name="Calibri", size=10),
            "formula": Font(name="Calibri", size=10, bold=True, color="0B5394"),
            "fill_title": PatternFill("solid", fgColor="1B365D"),
            "fill_header": PatternFill("solid", fgColor="2C5282"),
            "fill_ok": PatternFill("solid", fgColor="C6F6D5"),
            "fill_warn": PatternFill("solid", fgColor="FEEBC8"),
            "fill_err": PatternFill("solid", fgColor="FED7D7"),
            "fill_est": PatternFill("solid", fgColor="E2E8F0"),
            "fill_zebra": PatternFill("solid", fgColor="F7FAFC"),
        }

    def _title(self, ws, text: str, ncols: int, st: Dict[str, Any]) -> None:
        ws.cell(row=1, column=1, value=text)
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
        c = ws.cell(row=1, column=1)
        c.font = st["title"]
        c.fill = st["fill_title"]
        c.alignment = st["Alignment"](horizontal="left", vertical="center")
        ws.row_dimensions[1].height = 24

    def _header(self, ws, row: int, headers: Sequence[str], st: Dict[str, Any]) -> None:
        for k, h in enumerate(headers, start=1):
            c = ws.cell(row=row, column=k, value=h)
            c.font = st["header"]
            c.fill = st["fill_header"]
            c.border = st["border"]
            c.alignment = st["Alignment"](horizontal="center", vertical="center", wrap_text=True)

    def _status_fill(self, status: Status, st: Dict[str, Any]):
        return {
            Status.OK: st["fill_ok"],
            Status.A_VERIFIER: st["fill_warn"],
            Status.ESTIME: st["fill_est"],
            Status.NON_RESOLU: st["fill_err"],
        }[status]

    # -- ecriture ----------------------------------------------------------
    def write(self, path: str | Path) -> str:
        import openpyxl

        st = self._styles()
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        cote_rows = self._cote_rows()
        self._sheet_elements(wb, st, cote_rows)
        self._sheet_cotations(wb, st, cote_rows)
        self._sheet_axes(wb, st)
        self._sheet_controles(wb, st)
        self._sheet_a_verifier(wb, st)
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        wb.save(out)
        logger.info("Classeur dimensions ecrit : %s", out)
        return str(out)

    def _cote_rows(self) -> Dict[str, int]:
        """Ligne Excel de chaque cote dans la feuille Cotations (donnees des la ligne 3)."""
        return {c.id: i + 3 for i, c in enumerate(self.plan.cotes)}

    def _excel_formula(self, terms: Sequence[Term], cote_rows: Dict[str, int]) -> Optional[str]:
        if not terms:
            return None
        parts: List[str] = []
        for i, t in enumerate(terms):
            if t.cote_id and t.cote_id in cote_rows:
                core = f"Cotations!$C${cote_rows[t.cote_id]}"
            else:
                core = _num(t.value)
            if t.divisor not in (1, 1.0):
                core += "/" + _num(t.divisor)
            if i == 0:
                parts.append(("-" if t.sign < 0 else "") + core)
            else:
                parts.append(("-" if t.sign < 0 else "+") + core)
        return "=" + "".join(parts)

    def _sheet_elements(self, wb, st: Dict[str, Any], cote_rows: Dict[str, int]) -> None:
        ws = wb.create_sheet(self.SHEETS[0])
        headers = [c[0] for c in self.ELEMENTS_COLS]
        self._title(ws, "MÉTRÉ — RÉSOLUTION DES DIMENSIONS (dimension_resolver)", len(headers), st)
        self._header(ws, 2, headers, st)
        for k, (_name, width) in enumerate(self.ELEMENTS_COLS, start=1):
            ws.column_dimensions[chr(64 + k)].width = max(8, width)

        f_unit = self.cfg.unit_m
        for i, el in enumerate(self.plan.elements):
            r = i + 3
            fill = self._status_fill(el.status, st)
            ws.cell(row=r, column=1, value=el.element_id)
            ws.cell(row=r, column=2, value=el.category)
            ws.cell(row=r, column=3, value=el.label)
            ws.cell(row=r, column=4, value=el.axis_start)
            ws.cell(row=r, column=5, value=el.axis_end)

            # L : formule vivante des que la valeur vient de cotes
            f_L = self._excel_formula(el.terms, cote_rows)
            if f_L is not None:
                ws.cell(row=r, column=6, value=f_L).font = st["formula"]
            elif el.L is not None:
                ws.cell(row=r, column=6, value=round(el.L, self.cfg.round_len))

            if el.l is not None:
                ws.cell(row=r, column=7, value=round(el.l, self.cfg.round_len))
            if el.h is not None:
                ws.cell(row=r, column=8, value=round(el.h, self.cfg.round_len))

            # surface : formule vivante L x l (facteur d'unite en clair)
            if el.surface_m2 is not None:
                if el.surface_kind == "plan" and el.L is not None and el.l is not None:
                    ws.cell(row=r, column=9,
                            value=f"=ROUND(F{r}*G{r}*{f_unit * f_unit:.6f},{self.cfg.round_m2})"
                            ).font = st["formula"]
                elif el.surface_kind == "section" and el.l is not None and el.h is not None:
                    ws.cell(row=r, column=9,
                            value=f"=ROUND(G{r}*H{r}*{f_unit * f_unit:.6f},{self.cfg.round_m2})"
                            ).font = st["formula"]
                else:
                    ws.cell(row=r, column=9, value=round(el.surface_m2, self.cfg.round_m2))
            if el.volume_m3 is not None:
                if el.surface_m2 is not None and el.h is not None:
                    ws.cell(row=r, column=10,
                            value=f"=ROUND(I{r}*H{r}*{f_unit:.6f},{self.cfg.round_m3})"
                            ).font = st["formula"]
                elif el.L is not None and el.l is not None and el.h is not None:
                    ws.cell(row=r, column=10,
                            value=(f"=ROUND(F{r}*G{r}*H{r}*{f_unit ** 3:.8f},"
                                   f"{self.cfg.round_m3})")).font = st["formula"]
                else:
                    ws.cell(row=r, column=10, value=round(el.volume_m3, self.cfg.round_m3))

            ws.cell(row=r, column=11, value=el.method.value)
            c_formule = ws.cell(row=r, column=12, value=el.formula or "")
            c_formule.data_type = "s"          # texte, jamais evalue
            ws.cell(row=r, column=13, value=round(el.confidence, 2))
            ws.cell(row=r, column=14, value=el.status.value).fill = fill
            ws.cell(row=r, column=15, value=el.mode)
            ws.cell(row=r, column=16, value=" | ".join(el.notes))
            for k in range(1, len(headers) + 1):
                c = ws.cell(row=r, column=k)
                c.border = st["border"]
                if c.font is None or c.font.color is None:
                    c.font = st["normal"]
                c.alignment = st["Alignment"](
                    horizontal="left" if k in (3, 12, 16) else "center",
                    vertical="center", wrap_text=k in (12, 16))
        ws.freeze_panes = "A3"

    def _sheet_cotations(self, wb, st: Dict[str, Any], cote_rows: Dict[str, int]) -> None:
        ws = wb.create_sheet(self.SHEETS[1])
        headers = ["id", "direction", "valeur", "longueur_dessin", "echelle",
                   "axe_debut", "axe_fin", "source", "ecart_graphe", "statut"]
        self._title(ws, "COTATIONS LUES SUR LE PLAN", len(headers), st)
        self._header(ws, 2, headers, st)
        widths = [8, 9, 10, 14, 10, 10, 10, 9, 11, 12]
        for k, w in enumerate(widths, start=1):
            ws.column_dimensions[chr(64 + k)].width = w

        graph_X = self.plan.axes.get("X")
        graph_Y = self.plan.axes.get("Y")
        residuals: Dict[str, float] = {}
        for direction in ("X", "Y"):
            g = AxisGraph(direction, [c for c in self.plan.cotes if c.direction == direction],
                          self.cfg.snap_tol)
            g.build()
            residuals.update(g.solve().residuals)

        for i, c in enumerate(self.plan.cotes):
            r = cote_rows[c.id]
            tol = self.cfg.tolerance_for(c.value)
            resid = residuals.get(c.id, 0.0)
            ok = abs(resid) <= tol
            ws.cell(row=r, column=1, value=c.id)
            ws.cell(row=r, column=2, value=c.direction)
            ws.cell(row=r, column=3, value=round(c.value, 4))
            ws.cell(row=r, column=4, value=round(c.drawing_length, 4))
            ws.cell(row=r, column=5, value=round(c.value / c.drawing_length, 6)
                    if c.drawing_length else None)
            ws.cell(row=r, column=6, value=c.axis_start)
            ws.cell(row=r, column=7, value=c.axis_end)
            ws.cell(row=r, column=8, value=c.source)
            ws.cell(row=r, column=9, value=round(resid, 3))
            ws.cell(row=r, column=10, value="OK" if ok else "A VERIFIER").fill = (
                st["fill_ok"] if ok else st["fill_warn"])
            for k in range(1, len(headers) + 1):
                cell = ws.cell(row=r, column=k)
                cell.border = st["border"]
                if cell.font is None or cell.font.color is None:
                    cell.font = st["normal"]
                cell.alignment = st["Alignment"](horizontal="center")
        # note de bas de feuille
        last = len(self.plan.cotes) + 4
        ws.cell(row=last, column=1,
                value=f"Unite : {self.cfg.unit} — echelle = valeur / longueur dessin").font = st["normal"]
        _ = graph_X, graph_Y
        ws.freeze_panes = "A3"

    def _sheet_axes(self, wb, st: Dict[str, Any]) -> None:
        ws = wb.create_sheet(self.SHEETS[2])
        headers = ["direction", "axe", "position cumulee", "statut", "nb cotes"]
        self._title(ws, "AXES — POSITIONS CUMULEES", len(headers), st)
        self._header(ws, 2, headers, st)
        for k, w in enumerate([10, 8, 17, 12, 10], start=1):
            ws.column_dimensions[chr(64 + k)].width = w
        r = 3
        for direction in ("X", "Y"):
            for ax in self.plan.axes.get(direction, []):
                ws.cell(row=r, column=1, value=direction)
                ws.cell(row=r, column=2, value=ax.name)
                ws.cell(row=r, column=3, value=round(ax.position, 3))
                ws.cell(row=r, column=4, value=ax.status.value).fill = (
                    st["fill_ok"] if ax.determined else st["fill_est"])
                ws.cell(row=r, column=5, value=len(ax.cote_ids))
                for k in range(1, len(headers) + 1):
                    cell = ws.cell(row=r, column=k)
                    cell.border = st["border"]
                    if cell.font is None or cell.font.color is None:
                        cell.font = st["normal"]
                    cell.alignment = st["Alignment"](horizontal="center")
                r += 1
        ws.cell(row=r + 1, column=1,
                value=f"Unite : {self.cfg.unit} — axe le plus a gauche ancre a 0").font = st["normal"]
        ws.freeze_panes = "A3"

    def _sheet_controles(self, wb, st: Dict[str, Any]) -> None:
        ws = wb.create_sheet(self.SHEETS[3])
        headers = ["chaine", "total declare", "somme calculee", "ecart",
                   "tolerance", "verdict", "detail"]
        self._title(ws, "CONTROLES DE FERMETURE", len(headers), st)
        self._header(ws, 2, headers, st)
        for k, w in enumerate([34, 14, 16, 10, 11, 13, 52], start=1):
            ws.column_dimensions[chr(64 + k)].width = w
        r = 3
        for ctrl in self.plan.controls:
            ws.cell(row=r, column=1, value=ctrl.chain)
            ws.cell(row=r, column=2, value=round(ctrl.declared, 3))
            ws.cell(row=r, column=3, value=round(ctrl.computed, 3))
            ws.cell(row=r, column=4, value=round(ctrl.gap, 3))
            ws.cell(row=r, column=5, value=round(ctrl.tolerance, 3))
            ws.cell(row=r, column=6, value="OK" if ctrl.ok else "A VERIFIER").fill = (
                st["fill_ok"] if ctrl.ok else st["fill_warn"])
            ws.cell(row=r, column=7, value=ctrl.detail)
            for k in range(1, len(headers) + 1):
                cell = ws.cell(row=r, column=k)
                cell.border = st["border"]
                if cell.font is None or cell.font.color is None:
                    cell.font = st["normal"]
                cell.alignment = st["Alignment"](
                    horizontal="left" if k in (1, 7) else "center", wrap_text=k == 7)
            r += 1
        ws.freeze_panes = "A3"

    def _sheet_a_verifier(self, wb, st: Dict[str, Any]) -> None:
        ws = wb.create_sheet(self.SHEETS[4])
        headers = ["rubrique", "valeur", "commentaire"]
        self._title(ws, "HYPOTHESES ET CAS A VERIFIER", len(headers), st)
        self._header(ws, 2, headers, st)
        for k, w in enumerate([26, 22, 72], start=1):
            ws.column_dimensions[chr(64 + k)].width = w
        rows: List[Tuple[str, str, str]] = [
            ("unite de travail", self.cfg.unit,
             "toutes les longueurs des feuilles Elements/Cotations sont dans cette unite"),
            ("sorties", "m2 / m3", "conversion par facteur d'unite au carre / au cube"),
            ("mode de longueur", self.cfg.length_mode,
             "brut = axe a axe, net = entre nus (demi-largeurs retranchees)"),
            ("tolerance ecart", f"{self.cfg.tol_abs:g} {self.cfg.unit} ou {self.cfg.tol_pct:.0%}",
             "au-dela : statut A_VERIFIER, les deux valeurs sont conservees"),
            ("seuil confiance", f"{self.cfg.conf_threshold:.2f}",
             "vert si >= seuil, orange sinon (meme regle que l'overlay)"),
            ("snap extremites", f"{self.cfg.snap_tol:g} {self.cfg.unit}",
             "tolerance de regroupement des extremites de cotes"),
        ]
        if self.plan.scale is not None:
            s = self.plan.scale
            rows.append(("echelle M4", f"{s.factor:.4f} {self.cfg.unit}/unite dessin",
                         f"regression sur {s.n_used} cotes, {s.n_rejected} rejetee(s), r2={s.r2:.4f}"))
        else:
            rows.append(("echelle M4", "non calibree",
                         "aucune mesure a l'echelle : M4 indisponible"))
        for el in self.plan.elements:
            if el.status in (Status.A_VERIFIER, Status.NON_RESOLU, Status.ESTIME):
                detail = " | ".join(el.notes) or "aucune note"
                if el.secondary is not None:
                    detail += (f" | recoupement {el.secondary.method.value}="
                               f"{_num(el.secondary.value)} ecart={_num(el.secondary.gap)}")
                rows.append((f"{el.element_id} ({el.status.value})", el.label, detail))
        r = 3
        for a, b, c in rows:
            ws.cell(row=r, column=1, value=a)
            ws.cell(row=r, column=2, value=b)
            ws.cell(row=r, column=3, value=c)
            if "A_VERIFIER" in a or "ESTIME" in a:
                ws.cell(row=r, column=1).fill = st["fill_warn"]
            elif "NON_RESOLU" in a:
                ws.cell(row=r, column=1).fill = st["fill_err"]
            for k in range(1, len(headers) + 1):
                cell = ws.cell(row=r, column=k)
                cell.border = st["border"]
                if cell.font is None or cell.font.color is None:
                    cell.font = st["normal"]
                cell.alignment = st["Alignment"](
                    horizontal="left", vertical="center", wrap_text=k == 3)
            r += 1
        ws.freeze_panes = "A3"


# ============================================================================
# 15. Aide : pont depuis DrawingElement / plan_data
# ============================================================================

def specs_from_drawing_elements(elements: Iterable[Any],
                                axis_names: Optional[Dict[str, Sequence[str]]] = None
                                ) -> List[ElementSpec]:
    """Convertit des ``DrawingElement`` (bbox px) en ``ElementSpec``.

    Les positions d'axes ne sont pas deduites ici : l'appelant fournit les
    cotations et peut renseigner ``pos_start``/``pos_end`` apres coup via le
    resolveur. Cette fonction garantit surtout la lecture M1 du texte.
    """
    specs: List[ElementSpec] = []
    for i, el in enumerate(elements):
        dims = getattr(el, "dimensions", {}) or {}
        text = getattr(el, "label", "") or ""
        if dims:
            text += " " + " ".join(f"{k}={v}" for k, v in dims.items())
        category = getattr(el, "category", "autre")
        bbox = getattr(el, "bbox", None)
        specs.append(ElementSpec(
            label=getattr(el, "label", f"E{i + 1}"),
            category=category,
            declared_text=text,
            element_id=f"E{i + 1}",
            bbox=tuple(bbox) if bbox else None,
        ))
    _ = axis_names
    return specs


def dump_plan(plan: PlanResolution, json_path: str | Path) -> str:
    """Ecrit le resultat en JSON (audit / integration UI)."""
    out = Path(json_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return str(out)
