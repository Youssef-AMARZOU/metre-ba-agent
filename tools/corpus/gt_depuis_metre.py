#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/corpus/gt_depuis_metre.py -- Brouillon de vérité terrain (C1).

Lit un métré manuel au format « Détail Quantitatif Métré » (colonnes
N° | Désignation | ... | U | N | Longueur | largeur | Hauteur | Qté)
et en tire un inventaire normalisé par famille :

    {"semelles": {"S1": {"a":1.5,"b":1.1,"h":0.4,"nombre":17,
                          "positions":["A3","A4",...]}, ...}, ...}

Le résultat est TOUJOURS écrit avec "statut": "brouillon". Un humain
(l'ingénieur métreur) doit le relire et passer le statut à "valide" :
le benchmark n'utilise que les vérités terrain validées.

Usage :
    python tools/corpus/gt_depuis_metre.py <metre.xlsx> <sortie.json> \
        --plan-id extension_archive [--feuille "Detail quontitafif fondation"]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import openpyxl

# En-têtes de blocs du gabarit -> famille. Ordre = priorité de test.
BLOCS = [
    (r"\bbeton\s+semelle\s+isolee\b", "semelles"),
    (r"\bbeton\s+semelle\s+filante\b", "semelles_filantes"),
    (r"\bbeton\s+l[o]?ngrine", "longrines"),
    (r"\bamorce", "amorces"),
    (r"^\s*poteaux?\s*$", "poteaux"),
    (r"^\s*poutres?\s*$", "poutres"),
    (r"\bchainage", "chainages"),
    (r"\bdallage|\bplancher|\bph\s+rdc|\bdalle", None),   # fin de zone
    (r"\breduire\b", None),
]
DEBUT_ZONE = r"beton\s+pour\s+beton\s+arme"
REPERE = re.compile(r"^(S\d+|P\d+|N\d+|PN|PC|LG\d*|CH\d*|M\d+|V\d+)$", re.I)
MASSIF = re.compile(r"massif\s*(\d+)\s*[*x×]\s*(\d+)\s*[*x×]\s*(\d+)", re.I)


def _norm(txt) -> str:
    txt = unicodedata.normalize("NFKD", str(txt or ""))
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", txt).strip().lower()


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _position(axe, fil):
    axe = str(axe or "").strip()
    fil = str(fil or "").strip().replace(".0", "")
    return f"{axe}{fil}" if axe or fil else ""


def extraire(path: Path, feuille: str | None = None) -> dict:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[feuille] if feuille else wb.worksheets[0]

    familles: dict = defaultdict(lambda: defaultdict(lambda: {
        "nombre": 0, "positions": [], "sections": []}))
    dans_zone = False
    famille = None

    for row in ws.iter_rows(values_only=True):
        cells = list(row[:12]) + [None] * max(0, 12 - len(row))
        textes = [_norm(c) for c in cells[:6] if isinstance(c, str)]
        ligne = " ".join(textes)
        if not dans_zone:
            if re.search(DEBUT_ZONE, ligne):
                dans_zone = True
            continue
        # changement de bloc ?
        nums = [_num(c) for c in cells[7:11]]
        if ligne and not any(n is not None for n in nums[1:]):
            for motif, fam in BLOCS:
                if re.search(motif, ligne):
                    famille = fam
                    break
            if re.match(r"^\d+$", str(cells[0] or "").strip()) and famille is None:
                break  # article suivant du métré : fin de la zone béton armé
            continue
        if famille is None:
            continue
        n, L, l_, h = nums
        if n is None or n <= 0:
            continue  # lignes « reduire » (N négatif) ignorées
        # repère et position : colonnes B..F selon le bloc
        repere, pos = None, ""
        for idx in range(1, 6):
            c = cells[idx]
            if isinstance(c, str) and REPERE.match(c.strip()):
                repere = c.strip().upper()
                rest = [x for x in cells[idx + 1:idx + 4] if x not in (None, "")]
                if famille == "longrines":
                    rest = [x for x in cells[3:5] if x not in (None, "")]
                pos = _position(*(rest + [None, None])[:2])
                break
            if isinstance(c, str):
                m = MASSIF.search(c)
                if m:
                    repere = f"MASSIF_{m.group(1)}x{m.group(2)}x{m.group(3)}"
                    pos = _position(cells[idx + 3], cells[idx + 4])
                    break
        if repere is None:
            continue
        fam = "massifs" if repere.startswith("MASSIF_") else famille
        if fam in ("amorces", "semelles_filantes"):
            continue  # pas un type métrable indépendant dans le benchmark
        e = familles[fam][repere]
        e["nombre"] += int(n)
        if pos:
            e["positions"].append(pos)
        e["sections"].append((L, l_, h))

    out: dict = {}
    for fam, types in familles.items():
        out[fam] = {}
        for rep, e in types.items():
            secs = e.pop("sections")
            if fam == "semelles" or fam == "massifs":
                a, b, h = _mode([(s[0], s[1], s[2]) for s in secs])
                e.update({"a": a, "b": b, "h": h})
            elif fam == "poteaux":
                a, b, haut = _mode([(s[0], s[1], s[2]) for s in secs])
                e.update({"a": a, "b": b, "hauteur": haut})
            else:  # poutres, longrines, chainages : L | b | h
                b, h = _mode([(s[1], s[2]) for s in secs])
                e.update({"b": b, "h": h,
                          "longueur_totale": round(sum(s[0] or 0 for s in secs), 3)})
            variantes = sorted({tuple(x for x in s if x is not None) for s in secs})
            if len({v[1:] if fam not in ("semelles", "massifs", "poteaux") else v
                    for v in variantes}) > 1:
                e["sections_variantes"] = [list(v) for v in variantes]
            out[fam][rep] = e
    return out


def _mode(valeurs):
    compte: dict = defaultdict(int)
    for v in valeurs:
        compte[v] += 1
    return max(compte.items(), key=lambda kv: kv[1])[0] if compte else (None,)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("metre")
    ap.add_argument("sortie")
    ap.add_argument("--plan-id", required=True)
    ap.add_argument("--feuille")
    args = ap.parse_args(argv)
    inventaire = extraire(Path(args.metre), args.feuille)
    doc = {
        "plan_id": args.plan_id,
        "statut": "brouillon",
        "source": Path(args.metre).name,
        "note": ("Brouillon généré automatiquement depuis le métré manuel. "
                 "À relire par l'ingénieur ; passer statut à 'valide' "
                 "pour l'utiliser dans le benchmark."),
        "familles": inventaire,
    }
    Path(args.sortie).write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    resume = {f: len(t) for f, t in inventaire.items()}
    print(f"[OK] {args.sortie} — types par famille : {resume}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
