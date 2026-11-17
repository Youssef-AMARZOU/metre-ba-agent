#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/corpus/bench.py -- Benchmark de généralisation sur plans réels (C5).

Pour chaque plan du manifeste présent sur la machine :
  1. extraction complète (extract_plan_auto) ;
  2. bilan de vérifiabilité (core.guardrails) ;
  3. si une vérité terrain existe : rappel / précision des types par
     famille et justesse des sections. Une section extraite qui diffère
     de la vérité terrain est une VALEUR FAUSSE (pire qu'une valeur
     manquante : elle passe inaperçue dans le métré).

Seuls les plans role=evaluation mesurent la généralisation. Les scores du
plan de calibrage sont affichés à part, pour mémoire.

Usage :
    python tools/corpus/bench.py [--corpus-dir DIR] [--ids a,b]
                                 [--brouillon] [--inclure-lents]
Sorties : output/bench/bench_<date>.md et .json
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "corpus"
TOL_M = 0.011  # 1 cm (+ arrondi)

# champs comparés par famille ; poteaux : section sans orientation
CHAMPS = {
    "semelles": ("a", "b", "h"),
    "massifs": ("a", "b", "h"),
    "poteaux": ("a", "b"),
    "poutres": ("b", "h"),
    "longrines": ("b", "h"),
    "chainages": ("b", "h"),
}
SANS_ORIENTATION = {"semelles", "massifs", "poteaux"}


def corpus_dir_defaut() -> Path:
    env = os.environ.get("PLANBA_CORPUS_DIR")
    if env:
        return Path(env)
    return Path.home() / "Downloads"


