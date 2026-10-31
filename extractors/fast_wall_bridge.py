#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extractors/fast_wall_bridge.py — Pont Python zéro-copie vers le noyau natif.

Compile AUTOMATIQUEMENT ``fast_wall_core.cpp`` (g++/clang++/cl) si la
bibliothèque partagée est absente ou plus ancienne que la source, puis lie les
pointeurs ctypes. Si aucun compilateur n'est présent, ``load_fast_wall_or_none``
retourne ``None`` : l'appelant bascule alors sur l'implémentation Python pure
(``extractors.dotted_walls.py``). Aucun chemin par défaut du pipeline ne dépend
du natif.

Cinq points d'entrée exposés :
    - estimate_stroke_width_fused_c / estimate_stroke_width_roi_c ;
    - reconstruct_dotted_line_c ;
    - deduplicate_and_merge_segments_c ;
    - pair_all_wall_faces_c.
"""
from __future__ import annotations

import ctypes
import os
import pathlib
import platform
import shutil
import subprocess
from typing import Optional

import numpy as np

__all__ = [
    "StrokeWidthResultC",
    "DottedSegmentC",
    "WallPairC",
    "build_and_load_fast_wall",
    "load_fast_wall_or_none",
    "estimate_stroke_width_fast",
    "deduplicate_and_merge_fast",
    "pair_wall_faces_fast",
    "reconstruct_dotted_line_fast",
]

#: Noms candidats de la bibliothèque compilée (dépend de la plateforme).
_LIB_NAMES = ("fastwall.dll", "fastwall.so", "libfastwall.so", "libfastwall.dylib")


class StrokeWidthResultC(ctypes.Structure):
    _fields_ = [
        ("width_median_px", ctypes.c_double),
        ("width_q25_px", ctypes.c_double),
        ("width_q75_px", ctypes.c_double),
        ("samples", ctypes.c_int32),
        ("confidence_code", ctypes.c_int32),
    ]


class DottedSegmentC(ctypes.Structure):
    _fields_ = [
        ("x1", ctypes.c_double),
        ("y1", ctypes.c_double),
        ("x2", ctypes.c_double),
        ("y2", ctypes.c_double),
        ("length_px", ctypes.c_double),
        ("dot_pitch_px", ctypes.c_double),
        ("estimated_gap_px", ctypes.c_double),
        ("ransac_sigma_px", ctypes.c_double),
        ("local_max_gap_px", ctypes.c_int32),
        ("support_count", ctypes.c_int32),
        ("confidence_level", ctypes.c_int32),
    ]


class WallPairC(ctypes.Structure):
    _fields_ = [
        ("cx1", ctypes.c_double), ("cy1", ctypes.c_double),
        ("cx2", ctypes.c_double), ("cy2", ctypes.c_double),
        ("ax1", ctypes.c_double), ("ay1", ctypes.c_double),
        ("ax2", ctypes.c_double), ("ay2", ctypes.c_double),
        ("bx1", ctypes.c_double), ("by1", ctypes.c_double),
        ("bx2", ctypes.c_double), ("by2", ctypes.c_double),
        ("width_px", ctypes.c_double),
        ("overlap_px", ctypes.c_double),
        ("overlap_ratio", ctypes.c_double),
        ("angle_diff_deg", ctypes.c_double),
        ("idx_a", ctypes.c_int32),
        ("idx_b", ctypes.c_int32),
    ]


def _bind_all(lib: ctypes.CDLL) -> ctypes.CDLL:
    """Déclare les signatures des cinq points d'entrée C."""
    lib.estimate_stroke_width_fused_c.argtypes = [
        ctypes.POINTER(ctypes.c_uint8), ctypes.POINTER(ctypes.c_float),
        ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32,
        ctypes.POINTER(StrokeWidthResultC),
    ]
    lib.estimate_stroke_width_fused_c.restype = None

    lib.estimate_stroke_width_roi_c.argtypes = [
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_int32, ctypes.c_int32,
        ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32,
        ctypes.c_int32, ctypes.POINTER(StrokeWidthResultC),
    ]
    lib.estimate_stroke_width_roi_c.restype = None

    lib.reconstruct_dotted_line_c.argtypes = [
        ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double),
        ctypes.c_int32, ctypes.POINTER(ctypes.c_double),
        ctypes.c_double, ctypes.c_double, ctypes.c_int32, ctypes.c_double,
        ctypes.c_int32, ctypes.POINTER(DottedSegmentC), ctypes.c_int32,
    ]
    lib.reconstruct_dotted_line_c.restype = ctypes.c_int32

    lib.deduplicate_and_merge_segments_c.argtypes = [
        ctypes.POINTER(ctypes.c_double), ctypes.c_int32, ctypes.c_double,
        ctypes.c_double, ctypes.c_double, ctypes.POINTER(ctypes.c_double),
        ctypes.c_int32,
    ]
    lib.deduplicate_and_merge_segments_c.restype = ctypes.c_int32

    lib.pair_all_wall_faces_c.argtypes = [
        ctypes.POINTER(ctypes.c_double), ctypes.c_int32, ctypes.c_double,
        ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double,
        ctypes.POINTER(WallPairC), ctypes.c_int32,
    ]
    lib.pair_all_wall_faces_c.restype = ctypes.c_int32

    return lib


