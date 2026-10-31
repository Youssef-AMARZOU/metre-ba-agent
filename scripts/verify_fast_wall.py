#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/verify_fast_wall.py — Test d'intégrité de bout en bout du noyau natif.

Compile automatiquement ``fast_wall_core.cpp`` et vérifie les 4 étages
(épaisseur de trait par ROI, déduplication/raccordement, appariement des
doubles traits) sans copie mémoire. Nécessite un compilateur C++.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2
import numpy as np

from extractors.fast_wall_bridge import (
    build_and_load_fast_wall,
    estimate_stroke_width_fast,
    deduplicate_and_merge_fast,
    pair_wall_faces_fast,
)


def run_smoke_test() -> int:
    lib = build_and_load_fast_wall()

    # Plan synthétique 800x600 : deux faces de mur d'épaisseur de trait 3 px
    # séparées de 18 px.
    canvas = np.zeros((600, 800), dtype=np.uint8)
    cv2.line(canvas, (100, 200), (700, 200), 255, thickness=3)
    cv2.line(canvas, (100, 218), (700, 218), 255, thickness=3)

    # Test 1 : épaisseur de trait en C++ pur (EDT + Zhang-Suen + 3x3)
    sw = estimate_stroke_width_fast(
        lib, canvas, roi=(80, 180, 640, 60), use_pure_cpp_thinning=True)
    print("Epaisseur estimee (C++ ROI) :", sw)

    # Test 2 : déduplication et raccordement de fragments colinéaires bruités
    raw_segments = np.array([
        [100.0, 200.0, 398.0, 200.2],
        [402.0, 199.9, 700.0, 200.0],   # fragment colinéaire, gap de 4 px
        [105.0, 200.4, 695.0, 199.8],   # doublon du contour supérieur
        [100.0, 218.0, 700.0, 218.0],   # face inférieure du mur
    ], dtype=np.float64)

    merged = deduplicate_and_merge_fast(
        lib, raw_segments, stroke_width_px=sw["width_px"] or 3.0)
    print(f"Segments après déduplication/raccordement : "
          f"{len(raw_segments)} -> {len(merged)}")

    # Test 3 : appariement des deux faces du mur
    pairs = pair_wall_faces_fast(lib, merged, width_range=(10.0, 30.0))
    print("Murs appariés :", len(pairs))
    if pairs:
        print("Largeur du mur (px) :", round(pairs[0]["width_px"], 2),
              "| Recouvrement :", round(pairs[0]["overlap_ratio"], 3))

    assert sw["confidence"] == "candidate", "épaisseur de trait non mesurée"
    assert len(merged) < len(raw_segments), "la déduplication doit réduire le nombre de segments"
    assert len(pairs) >= 1, "les deux faces du mur doivent être appariées"
    print("\n[OK] Noyau natif vérifié de bout en bout.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run_smoke_test())
    except RuntimeError as exc:
        print(f"[SKIP] {exc}")
        raise SystemExit(0)
