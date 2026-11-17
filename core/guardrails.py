#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/guardrails.py -- Garde-fous « zéro valeur inventée » (RG-6 / RG-7).

Deux responsabilités, sans dépendance au moteur d'extraction :

1. ``valeur_lue`` / ``A_RENSEIGNER`` : lire une dimension SANS défaut
   inventé. Une valeur absente, nulle, négative ou non numérique est
   « non lue » (None). 0 n'est jamais une dimension : un 0 donne un
   volume de 0 m3 silencieux, c'est une invention déguisée.

2. ``evaluer_verifiabilite`` : pour un plan_data extrait, dire
   honnêtement ce qui est lu, ce qui manque et ce qui est physiquement
   invraisemblable, puis classer le résultat :
       VERIFIABLE      >= 90 % des champs requis lus, rien hors bornes
       PARTIEL         >= 50 % lus
       NON_VERIFIABLE  sinon, ou aucun élément structurel trouvé

Les bornes physiques sont génériques (règles de l'art BA), jamais
calibrées sur un plan particulier. Elles ne corrigent rien : elles
signalent.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

A_RENSEIGNER = "A RENSEIGNER"

VERIFIABLE = "VERIFIABLE"
PARTIEL = "PARTIEL"
NON_VERIFIABLE = "NON_VERIFIABLE"

SEUIL_VERIFIABLE = 0.90
SEUIL_PARTIEL = 0.50

# Champs requis pour pouvoir calculer un volume, par famille.
CHAMPS_REQUIS: Dict[str, Tuple[str, ...]] = {
    "semelles": ("a", "b", "h"),
    "poteaux": ("a", "b"),
    "poutres": ("b", "h"),
    "longrines": ("b", "h"),
    "chainages": ("b", "h"),
    "massifs": ("a", "b", "h"),
}

# Bornes physiques génériques (m). Hors bornes => signalé, jamais corrigé.
BORNES_M: Dict[str, Dict[str, Tuple[float, float]]] = {
    "semelles": {"a": (0.30, 6.00), "b": (0.30, 6.00), "h": (0.15, 2.00)},
    "poteaux": {"a": (0.10, 2.00), "b": (0.10, 2.00)},
    "poutres": {"b": (0.08, 1.00), "h": (0.10, 2.50)},
    "longrines": {"b": (0.10, 1.00), "h": (0.15, 1.50)},
    "chainages": {"b": (0.07, 0.80), "h": (0.07, 0.80)},
    "massifs": {"a": (0.20, 4.00), "b": (0.20, 4.00), "h": (0.20, 4.00)},
}


def valeur_lue(source: Any, *cles: str) -> Optional[float]:
    """Première valeur numérique strictement positive parmi ``cles``.

    Retourne None si rien n'a été lu. Ne remplace JAMAIS par un défaut.
    """
    if not isinstance(source, dict):
        return None
    for cle in cles:
        val = source.get(cle)
        if isinstance(val, bool) or val is None:
            continue
        try:
            num = float(str(val).replace(",", ".")) if isinstance(val, str) else float(val)
        except (TypeError, ValueError):
            continue
        if num > 0:
            return num
    return None


def ou_a_renseigner(valeur: Optional[float]) -> Any:
    """Valeur lue, ou le marqueur texte ``A RENSEIGNER`` pour l'Excel.

    Dans une formule Excel, le texte provoque #VALEUR! : un total ne peut
    donc pas être imprimé tant qu'une dimension manque (voulu).
    """
    return valeur if valeur is not None else A_RENSEIGNER


def _evaluer_element(famille: str, repere: str, dims: Dict[str, Any]) -> Dict[str, Any]:
    requis = CHAMPS_REQUIS.get(famille, ())
    bornes = BORNES_M.get(famille, {})
    lus, manquants, zeros, hors = [], [], [], []
    for champ in requis:
        brut = dims.get(champ)
        val = valeur_lue(dims, champ)
        if val is None:
            manquants.append(champ)
            if brut is not None and not isinstance(brut, bool):
                try:
                    if float(brut) == 0:
                        zeros.append(champ)
                except (TypeError, ValueError):
                    pass
            continue
        lus.append(champ)
        lo, hi = bornes.get(champ, (None, None))
        if lo is not None and not (lo <= val <= hi):
            hors.append({"champ": champ, "valeur": val, "bornes": [lo, hi]})
    return {
        "famille": famille,
        "repere": repere,
        "requis": list(requis),
        "lus": lus,
        "manquants": manquants,
        "zeros_deguises": zeros,
        "hors_bornes": hors,
    }


def evaluer_verifiabilite(plan_data: Dict[str, Any]) -> Dict[str, Any]:
    """Bilan de vérifiabilité d'un plan_data (catalogue_types)."""
    catalogue = (plan_data or {}).get("catalogue_types") or {}
    elements: List[Dict[str, Any]] = []
    for famille in CHAMPS_REQUIS:
        for repere, dims in (catalogue.get(famille) or {}).items():
            elements.append(_evaluer_element(famille, str(repere),
                                             dims if isinstance(dims, dict) else {}))

    n_requis = sum(len(e["requis"]) for e in elements)
    n_lus = sum(len(e["lus"]) for e in elements)
    n_hors = sum(len(e["hors_bornes"]) for e in elements)
    taux = (n_lus / n_requis) if n_requis else 0.0

    if not elements:
        statut = NON_VERIFIABLE
    elif taux >= SEUIL_VERIFIABLE and n_hors == 0:
        statut = VERIFIABLE
    elif taux >= SEUIL_PARTIEL:
        statut = PARTIEL
    else:
        statut = NON_VERIFIABLE

    incomplets = [f"{e['repere']} ({', '.join(e['manquants'])})"
                  for e in elements if e["manquants"]]
    invraisemblables = [f"{e['repere']}.{h['champ']}={h['valeur']:g} m"
                        for e in elements for h in e["hors_bornes"]]
    message = (f"Vérifiabilité : {statut} — {n_lus}/{n_requis} dimensions "
               f"requises lues ({taux:.0%}) sur {len(elements)} types.")
    if not elements:
        message = ("Vérifiabilité : NON_VERIFIABLE — aucun élément structurel "
                   "dimensionnable reconnu. Ne pas utiliser ce métré.")
    if incomplets:
        message += " À renseigner : " + "; ".join(incomplets[:12])
        if len(incomplets) > 12:
            message += f" (+{len(incomplets) - 12})"
        message += "."
    if invraisemblables:
        message += " Hors bornes physiques : " + "; ".join(invraisemblables) + "."

    return {
        "statut": statut,
        "taux_dimensions_lues": round(taux, 4),
        "dimensions_lues": n_lus,
        "dimensions_requises": n_requis,
        "nb_types": len(elements),
        "nb_hors_bornes": n_hors,
        "elements": elements,
        "message": message,
    }


def annoter_verifiabilite(plan_data: Dict[str, Any]) -> Dict[str, Any]:
    """Ajoute ``_meta.verifiabilite`` et un avertissement lisible.

    Additif : ne modifie aucune valeur extraite.
    """
    if not isinstance(plan_data, dict):
        return plan_data
    bilan = evaluer_verifiabilite(plan_data)
    meta = plan_data.setdefault("_meta", {})
    if not isinstance(meta, dict):
        return plan_data
    meta["verifiabilite"] = bilan
    avert = meta.setdefault("avertissements", [])
    if isinstance(avert, list):
        avert[:] = [a for a in avert
                    if not (isinstance(a, str) and a.startswith("Vérifiabilité :"))]
        avert.append(bilan["message"])
    return plan_data


def familles_evaluees() -> Iterable[str]:
    return CHAMPS_REQUIS.keys()
