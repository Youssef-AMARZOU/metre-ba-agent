#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extractors/dotted_walls.py — Reconstruction des murs POINTILLÉS / en tirets.

Méthode « Hough propose, RANSAC valide » (100 % numpy + OpenCV, aucune
dépendance scikit-learn ni opencv-contrib) :

    binaire (encre blanche)
        -> extraction des petits composants (points / tirets courts)
        -> HoughLinesP : hypothèses de segments dominants
        -> pour chaque hypothèse : collecte des points dans une bande étroite
           + RANSAC orthogonal adaptatif (seuil tau déduit du MAD)
        -> projection des centres, mesure des pas (p_i) et des vides (g_i)
        -> découpe aux vraies interruptions (portes / passages)
        -> validation par longueur minimale `k·t` et stabilité du motif

Tous les seuils sont dérivés de l'épaisseur de trait `t` mesurée sur le
document, jamais de constantes absolues en pixels : le même code reconstruit
un pointillé fin sur un plan 900 px comme sur un plan 3000 px.

Le résultat est un masque de segments (0/255) prêt à être fusionné dans
`PlanSurfaceExtractor.wall_mask`.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

__all__ = [
    "estimate_stroke_width",
    "extract_dot_candidates",
    "fit_orthogonal_ransac",
    "reconstruct_dotted_line_from_hypothesis",
    "reconstruct_dotted_walls",
]


# ============================================================================
# 1. Épaisseur de trait (mesure locale, pas de squelette opencv-contrib)
# ============================================================================

def estimate_stroke_width(binary: np.ndarray) -> Optional[float]:
    """Épaisseur médiane de trait, via transformée de distance.

    Les pixels « crête » (distance localement maximale) approchent le
    demi-diamètre du trait ; `2·r − 1` redonne l'épaisseur d'un trait de
    1 px (≈ 1 px). Retourne ``None`` si trop peu de pixels exploitables.
    """
    if binary is None or binary.ndim != 2 or not binary.any():
        return None
    u8 = np.where(binary > 0, 255, 0).astype(np.uint8)
    dt = cv2.distanceTransform(u8, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    ridge = np.zeros_like(dt, dtype=np.uint8)
    # maximum local dans un voisinage 3x3 (approximation robuste du squelette)
    local_max = cv2.dilate(dt, np.ones((3, 3), np.uint8))
    ridge[(dt > 0) & (dt >= local_max - 1e-6)] = 1
    radii = dt[ridge > 0]
    if radii.size < 20:
        return None
    widths = np.maximum(1.0, 2.0 * radii - 1.0)
    return float(np.median(widths))


# ============================================================================
# 2. Extraction des points / tirets courts
# ============================================================================

def extract_dot_candidates(
    binary: np.ndarray,
    stroke_width_px: float,
    min_area_factor: float = 0.4,
    max_length_factor: float = 12.0,
) -> List[Dict[str, Any]]:
    """Extrait les petits composants connexes (points, tirets courts).

    Écarte les grands murs continus : l'aire et l'envergure sont bornées par
    des multiples de l'épaisseur de trait `t`.
    """
    t = max(1.0, float(stroke_width_px))
    binary_u8 = np.where(binary > 0, 255, 0).astype(np.uint8)

    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
        binary_u8, connectivity=8
    )

    min_area = max(1.0, min_area_factor * (t ** 2))
    max_span = max_length_factor * t
    max_area = (max_span * t) * 2.5

    components: List[Dict[str, Any]] = []
    for label_idx in range(1, num_labels):
        area = float(stats[label_idx, cv2.CC_STAT_AREA])
        w = float(stats[label_idx, cv2.CC_STAT_WIDTH])
        h = float(stats[label_idx, cv2.CC_STAT_HEIGHT])
        span = math.hypot(w, h)

        if area < min_area or area > max_area or span > max_span:
            continue

        x0 = int(stats[label_idx, cv2.CC_STAT_LEFT])
        y0 = int(stats[label_idx, cv2.CC_STAT_TOP])
        sub_mask = labels[y0:y0 + int(h), x0:x0 + int(w)] == label_idx
        ys, xs = np.nonzero(sub_mask)

        # Coordonnées réelles des pixels du composant pour projeter son étendue
        pts = np.column_stack((xs + x0, ys + y0)).astype(np.float64)

        components.append({
            "id": int(label_idx),
            "center": np.array(centroids[label_idx], dtype=np.float64),
            "points": pts,
            "area": area,
            "equiv_diameter": float(2.0 * math.sqrt(area / math.pi)),
        })

    return components


