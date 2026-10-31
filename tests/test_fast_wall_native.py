# -*- coding: utf-8 -*-
"""tests/test_fast_wall_native.py — Parité du noyau natif vs Python pur.

Le noyau natif (``extractors/fast_wall_core.cpp``) est OPTIONNEL : ces tests
sont sautés (``pytest.skip``) si la bibliothèque compilée est absente.
Quand elle est présente, ils vérifient la parité avec l'implémentation Python
pure (``extractors/dotted_walls.py``) et la détection des murs en double trait.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from extractors.fast_wall_bridge import (  # noqa: E402
    load_fast_wall_or_none,
    pair_wall_faces_fast,
    reconstruct_dotted_line_fast,
    estimate_stroke_width_fast,
    deduplicate_and_merge_fast,
)
from extractors.dotted_walls import (  # noqa: E402
    extract_dot_candidates,
    reconstruct_dotted_line_from_hypothesis,
)

_lib = load_fast_wall_or_none()

pytestmark = pytest.mark.skipif(
    _lib is None,
    reason="bibliothèque native fastwall absente (scripts/build_fast_wall.py)")


def _dotted_line(n_dots=12, step=12, r=2, y=200, x0=100, shape=(400, 700)):
    ink = np.zeros(shape, np.uint8)
    for i in range(n_dots):
        cv2.circle(ink, (x0 + i * step, y), r, 255, -1)
    return ink


def test_pair_wall_faces_detecte_un_double_trait():
    # Deux faces parallèles distantes de 12 px, recouvrement total
    segs = np.array([
        [100.0, 200.0, 400.0, 200.0],
        [100.0, 212.0, 400.0, 212.0],
    ], dtype=np.float64)
    pairs = pair_wall_faces_fast(
        _lib, segs, width_range=(8.0, 16.0), angle_tol_deg=3.0)
    assert len(pairs) == 1
    assert pairs[0]["indices"] == (0, 1)
    assert 11.0 <= pairs[0]["width_px"] <= 13.0
    assert pairs[0]["overlap_ratio"] >= 0.99


def test_pair_wall_faces_rejette_les_segments_non_paralleles():
    segs = np.array([
        [100.0, 200.0, 400.0, 200.0],
        [100.0, 212.0, 400.0, 260.0],   # oblique -> angle > 3°
    ], dtype=np.float64)
    pairs = pair_wall_faces_fast(
        _lib, segs, width_range=(8.0, 16.0), angle_tol_deg=3.0)
    assert pairs == []


def test_reconstruct_dotted_line_parite_natif_python():
    ink = _dotted_line(n_dots=12, step=12)
    comps = extract_dot_candidates(ink, stroke_width_px=3.0)
    hyp = (95.0, 200.0, 240.0, 200.0)

    py = reconstruct_dotted_line_from_hypothesis(
        hyp, comps, stroke_width_px=3.0)

    centers = np.array([c["center"] for c in comps], dtype=np.float64)
    spans = np.array([c["equiv_diameter"] for c in comps], dtype=np.float64)
    nat = reconstruct_dotted_line_fast(
        _lib, centers, spans, hyp, stroke_width_px=3.0)

    assert py["segments"] and nat, "les deux implémentations doivent reconstruire"
    py_seg = py["segments"][0]["segment"]
    nat_seg = nat[0]["segment"]
    for a, b in zip(py_seg, nat_seg):
        assert a == pytest.approx(b, abs=1.0)
    assert py["segments"][0]["support_count"] == nat[0]["support_count"]


def test_estimate_stroke_width_roi_plausible():
    # NB : cv2.line(thickness=T) dessine en réalité 2T-1 px (diamant LINE_8) ;
    # pour T=5 le trait réel fait 7 px. La formule native 2*EDT-1 le restitue.
    img = np.zeros((300, 400), np.uint8)
    cv2.line(img, (50, 150), (350, 150), 255, 5)
    cv2.line(img, (200, 50), (200, 250), 255, 5)

    res = estimate_stroke_width_fast(_lib, img, use_pure_cpp_thinning=True)
    assert res["confidence"] == "candidate"
    assert res["samples"] >= 30
    assert res["width_px"] == pytest.approx(7.0, abs=1.5)
    # Les quartiles encadrent la médiane
    assert res["width_q25"] <= res["width_px"] <= res["width_q75"]


def test_estimate_stroke_width_fused_plausible():
    img = np.zeros((300, 400), np.uint8)
    cv2.line(img, (50, 150), (350, 150), 255, 5)   # 7 px réels
    res = estimate_stroke_width_fast(_lib, img, use_pure_cpp_thinning=False)
    # Sur une machine sans opencv-contrib, bascule automatiquement sur le ROI
    if res["confidence"] == "candidate":
        assert res["width_px"] == pytest.approx(7.0, abs=1.5)
    else:
        assert res["width_px"] is None


def test_deduplicate_and_merge_raccorde_les_fragments():
    raw = np.array([
        [100.0, 200.0, 398.0, 200.0],
        [402.0, 200.0, 700.0, 200.0],   # fragment colinéaire, gap de 4 px
        [105.0, 200.5, 695.0, 199.5],   # doublon du même contour
        [100.0, 218.0, 700.0, 218.0],   # face parallèle (à conserver)
    ], dtype=np.float64)
    merged = deduplicate_and_merge_fast(_lib, raw, stroke_width_px=3.0)
    # 4 segments -> 2 (un contour supérieur fusionné + la face inférieure)
    assert merged.shape[0] == 2, merged
    # Le contour supérieur fusionné s'étend de ~100 à ~700
    top = merged[np.argmin(np.abs(merged[:, 1] - 200.0))]
    assert top[0] == pytest.approx(100.0, abs=8.0)
    assert top[2] == pytest.approx(700.0, abs=8.0)