def _default_src() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent / "fast_wall_core.cpp"


def _default_lib(src_file: pathlib.Path) -> pathlib.Path:
    is_windows = platform.system().lower().startswith("win")
    return src_file.with_name("fastwall" + (".dll" if is_windows else ".so"))


def _find_prebuilt(src_file: pathlib.Path) -> Optional[pathlib.Path]:
    env = os.environ.get("PLANBA_FASTWALL_LIB")
    if env:
        p = pathlib.Path(env)
        if p.exists():
            return p
    lib_file = _default_lib(src_file)
    if lib_file.exists():
        return lib_file
    here = pathlib.Path(__file__).resolve().parent
    for name in _LIB_NAMES:
        p = here / name
        if p.exists():
            return p
    return None


def build_and_load_fast_wall(cpp_path=None) -> ctypes.CDLL:
    """Compile automatiquement ``fast_wall_core.cpp`` si nécessaire et charge la DLL.

    Lève ``RuntimeError`` si aucun compilateur C++ n'est disponible et que la
    bibliothèque n'existe pas encore.
    """
    src_file = pathlib.Path(cpp_path) if cpp_path else _default_src()
    if not src_file.exists():
        raise FileNotFoundError(f"Source C++ introuvable : {src_file}")

    prebuilt = _find_prebuilt(src_file)
    lib_file = prebuilt or _default_lib(src_file)

    needs_build = (
        not lib_file.exists()
        or (src_file.exists() and src_file.stat().st_mtime > lib_file.stat().st_mtime)
    )

    if needs_build:
        is_windows = platform.system().lower().startswith("win")
        if is_windows and shutil.which("cl"):
            cmd = ["cl", "/O2", "/fp:fast", "/std:c++17", "/LD",
                   str(src_file), f"/Fe:{lib_file}"]
        else:
            compiler = shutil.which("g++") or shutil.which("clang++")
            if not compiler:
                raise RuntimeError(
                    "Aucun compilateur C++ (g++, clang++, cl) détecté dans le "
                    "PATH : impossible de compiler fast_wall_core.cpp. Le "
                    "pipeline reste en Python pur.")
            cmd = [compiler, "-O3", "-march=native", "-ffast-math",
                   "-std=c++17", "-shared", "-fPIC", str(src_file),
                   "-o", str(lib_file)]
        subprocess.run(cmd, check=True)

    return _bind_all(ctypes.CDLL(str(lib_file)))


def load_fast_wall_or_none() -> Optional[ctypes.CDLL]:
    """Charge le noyau natif si possible, sinon ``None`` (repli Python pur).

    Ne lève jamais : sans compilateur ni bibliothèque précompilée, retourne
    ``None`` et l'appelant bascule sur ``extractors.dotted_walls``.
    """
    try:
        return build_and_load_fast_wall()
    except (RuntimeError, FileNotFoundError, OSError, subprocess.SubprocessError):
        return None


# ============================================================================
# Wrappers zéro-copie
# ============================================================================

