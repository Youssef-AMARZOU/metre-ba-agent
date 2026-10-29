#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/history.py -- Historique local des projets PlanBA Pro.

Stocke, dans `<sortie>/.planba_history.json`, une fiche par analyse :

    {id, nom, fichier, date, type, beton, acier, chute, statut, dossier,
     livrables: {...}, alertes: n}

100 % local : aucun envoi reseau. Le fichier reste dans le dossier `output`
de l'application (a cote de l'executable).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

HISTORY_NAME = ".planba_history.json"
MAX_ENTRIES = 30


def _history_path(output_dir: Optional[Path] = None) -> Path:
    if output_dir is None:
        from core.paths import get_output_dir
        output_dir = get_output_dir()
    return Path(output_dir) / HISTORY_NAME


def load_history(output_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = _history_path(output_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [e for e in data if isinstance(e, dict)]
    except Exception:
        return []


def save_history(entries: List[Dict[str, Any]],
                 output_dir: Optional[Path] = None) -> None:
    path = _history_path(output_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries[:MAX_ENTRIES], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def add_entry(nom: str, fichier: str, *, type_analyse: str = "ba",
              beton: float = 0.0, acier: float = 0.0, chute: float = 0.0,
              statut: str = "Terminé", dossier: str = "",
              livrables: Optional[Dict[str, str]] = None,
              alertes: int = 0, surfaces: float = 0.0,
              plan_data: Optional[Dict[str, Any]] = None,
              output_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Ajoute (ou met a jour, meme fichier + meme nom) une fiche projet.

    ``plan_data`` : instantane des donnees du plan. Il est ecrit dans
    ``<sortie>/projets/<id>/plan_data.json`` afin que la carte du menu
    « Mes plans » puisse rouvrir CE projet precis (et pas le dernier analyse).
    """
    entries = load_history(output_dir)
    fichier_nom = os.path.basename(fichier or "")
    for entry in entries:
        if entry.get("nom") == nom and entry.get("fichier") == fichier_nom:
            entry.update({
                "date": time.strftime("%Y-%m-%d %H:%M"),
                "type": type_analyse, "beton": round(beton, 3),
                "acier": round(acier, 1), "chute": round(chute, 1),
                "statut": statut, "dossier": dossier,
                "livrables": livrables or entry.get("livrables", {}),
                "alertes": int(alertes), "surfaces": round(surfaces, 2),
            })
            snap = _write_snapshot(entry.get("id", ""), plan_data, output_dir)
            if snap:
                entry["snapshot"] = snap
            save_history(entries, output_dir)
            return entry

    entry = {
        "id": uuid.uuid4().hex[:12],
        "nom": nom or "Projet sans nom",
        "fichier": fichier_nom,
        "chemin_source": fichier,
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "type": type_analyse,
        "beton": round(beton, 3),
        "acier": round(acier, 1),
        "chute": round(chute, 1),
        "surfaces": round(surfaces, 2),
        "statut": statut,
        "dossier": dossier,
        "livrables": livrables or {},
        "alerte": int(alertes),
    }
    entries.insert(0, entry)
    snap = _write_snapshot(entry["id"], plan_data, output_dir)
    if snap:
        entry["snapshot"] = snap
    save_history(entries, output_dir)
    return entry


def _write_snapshot(entry_id: str, plan_data: Optional[Dict[str, Any]],
                    output_dir: Optional[Path] = None) -> str:
    """Ecrit l'instantane d'un projet et retourne son chemin ('' si echec)."""
    if not plan_data or not entry_id:
        return ""
    try:
        base = _history_path(output_dir).parent
        folder = Path(base) / "projets" / entry_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "plan_data.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(plan_data, f, ensure_ascii=False, indent=2)
        return str(path)
    except Exception:
        return ""


def remove_entry(entry_id: str, output_dir: Optional[Path] = None) -> None:
    entries = [e for e in load_history(output_dir) if e.get("id") != entry_id]
    save_history(entries, output_dir)
