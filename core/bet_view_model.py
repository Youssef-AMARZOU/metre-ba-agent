#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/bet_view_model.py -- Vue modele (lecture seule) pour l'interface.

Construit, a partir d'un `plan_data` legacy + du resultat des surfaces, tout ce
que l'ecran de resultats doit afficher :

  - lignes d'avant-metre (feuille 1 du classeur certifie)
  - nomenclature d'acier (feuille 2)
  - coupe des barres de 12 m (feuille 5)
  - anomalies du gatekeeper (feuille 4)
  - totaux de synthese (beton, acier, ratio, propriete, elements)
  - repartition beton par famille / acier par diametre

Aucun calcul nouveau : tout vient de `MoroccanBETEngine` (deterministe).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

FAMILY_LABELS = {
    "semelle": "Semelles",
    "beton_proprete": "Béton de propreté",
    "poteau": "Poteaux",
    "poutre": "Poutres",
    "longrine": "Longrines",
    "chainage": "Chaînages",
    "voile": "Voiles",
    "dalle": "Dalles",
    "acier": "Acier",
}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt(value: float, decimals: int = 2) -> str:
    txt = f"{value:,.{decimals}f}"
    return txt.replace(",", " ").replace(".", ",")


def build_view_model(plan_data: Optional[dict],
                     surfaces: Optional[dict] = None,
                     projet: str = "") -> Dict[str, Any]:
    """Retourne un dict pret a peindre (jamais d'exception : dict vide)."""
    vm: Dict[str, Any] = {
        "lignes": [], "armatures": [], "stocks": {}, "anomalies": [],
        "elements": [], "familles": [], "diametres": [],
        "totaux": {"beton": 0.0, "acier": 0.0, "proprete": 0.0,
                   "ratio": 0.0, "n_elements": 0},
        "surfaces": {"lignes": [], "total": 0.0, "niveaux": []},
        "journal": [],
        "meta": {"projet": projet, "n_anomalies": 0, "n_bloquantes": 0},
    }
    try:
        from core.bet_contracts import legacy_plan_data_to_records
        from core.populate_modele_bet import (PlanBAExcelPopulator,
                                              optimize_12m_stock_bars)

        records = legacy_plan_data_to_records(plan_data or {})
        if records:
            pop = PlanBAExcelPopulator(projet or "Projet")
            lines, rows = pop.compute(records)
            stocks = optimize_12m_stock_bars(rows)
            anomalies = list(pop.engine.anomalies)

            vm["lignes"] = [{
                "prix": ln.prix_code,
                "famille": FAMILY_LABELS.get(ln.category, ln.category),
                "designation": ln.designation,
                "reperes": ", ".join(ln.grid_locations[:4]),
                "unite": ln.unite,
                "nombre": ln.nombre,
                "longueur": _f(ln.longueur),
                "largeur": _f(ln.largeur),
                "hauteur": _f(ln.hauteur),
                "quantite": _f(ln.quantite_partielle),
                "acier": _f(ln.acier_kg),
                "ratio": _f(ln.ratio_acier_kg_m3),
                "_tag": ("err" if any(a.element_label == ln.label
                                      and a.severity == "BLOQUANT"
                                      for a in anomalies) else ""),
            } for ln in lines]

            vm["armatures"] = [{
                "repere": r.element_label,
                "categorie": FAMILY_LABELS.get(r.category, r.category),
                "marque": r.bar_mark,
                "diametre": r.diameter_mm,
                "nb": r.nb_elements * r.nb_bars_per_elem,
                "longueur": _f(r.developed_length_m),
                "total": _f(r.total_length_m),
                "_tag": "",
            } for r in rows]

            vm["stocks"] = stocks
            vm["anomalies"] = [{
                "severite": a.severity,
                "categorie": "Erreur" if a.severity == "BLOQUANT"
                             else "Avertissement",
                "element": a.element_label,
                "regle": a.rule_code,
                "message": a.message,
                "_tag": "err" if a.severity == "BLOQUANT" else "warn",
            } for a in anomalies]

            beton = sum(_f(l.quantite_partielle) for l in lines)
            proprete = sum(_f(l.quantite_partielle) for l in lines
                           if l.category == "beton_proprete")
            acier = sum(_f(l.acier_kg) for l in lines)
            ba_beton = beton - proprete
            vm["totaux"] = {
                "beton": round(beton, 3),
                "beton_arme": round(ba_beton, 3),
                "proprete": round(proprete, 3),
                "acier": round(acier, 1),
                "ratio": round(acier / ba_beton, 1) if ba_beton > 0 else 0.0,
                "n_elements": len(lines),
                "n_ouvrages": len(records),
            }

            # Repartitions
            par_famille: Dict[str, float] = {}
            for ln in lines:
                if ln.category == "beton_proprete":
                    continue
                key = FAMILY_LABELS.get(ln.category, ln.category)
                par_famille[key] = par_famille.get(key, 0.0) + _f(
                    ln.quantite_partielle)
            vm["familles"] = sorted(
                [(k, round(v, 2)) for k, v in par_famille.items() if v > 0],
                key=lambda kv: -kv[1])

            from core.moroccan_bet_engine import MOROCCAN_REBAR_CATALOG
            par_dia: Dict[int, float] = {}
            for r in rows:
                masse = MOROCCAN_REBAR_CATALOG.get(r.diameter_mm, 0.0)
                par_dia[r.diameter_mm] = par_dia.get(r.diameter_mm, 0.0) + \
                    _f(r.total_length_m) * masse
            vm["diametres"] = [(f"HA{d}", round(v, 1))
                               for d, v in sorted(par_dia.items())]

            vm["elements"] = [{
                "repere": rec.label,
                "type": FAMILY_LABELS.get(rec.category, rec.category),
                "axes": ", ".join(rec.grid_axes[:3]) or "-",
                "dimensions": _dimension_text(rec.raw_dimensions),
                "quantite": (len(rec.grid_axes) or rec.qty_plan_coffrage or 1),
                "aciers": " | ".join(rec.rebar_specs[:3]) or "-",
                "niveau": rec.level,
                "_tag": "",
            } for rec in records]

            vm["meta"].update({
                "n_anomalies": len(anomalies),
                "n_bloquantes": sum(1 for a in anomalies
                                    if a.severity == "BLOQUANT"),
            })
    except Exception as exc:  # jamais bloquant : l'ecran affiche l'essentiel
        vm["meta"]["erreur_vue"] = f"{type(exc).__name__}: {exc}"
    try:
        rep = (surfaces or {}).get("reports") or []
        lignes = []
        for report in rep:
            for room in getattr(report, "rooms", []):
                conf = _f(getattr(room, "confidence", 0.0))
                lignes.append({
                    "niveau": room.level,
                    "piece": room.name,
                    "surface": _f(room.area_m2),
                    "perimetre": _f(room.perimeter_m) if getattr(
                        room, "perimeter_m", None) else "-",
                    "source": getattr(room, "method", "-"),
                    "confiance": conf,
                    "observation": " ; ".join(getattr(room, "warnings", [])[:2]),
                    "_tag": ("ok" if conf >= 0.7 else
                             "warn" if conf >= 0.4 else "err"),
                })
        total = round(sum(_f(r) for r in
                          [getattr(x, "total_area_m2", 0.0) for x in rep]), 2)
        vm["surfaces"] = {
            "lignes": lignes, "total": total,
            "niveaux": sorted({getattr(x, "level", "RDC") for x in rep}),
            "n_pieces": len(lignes),
            "xlsx": (surfaces or {}).get("xlsx"),
        }
    except Exception:
        pass

    vm["journal"] = [str(x) for x in (plan_data or {}).get(
        "_meta", {}).get("avertissements", [])]
    if projet:
        vm["meta"]["projet"] = projet
    return vm


def _dimension_text(dims: Dict[str, Any]) -> str:
    """Formate une section sans ambiguite : 120 × 120 × 30, 25 × 40 · L 4,20 m."""
    if not dims:
        return "-"

    def g(key):
        val = dims.get(key)
        return float(val) if isinstance(val, (int, float)) and val else None

    if g("A") and g("B"):
        txt = f"{g('A'):g} × {g('B'):g}"
        return txt + (f" × {g('H'):g}" if g("H") else "")
    if g("b") and g("h"):
        txt = f"{g('b'):g} × {g('h'):g}"
        if g("L"):
            txt += f" · L {g('L'):g} m"
        return txt
    if g("b") and g("H"):
        return f"{g('b'):g} × {g('H'):g}"
    parts = [f"{k} {v:g}" for k in ("H", "L", "ep")
             for v in [g(k)] if v]
    return " · ".join(parts) or "-"