def estimate_stroke_width_fast(
    lib: ctypes.CDLL,
    binary: np.ndarray,
    roi=None,
    use_pure_cpp_thinning: bool = False,
) -> dict:
    """Estime l'épaisseur locale des traits par pointeurs C++ (zéro copie).

    ``binary`` doit être l'IMAGE COMPLÈTE ; ``roi`` = ``(x0, y0, w, h)``
    localise la fenêtre sans réallocation.
    """
    import cv2

    if binary.dtype != np.uint8:
        binary = np.where(binary > 0, 255, 0).astype(np.uint8)
    elif not binary.flags["C_CONTIGUOUS"] and binary.strides[1] != 1:
        binary = np.ascontiguousarray(binary, dtype=np.uint8)

    h, w = binary.shape[:2]
    x0, y0, rw, rh = roi if roi is not None else (0, 0, w, h)
    res = StrokeWidthResultC()

    if use_pure_cpp_thinning or not hasattr(cv2, "ximgproc"):
        lib.estimate_stroke_width_roi_c(
            binary.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
            int(w), int(h), int(binary.strides[0]),
            int(x0), int(y0), int(rw), int(rh), ctypes.byref(res),
        )
    else:
        sub_bin = binary[y0:y0 + rh, x0:x0 + rw]
        skel = cv2.ximgproc.thinning(sub_bin)
        dist = cv2.distanceTransform(sub_bin, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
        lib.estimate_stroke_width_fused_c(
            skel.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
            dist.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            int(skel.shape[1]), int(skel.shape[0]),
            int(skel.strides[0]), int(dist.strides[0] // dist.itemsize),
            ctypes.byref(res),
        )

    if res.confidence_code == 0:
        return {"width_px": None, "confidence": "low", "samples": int(res.samples)}
    return {
        "width_px": float(res.width_median_px),
        "width_q25": float(res.width_q25_px),
        "width_q75": float(res.width_q75_px),
        "confidence": "candidate",
        "samples": int(res.samples),
    }


def deduplicate_and_merge_fast(
    lib: ctypes.CDLL,
    segments: np.ndarray,
    stroke_width_px: float,
    max_collinear_gap_px: Optional[float] = None,
    angle_tol_deg: float = 2.5,
) -> np.ndarray:
    """Déduplique et raccorde les segments colinéaires en C++ avant appariement."""
    segs_c = np.ascontiguousarray(segments, dtype=np.float64).reshape(-1, 4)
    n_in = segs_c.shape[0]
    if n_in == 0:
        return np.empty((0, 4), dtype=np.float64)

    t = max(1.0, float(stroke_width_px))
    perp_tol = 1.5 * t
    col_gap = float(max_collinear_gap_px) if max_collinear_gap_px is not None else (2.5 * t)

    out_arr = np.empty((n_in, 4), dtype=np.float64)
    n_out = lib.deduplicate_and_merge_segments_c(
        segs_c.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        int(n_in), float(perp_tol), float(col_gap), float(angle_tol_deg),
        out_arr.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), int(n_in),
    )
    return out_arr[:n_out].copy()


def pair_wall_faces_fast(
    lib: ctypes.CDLL,
    segments: np.ndarray,
    width_range,
    angle_tol_deg: float = 3.0,
    min_overlap_ratio: float = 0.6,
    max_width_variation: float = 2.0,
    max_pairs: int = 4096,
) -> list:
    """Apparie en O(M²) les contours parallèles pour reconstruire les murs à double trait."""
    segs_c = np.ascontiguousarray(segments, dtype=np.float64).reshape(-1, 4)
    n_segs = segs_c.shape[0]
    if n_segs < 2:
        return []

    out_buf = (WallPairC * max_pairs)()
    count = lib.pair_all_wall_faces_c(
        segs_c.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        int(n_segs), float(width_range[0]), float(width_range[1]),
        float(angle_tol_deg), float(min_overlap_ratio), float(max_width_variation),
        out_buf, int(max_pairs),
    )

    results = []
    for k in range(count):
        p = out_buf[k]
        results.append({
            "centerline": np.array([[p.cx1, p.cy1], [p.cx2, p.cy2]], dtype=np.float64),
            "face_a": np.array([[p.ax1, p.ay1], [p.ax2, p.ay2]], dtype=np.float64),
            "face_b": np.array([[p.bx1, p.by1], [p.bx2, p.by2]], dtype=np.float64),
            "width_px": float(p.width_px),
            "overlap_px": float(p.overlap_px),
            "overlap_ratio": float(p.overlap_ratio),
            "angle_difference_deg": float(p.angle_diff_deg),
            "indices": (int(p.idx_a), int(p.idx_b)),
        })
    return results


def reconstruct_dotted_line_fast(
    lib: ctypes.CDLL,
    comp_centers: np.ndarray,
    comp_spans: np.ndarray,
    hyp_segment,
    stroke_width_px: float,
    k_slenderness: float = 20.0,
    min_support_dots: int = 5,
    angle_gate_deg: float = 12.0,
    max_ransac_trials: int = 250,
    max_out_segments: int = 64,
) -> list:
    """Appelle le noyau natif de reconstruction de pointillés (zéro copie)."""
    centers_c = np.ascontiguousarray(comp_centers, dtype=np.float64)
    spans_c = np.ascontiguousarray(comp_spans, dtype=np.float64)
    hyp_c = np.ascontiguousarray(hyp_segment, dtype=np.float64)
    n = centers_c.shape[0]
    if n < min_support_dots:
        return []

    out_buf = (DottedSegmentC * max_out_segments)()
    count = lib.reconstruct_dotted_line_c(
        centers_c.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        spans_c.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        n,
        hyp_c.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        float(stroke_width_px), float(k_slenderness), int(min_support_dots),
        float(angle_gate_deg), int(max_ransac_trials), out_buf, max_out_segments,
    )

    results = []
    for k in range(count):
        p = out_buf[k]
        results.append({
            "segment": (p.x1, p.y1, p.x2, p.y2),
            "length_px": p.length_px,
            "dot_pitch_px": p.dot_pitch_px,
            "estimated_gap_px": p.estimated_gap_px,
            "ransac_sigma_px": p.ransac_sigma_px,
            "local_max_gap_px": p.local_max_gap_px,
            "support_count": p.support_count,
            "confidence_level": p.confidence_level,
        })
    return results