def charger_manifest() -> list:
    with open(CORPUS / "manifest.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["plans"]


def sha16(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()[:16]


def charger_gt(entree: dict, accepter_brouillon: bool):
    nom = entree.get("verite_terrain")
    if not nom:
        return None, "aucune vérité terrain"
    doc = json.loads((CORPUS / "verite_terrain" / nom).read_text(encoding="utf-8"))
    if doc.get("statut") != "valide" and not accepter_brouillon:
        return None, f"vérité terrain '{doc.get('statut')}' (non validée)"
    return doc, doc.get("statut")


def _repere(cle: str) -> str:
    """'N1_25x40' -> 'N1' ; 'LG_25x45' -> 'LG' ; 'S1' -> 'S1'."""
    return re.split(r"[_\s]", str(cle).strip().upper(), maxsplit=1)[0]


def _vals(dims: dict, champs, sans_orientation: bool):
    vals = []
    for c in champs:
        v = dims.get(c)
        try:
            v = float(v) if v is not None else None
        except (TypeError, ValueError):
            v = None
        vals.append(v if (v is not None and v > 0) else None)
    if sans_orientation and len(vals) >= 2 and None not in vals[:2]:
        vals[:2] = sorted(vals[:2])
    return vals


def comparer(plan_data: dict, gt: dict) -> dict:
    """Compare un plan_data extrait à une vérité terrain normalisée."""
    cat = (plan_data or {}).get("catalogue_types") or {}
    res = {"familles": {}, "totaux": {}}
    tot = {"types_gt": 0, "types_trouves": 0, "types_extraits": 0,
           "valeurs_justes": 0, "valeurs_fausses": 0, "valeurs_manquantes": 0}
    for fam, types_gt in (gt.get("familles") or {}).items():
        champs = CHAMPS.get(fam)
        if not champs:
            continue
        ext = {}
        for cle, dims in (cat.get(fam) or {}).items():
            ext.setdefault(_repere(cle), []).append(dims if isinstance(dims, dict) else {})
        if fam == "massifs":
            # les massifs n'ont pas de repère stable entre BET : on compare
            # le nombre de types et leurs sections, sans le nom.
            gt_ids = list(types_gt)
            ext_ids = list(ext)
            apparies = list(zip(gt_ids, ext_ids))
        else:
            apparies = [(r, r) for r in types_gt if r.upper() in ext]
            ext_ids = list(ext)
        detail = []
        justes = fausses = manquantes = 0
        for r_gt, r_ext in apparies:
            vg = _vals(types_gt[r_gt], champs, fam in SANS_ORIENTATION)
            # plusieurs variantes extraites d'un même repère : la meilleure
            meilleure = None
            for dims in ext[r_ext.upper() if fam != "massifs" else r_ext]:
                ve = _vals(dims, champs, fam in SANS_ORIENTATION)
                score = sum(1 for g, e in zip(vg, ve)
                            if g is not None and e is not None and abs(g - e) <= TOL_M)
                if meilleure is None or score > meilleure[0]:
                    meilleure = (score, ve)
            ve = meilleure[1]
            etat = []
            for c, g, e in zip(champs, vg, ve):
                if g is None:
                    continue
                if e is None:
                    manquantes += 1
                    etat.append(f"{c}:manquant")
                elif abs(g - e) <= TOL_M:
                    justes += 1
                    etat.append(f"{c}:ok")
                else:
                    fausses += 1
                    etat.append(f"{c}:FAUX({e:g}≠{g:g})")
            detail.append({"gt": r_gt, "extrait": r_ext, "etat": etat})
        trouves = len(apparies)
        n_gt = len(types_gt)
        n_ext = len(ext_ids)
        res["familles"][fam] = {
            "types_gt": sorted(types_gt),
            "types_extraits": sorted(ext_ids),
            "rappel": round(trouves / n_gt, 3) if n_gt else None,
            "precision": round(trouves / n_ext, 3) if n_ext else None,
            "valeurs_justes": justes, "valeurs_fausses": fausses,
            "valeurs_manquantes": manquantes, "detail": detail,
        }
        tot["types_gt"] += n_gt
        tot["types_trouves"] += trouves
        tot["types_extraits"] += n_ext
        tot["valeurs_justes"] += justes
        tot["valeurs_fausses"] += fausses
        tot["valeurs_manquantes"] += manquantes
    tot["rappel"] = round(tot["types_trouves"] / tot["types_gt"], 3) if tot["types_gt"] else None
    tot["precision"] = (round(tot["types_trouves"] / tot["types_extraits"], 3)
                        if tot["types_extraits"] else None)
    n_val = tot["valeurs_justes"] + tot["valeurs_fausses"] + tot["valeurs_manquantes"]
    tot["justesse_valeurs"] = round(tot["valeurs_justes"] / n_val, 3) if n_val else None
    res["totaux"] = tot
    return res


def executer(corpus_dir: Path, ids=None, brouillon=False, lents=False) -> dict:
    from core.local_extractor import extract_plan_auto
    from core.guardrails import evaluer_verifiabilite

    rapport = {"date": dt.datetime.now().isoformat(timespec="seconds"),
               "corpus_dir": str(corpus_dir), "plans": []}
    for e in charger_manifest():
        if ids and e["id"] not in ids:
            continue
        ligne = {"id": e["id"], "role": e["role"]}
        chemin = corpus_dir / e["fichier"]
        if e.get("lent") and not lents and not ids:
            ligne["statut"] = "ignoré (lent, --inclure-lents)"
            rapport["plans"].append(ligne)
            continue
        if not chemin.exists():
            ligne["statut"] = "absent de la machine"
            rapport["plans"].append(ligne)
            continue
        if sha16(chemin) != e["sha256_16"]:
            ligne["statut"] = "fichier différent du manifeste (sha256)"
            rapport["plans"].append(ligne)
            continue
        t0 = time.time()
        plan = extract_plan_auto(str(chemin))
        ligne["duree_s"] = round(time.time() - t0, 1)
        verif = evaluer_verifiabilite(plan)
        ligne["verifiabilite"] = {k: verif[k] for k in
                                  ("statut", "taux_dimensions_lues", "nb_types",
                                   "nb_hors_bornes")}
        gt, etat_gt = charger_gt(e, brouillon)
        ligne["verite_terrain"] = etat_gt
        if gt:
            ligne["comparaison"] = comparer(plan, gt)
        ligne["statut"] = "mesuré"
        rapport["plans"].append(ligne)
    return rapport


def en_markdown(r: dict) -> str:
    out = [f"# Benchmark corpus PlanBA — {r['date']}", "",
           "| Plan | Rôle | Statut | Vérifiabilité | Rappel types | Précision | "
           "Valeurs justes | **Valeurs fausses** | Manquantes | VT |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for p in r["plans"]:
        v = p.get("verifiabilite") or {}
        c = (p.get("comparaison") or {}).get("totaux") or {}
        out.append("| {id} | {role} | {st} | {vs} | {rap} | {pre} | {j} | **{f}** | {m} | {vt} |".format(
            id=p["id"], role=p["role"], st=p.get("statut", ""),
            vs=f"{v.get('statut', '')} ({v.get('taux_dimensions_lues', 0):.0%})" if v else "",
            rap=c.get("rappel", "—"), pre=c.get("precision", "—"),
            j=c.get("valeurs_justes", "—"), f=c.get("valeurs_fausses", "—"),
            m=c.get("valeurs_manquantes", "—"), vt=p.get("verite_terrain") or "—"))
    for p in r["plans"]:
        comp = p.get("comparaison")
        if not comp:
            continue
        out += ["", f"## {p['id']} — détail", ""]
        for fam, d in comp["familles"].items():
            out.append(f"- **{fam}** : rappel {d['rappel']} · précision {d['precision']} · "
                       f"VT {d['types_gt']} · extrait {d['types_extraits']}")
            for x in d["detail"]:
                if any("FAUX" in s or "manquant" in s for s in x["etat"]):
                    out.append(f"  - {x['gt']} → {', '.join(x['etat'])}")
    out += ["", "_Seuls les plans `evaluation` avec vérité terrain `valide` "
            "mesurent la généralisation. Une valeur fausse est un défaut bloquant._"]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Benchmark corpus PlanBA")
    ap.add_argument("--corpus-dir", type=Path, default=None)
    ap.add_argument("--ids", default="")
    ap.add_argument("--brouillon", action="store_true",
                    help="utiliser aussi les vérités terrain non validées")
    ap.add_argument("--inclure-lents", action="store_true")
    ap.add_argument("--sortie", type=Path, default=ROOT / "output" / "bench")
    args = ap.parse_args(argv)
    ids = [x for x in args.ids.split(",") if x] or None
    r = executer(args.corpus_dir or corpus_dir_defaut(), ids,
                 args.brouillon, args.inclure_lents)
    args.sortie.mkdir(parents=True, exist_ok=True)
    tag = dt.datetime.now().strftime("%Y%m%d_%H%M")
    (args.sortie / f"bench_{tag}.json").write_text(
        json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    md = en_markdown(r)
    (args.sortie / f"bench_{tag}.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