# ============================================================================
# 3. Ajustement orthogonal par RANSAC adaptatif
# ============================================================================

def _fit_line_svd(points: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Ajuste une droite orthogonale (centre c, direction u, normale n) par SVD."""
    c = points.mean(axis=0)
    centered = points - c
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    u = vt[0]
    u /= np.linalg.norm(u)
    n = np.array([-u[1], u[0]], dtype=np.float64)
    return c, u, n


def fit_orthogonal_ransac(
    centers: np.ndarray,
    stroke_width_px: float,
    max_trials: int = 250,
    min_inliers: int = 4,
    rng_seed: int = 42,
) -> Optional[Dict[str, Any]]:
    """Ajuste une droite 2D par RANSAC orthogonal en deux passes.

    1. Passe conservatrice basée sur l'épaisseur de trait `t`.
    2. Estimation robuste du bruit d'alignement (MAD) puis affinement du
       seuil `tau` (borné pour ne pas absorber une ligne parallèle).
    """
    n_pts = len(centers)
    if n_pts < min_inliers:
        return None

    t = max(1.0, float(stroke_width_px))
    initial_tol = max(1.5, 1.25 * t)
    rng = np.random.default_rng(rng_seed)

    best_inliers = None
    best_count = 0
    best_residual_sum = float("inf")

    for _ in range(max_trials):
        idx = rng.choice(n_pts, size=2, replace=False)
        p1, p2 = centers[idx[0]], centers[idx[1]]
        vec = p2 - p1
        norm = np.linalg.norm(vec)
        if norm < 1e-6:
            continue

        u_cand = vec / norm
        n_cand = np.array([-u_cand[1], u_cand[0]], dtype=np.float64)

        residuals = np.abs((centers - p1) @ n_cand)
        inliers = residuals <= initial_tol
        count = int(np.count_nonzero(inliers))

        if count > best_count or (
            count == best_count
            and count > 0
            and float(residuals[inliers].sum()) < best_residual_sum
        ):
            best_count = count
            best_inliers = inliers
            best_residual_sum = float(residuals[inliers].sum())

    if best_inliers is None or best_count < min_inliers:
        return None

    # Premier ajustement SVD sur les inliers conservateurs
    c_init, u_init, n_init = _fit_line_svd(centers[best_inliers])
    signed_res = (centers[best_inliers] - c_init) @ n_init

    # Estimation robuste du bruit perpendiculaire via MAD
    med_res = float(np.median(signed_res))
    mad = float(np.median(np.abs(signed_res - med_res)))
    sigma = 1.4826 * mad

    # Seuil affiné tau borné pour ne pas absorber une ligne parallèle ou des hachures
    tau = float(np.clip(max(1.0, 2.5 * sigma), 1.0, 2.0 * t))

    all_signed_res = (centers - c_init) @ n_init
    refined_inliers = np.abs(all_signed_res) <= tau
    if int(np.count_nonzero(refined_inliers)) < min_inliers:
        refined_inliers = best_inliers

    c_final, u_final, n_final = _fit_line_svd(centers[refined_inliers])
    final_res = (centers[refined_inliers] - c_final) @ n_final

    return {
        "center": c_final,
        "direction": u_final,
        "normal": n_final,
        "inlier_mask": refined_inliers,
        "sigma_px": float(sigma),
        "ransac_threshold_px": float(tau),
        "rms_residual_px": float(np.sqrt(np.mean(final_res ** 2))),
    }


# ============================================================================
# 4. Reconstruction d'une hypothèse Hough
# ============================================================================

def reconstruct_dotted_line_from_hypothesis(
    candidate_segment: Tuple[float, float, float, float],
    components: List[Dict[str, Any]],
    stroke_width_px: float,
    k_slenderness: float = 20.0,
    min_support_dots: int = 5,
    angle_gate_deg: float = 12.0,
) -> Dict[str, Any]:
    """Valide une hypothèse Hough et reconstruit les segments pointillés.

    Collecte les petits composants dans une bande étroite, ajuste la droite
    par RANSAC orthogonal, projette centres et étendues, mesure pas et vides,
    puis découpe aux vraies interruptions (portes / passages).
    """
    t = max(1.0, float(stroke_width_px))
    x1, y1, x2, y2 = map(float, candidate_segment)
    p_start = np.array([x1, y1], dtype=np.float64)
    p_end = np.array([x2, y2], dtype=np.float64)

    seg_vec = p_end - p_start
    seg_len = float(np.linalg.norm(seg_vec))
    if seg_len < 2.0:
        return {"segments": [], "diagnostics": {"reconstruction_confidence": "rejected"}}

    u_hyp = seg_vec / seg_len
    n_hyp = np.array([-u_hyp[1], u_hyp[0]], dtype=np.float64)

    # 1. Collecte dans une bande étroite autour de l'hypothèse
    band_half_width = max(2.5, 2.5 * t)
    s_min_hyp = float(p_start @ u_hyp) - 4.0 * t
    s_max_hyp = float(p_end @ u_hyp) + 4.0 * t

    band_comps = []
    for comp in components:
        c_pt = comp["center"]
        perp_dist = abs(float((c_pt - p_start) @ n_hyp))
        proj_s = float(c_pt @ u_hyp)
        if perp_dist <= band_half_width and s_min_hyp <= proj_s <= s_max_hyp:
            band_comps.append(comp)

    if len(band_comps) < min_support_dots:
        return {
            "segments": [],
            "diagnostics": {
                "stroke_width_px": t,
                "support_count": len(band_comps),
                "reconstruction_confidence": "low_support",
            },
        }

    # 2. Ajustement RANSAC orthogonal sur les centres de la bande
    centers = np.array([c["center"] for c in band_comps], dtype=np.float64)
    ransac_res = fit_orthogonal_ransac(
        centers, stroke_width_px=t, min_inliers=min_support_dots
    )
    if ransac_res is None:
        return {
            "segments": [],
            "diagnostics": {
                "stroke_width_px": t,
                "support_count": len(band_comps),
                "reconstruction_confidence": "ransac_failed",
            },
        }

    c_line = ransac_res["center"]
    u_line = ransac_res["direction"]
    if float(u_line @ u_hyp) < 0:
        u_line = -u_line

    # Vérifie que la droite ajustée ne dévie pas vers une hachure oblique
    angle_diff = math.degrees(math.acos(np.clip(float(u_line @ u_hyp), -1.0, 1.0)))
    if angle_diff > angle_gate_deg:
        return {
            "segments": [],
            "diagnostics": {
                "stroke_width_px": t,
                "angle_deviation_deg": angle_diff,
                "reconstruction_confidence": "angle_mismatch",
            },
        }

    inlier_mask = ransac_res["inlier_mask"]
    inlier_comps = [comp for comp, keep in zip(band_comps, inlier_mask) if keep]

    # 3. Projection des centres s_i et mesure de l'étendue projetée d_i
    projections = []
    for comp in inlier_comps:
        s_center = float((comp["center"] - c_line) @ u_line)
        pts_proj = (comp["points"] - c_line) @ u_line
        d_span = max(1.0, float(pts_proj.max() - pts_proj.min()) + 1.0)
        projections.append((s_center, d_span, comp["id"]))

    # 4. Tri des projections le long de la droite
    projections.sort(key=lambda item: item[0])
    s_vals = np.array([item[0] for item in projections], dtype=np.float64)
    d_vals = np.array([item[1] for item in projections], dtype=np.float64)

    # Pas entre centres (p_i) et vide net à franchir entre les marques (g_i)
    pitches = np.diff(s_vals)
    gaps = np.maximum(0.0, pitches - 0.5 * (d_vals[:-1] + d_vals[1:]))

    # Estimation du pas de référence du motif (hors grands trous d'ouverture)
    plausible_motif_mask = (gaps > 0.25 * t) & (gaps <= 25.0 * t)
    if not np.any(plausible_motif_mask):
        return {
            "segments": [],
            "diagnostics": {
                "stroke_width_px": t,
                "support_count": len(inlier_comps),
                "reconstruction_confidence": "no_dotted_motif",
            },
        }

    ref_gaps = gaps[plausible_motif_mask]
    median_gap = float(np.median(ref_gaps))
    median_pitch = float(np.median(pitches[plausible_motif_mask]))

    # 5. Séparation des groupes aux grands trous (ouvertures, portes, passages)
    break_threshold_px = max(3.0 * median_gap + 2.0 * t, 2.2 * median_pitch)
    split_indices = np.where(gaps > break_threshold_px)[0] + 1
    groups = np.split(np.arange(len(projections)), split_indices)

    reconstructed_segments = []
    for grp in groups:
        if len(grp) < min_support_dots:
            continue

        grp_s = s_vals[grp]
        grp_d = d_vals[grp]
        grp_pitches = np.diff(grp_s)
        grp_gaps = np.maximum(0.0, grp_pitches - 0.5 * (grp_d[:-1] + grp_d[1:]))

        # Contrôle de stabilité du motif : évite de relier du texte ou du mobilier
        pitch_med = float(np.median(grp_pitches))
        pitch_iqr = float(np.quantile(grp_pitches, 0.75) - np.quantile(grp_pitches, 0.25))
        relative_dispersion = pitch_iqr / max(1.0, pitch_med)

        if relative_dispersion > 0.65:
            continue

        # Estimation locale de maxLineGap : ceil(Q90(gaps) + t)
        local_max_gap = int(math.ceil(float(np.quantile(grp_gaps, 0.90)) + t))

        # Validation par longueur minimale : L_min = max(k·t, (n_min−1)·p)
        s_start = float(grp_s[0] - 0.5 * grp_d[0])
        s_end = float(grp_s[-1] + 0.5 * grp_d[-1])
        support_length = s_end - s_start
        l_min_required = max(k_slenderness * t, (min_support_dots - 1) * pitch_med)

        if support_length < l_min_required:
            continue

        pt_start = c_line + s_start * u_line
        pt_end = c_line + s_end * u_line

        confidence = "high" if relative_dispersion <= 0.35 and len(grp) >= 6 else "candidate"

        reconstructed_segments.append({
            "segment": (
                float(pt_start[0]), float(pt_start[1]),
                float(pt_end[0]), float(pt_end[1]),
            ),
            "length_px": float(support_length),
            "min_length_required_px": float(l_min_required),
            "local_max_line_gap_px": local_max_gap,
            "dot_pitch_px": pitch_med,
            "estimated_gap_px": float(np.median(grp_gaps)),
            "pitch_dispersion_iqr_ratio": float(relative_dispersion),
            "support_count": int(len(grp)),
            "reconstruction_confidence": confidence,
        })

    overall_conf = (
        "high"
        if any(s["reconstruction_confidence"] == "high" for s in reconstructed_segments)
        else ("candidate" if reconstructed_segments else "rejected")
    )

    diagnostics = {
        "stroke_width_px": t,
        "dot_pitch_px": median_pitch,
        "estimated_gap_px": median_gap,
        "break_threshold_px": float(break_threshold_px),
        "ransac_sigma_px": ransac_res["sigma_px"],
        "ransac_threshold_px": ransac_res["ransac_threshold_px"],
        "ransac_rms_px": ransac_res["rms_residual_px"],
        "support_count": int(np.count_nonzero(inlier_mask)),
        "reconstructed_count": len(reconstructed_segments),
        "reconstruction_confidence": overall_conf,
    }

    return {
        "segments": reconstructed_segments,
        "diagnostics": diagnostics,
    }


# ============================================================================
# 5. Orchestration : binaire -> masque de murs pointillés
# ============================================================================

def _median_nearest_neighbor_pitch(centers: np.ndarray) -> Optional[float]:
    """Pas médian entre centres de points (échantillonné, O(n·m))."""
    n = len(centers)
    if n < 2:
        return None
    # échantillonne pour rester O(n) raisonnable sur les plans denses
    sample = centers if n <= 600 else centers[np.random.default_rng(0).choice(n, 600, replace=False)]
    dists = []
    for i in range(len(sample)):
        d = np.linalg.norm(sample - sample[i], axis=1)
        d = np.sort(d[d > 1e-6])
        if d.size:
            dists.append(float(d[0]))
    if not dists:
        return None
    return float(np.median(dists))


def _segment_angle_deg(seg: Tuple[float, float, float, float]) -> float:
    x1, y1, x2, y2 = seg
    ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
    # ramené dans [0, 90) : un mur vertical = 90°, horizontal = 0°
    return abs(ang) % 180.0


def _is_axis_aligned(ang: float, gate_deg: float) -> bool:
    # écart à l'axe le plus proche (0°, 90°, 180°, 270°)
    mod = ang % 90.0
    return min(mod, 90.0 - mod) <= gate_deg


def reconstruct_dotted_walls(
    binary: np.ndarray,
    stroke_width_px: Optional[float] = None,
    min_support_dots: int = 4,
    min_dots_overall: int = 6,
    axis_aligned_only: bool = True,
    axis_gate_deg: float = 6.0,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Reconstruit les murs pointillés d'un binaire (encre blanche).

    Retourne ``(masque 0/255, diagnostics)``. Le masque contient des segments
    d'épaisseur ``t`` tracés le long des droites validées ; les hypothèses
    rejetées (hachures, texte, mobilier) ne produisent rien.

    ``axis_aligned_only`` : ne conserve que les segments proches de
    l'horizontale ou de la verticale (à ``axis_gate_deg`` près). Les murs
    d'architecture sont orthogonaux ; cela écarte les hachures obliques que
    Hough pourrait proposer. Désactivable pour des plans non orthogonaux.
    """
    out = np.zeros_like(binary, dtype=np.uint8)

    if binary is None or binary.ndim != 2 or not binary.any():
        return out, {"reconstruction_confidence": "empty"}

    t = float(stroke_width_px) if stroke_width_px else (estimate_stroke_width(binary) or 2.0)
    t = max(1.0, t)

    components = extract_dot_candidates(binary, stroke_width_px=t)
    if len(components) < min_dots_overall:
        return out, {
            "stroke_width_px": t,
            "support_count": len(components),
            "reconstruction_confidence": "low_support",
        }

    # Masque des points (pour la proposition Hough)
    dot_mask = np.zeros_like(binary, dtype=np.uint8)
    for comp in components:
        pts = comp["points"].astype(int)
        dot_mask[pts[:, 1], pts[:, 0]] = 255

    pitch = _median_nearest_neighbor_pitch(
        np.array([c["center"] for c in components], dtype=np.float64)) or (4.0 * t)

    # Proposition Hough permissive : minLineLength et maxLineGap dérivés de t
    min_len = max(2, int(round(8.0 * t)))
    max_gap = max(round(2.0 * pitch), round(4.0 * t))
    lines = cv2.HoughLinesP(
        dot_mask, rho=1, theta=np.pi / 180, threshold=min_support_dots,
        minLineLength=min_len, maxLineGap=max_gap)

    if lines is None:
        return out, {
            "stroke_width_px": t,
            "support_count": len(components),
            "reconstruction_confidence": "no_hough_candidates",
        }

    # Fusion des segments Hough redondants : on garde les plus longs
    candidates = lines.reshape(-1, 4)
    diagnostics_list = []
    thickness = max(2, int(round(t)))
    for seg in candidates:
        res = reconstruct_dotted_line_from_hypothesis(
            tuple(float(v) for v in seg),
            components, stroke_width_px=t,
            min_support_dots=min_support_dots)
        for s in res["segments"]:
            if axis_aligned_only and not _is_axis_aligned(
                    _segment_angle_deg(s["segment"]), axis_gate_deg):
                continue
            x1, y1, x2, y2 = s["segment"]
            cv2.line(out, (int(round(x1)), int(round(y1))),
                     (int(round(x2)), int(round(y2))), 255, thickness)
        diagnostics_list.append(res["diagnostics"])

    conf = (
        "high" if any(d.get("reconstruction_confidence") == "high"
                      for d in diagnostics_list)
        else ("candidate" if np.any(out) else "rejected"))
    return out, {
        "stroke_width_px": t,
        "dot_pitch_px": pitch,
        "hough_candidates": int(len(candidates)),
        "support_count": len(components),
        "reconstructed_count": int(np.count_nonzero(out) > 0),
        "reconstruction_confidence": conf,
    }
