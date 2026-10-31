# -*- coding: utf-8 -*-
"""tests/test_dotted_walls.py -- Reconstruction « Hough propose / RANSAC valide ».

Verrouille le module opt-in `extractors/dotted_walls.py` (murs pointillés
obliques ou espacés). Ce module n'est PAS branché par défaut dans
`wall_mask` : mesuré sur les plans orthogonaux, il régresse face au
`_link_dot_chains` déterministe. Il reste disponible pour les pointillés
non orthogonaux.
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

from extractors.dotted_walls import (  # noqa: E402
    estimate_stroke_width,
    extract_dot_candidates,
    fit_orthogonal_ransac,
    reconstruct_dotted_line_from_hypothesis,
    reconstruct_dotted_walls,
)


def _dotted_line(n_dots=12, step=12, r=2, y=200, x0=100, shape=(400, 700)):
    ink = np.zeros(shape, np.uint8)
    for i in range(n_dots):
        cv2.circle(ink, (x0 + i * step, y), r, 255, -1)
    return ink


def test_estimate_stroke_width_proche_du_trait():
    ink = np.zeros((200, 400), np.uint8)
    cv2.line(ink, (50, 100), (350, 100), 255, 3)   # trait de 3 px
    t = estimate_stroke_width(ink)
    assert t is not None
    assert 2.0 <= t <= 5.0


def test_extract_dot_candidates_ecarte_les_gros_murs():
    ink = _dotted_line()
    # un gros mur plein en bas (doit être écarté)
    cv2.rectangle(ink, (40, 300), (400, 340), 255, -1)
    comps = extract_dot_candidates(ink, stroke_width_px=3.0)
    # seuls les points du pointillé sont retenus
    ids = {c["id"] for c in comps}
    assert len(comps) >= 10
    # le rectangle plein n'est pas un "petit composant"
    assert all(c["area"] < 4000 for c in comps)


def test_fit_orthogonal_ransac_sur_une_droite():
    rng = np.random.default_rng(0)
    x = np.linspace(0, 120, 12)
    y = 0.3 * rng.standard_normal(12)            # faible bruit perpendiculaire
    centers = np.column_stack((x, 100.0 + y)).astype(np.float64)
    res = fit_orthogonal_ransac(centers, stroke_width_px=3.0)
    assert res is not None
    # direction proche de l'horizontale
    u = res["direction"]
    assert abs(float(np.arctan2(u[1], u[0]))) < 0.15
    assert res["rms_residual_px"] < 2.0


def test_reconstruct_hypothese_alignee():
    ink = _dotted_line(n_dots=12, step=12)
    comps = extract_dot_candidates(ink, stroke_width_px=3.0)
    # hypothèse Hough le long du pointillé
    res = reconstruct_dotted_line_from_hypothesis(
        (95.0, 200.0, 240.0, 200.0), comps, stroke_width_px=3.0)
    assert res["segments"], res["diagnostics"]
    seg = res["segments"][0]
    assert seg["reconstruction_confidence"] in ("high", "candidate")
    assert seg["support_count"] >= 5
    assert seg["length_px"] >= 100


def test_reconstruct_dotted_walls_h_v():
    ink = np.zeros((400, 700), np.uint8)
    # 1 mur horizontal + 1 mur vertical pointillés
    for i in range(20):
        cv2.circle(ink, (100 + i * 12, 200), 2, 255, -1)
        cv2.circle(ink, (500, 100 + i * 12), 2, 255, -1)
    mask, diag = reconstruct_dotted_walls(ink)
    assert int((mask > 0).sum()) > 0
    assert diag["reconstruction_confidence"] in ("high", "candidate")


def test_reconstruct_dotted_walls_rejette_l_oblique():
    # Hachures obliques : avec axis_aligned_only=True, elles ne produisent rien
    ink = np.zeros((400, 700), np.uint8)
    for i in range(20):
        cv2.circle(ink, (100 + i * 10, 300 - i * 10), 2, 255, -1)
    mask, _ = reconstruct_dotted_walls(ink, axis_aligned_only=True, axis_gate_deg=6.0)
    assert int((mask > 0).sum()) == 0
