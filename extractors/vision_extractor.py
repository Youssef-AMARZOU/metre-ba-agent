#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extractors/vision_extractor.py -- Moteur Computer Vision hybride PlanBA.

Pipeline 4 couches, 100% freemium / open-source :
  1. Diagnostic & pretraitement : PyMuPDF 300 DPI + OpenCV (detection des
     scans et des polices AutoCAD SHX explosees en milliers de vecteurs).
  2. Zonage morphologique : tableaux / nomenclatures (grilles H+V),
     contours structurels fermes (poteaux, semelles), bulles d'axes.
  3. Extraction multimodale : OCR local (routeur) + graphe spatial 2D
     (scipy cKDTree) + VLM (Ollama / Groq free) sur crops ROI uniquement.
  4. Anti-hallucination : controle du ratio b/h lu vs ratio pixel du
     contour (corrige 25x400 -> 0.25x0.40) + overlay d'audit PNG.

Sortie : `core.bet_contracts.DrawingElement` (jamais de volume ni de poids).
"""
from __future__ import annotations

import logging
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.bet_contracts import (
    LABEL_PREFIX_TO_CATEGORY,
    RE_AXIS_LETTER,
    RE_AXIS_NUMBER,
    RE_LABEL,
    RE_LONG_BAR,
    RE_SECTION,
    RE_STIRRUP,
    DrawingElement,
    ExtractedPlanRecord,
)

logger = logging.getLogger(__name__)

__all__ = ["DrawingElement", "ExtractedPlanRecord", "PlanBAVisionExtractor"]

BBox = Tuple[int, int, int, int]  # (x, y, w, h) pixels

# Seuils de bascule Vision (polices SHX explosees / scans)
VISION_MIN_WORDS = 15
VISION_MIN_DRAWINGS = 1500
VISION_DRAWINGS_PER_WORD = 25.0

# Plages plausibles d'une section d'ouvrage (m) pour la correction decimale
SECTION_MIN_M, SECTION_MAX_M = 0.10, 3.00


def _spatial_index(points):
    """cKDTree (scipy) ou index numpy equivalent (exe PyInstaller sans scipy)."""
    import numpy as np
    pts = np.asarray(points, dtype=float)
    try:
        from scipy.spatial import cKDTree
        return cKDTree(pts)
    except Exception:  # pragma: no cover - chemin de secours sans scipy
        pass

    class _NumpyIndex:
        def __init__(self, data):
            self.data = data

        def query(self, pt, k=1, distance_upper_bound=float("inf")):
            d = np.linalg.norm(self.data - np.asarray(pt, dtype=float), axis=1)
            order = np.argsort(d)[:k]
            dist = d[order].astype(float)
            idx = order.astype(int)
            mask = dist > distance_upper_bound
            dist[mask] = np.inf
            idx[mask] = len(self.data)
            if k == 1:
                return float(dist[0]), int(idx[0])
            return dist, idx

        def query_ball_point(self, pt, r):
            d = np.linalg.norm(self.data - np.asarray(pt, dtype=float), axis=1)
            return [int(i) for i in np.nonzero(d <= r)[0]]

    return _NumpyIndex(pts)


class PlanBAVisionExtractor:
    """Extracteur CV pour plans BA scannes, SHX explosés ou hybrides."""

    def __init__(
        self,
        dpi: int = 300,
        max_side_px: int = 8000,
        ocr_router: Optional[Any] = None,
        vlm_router: Optional[Any] = None,
        groq_client: Optional[Any] = None,
        max_dist_px: float = 280.0,
        tile_px: int = 2400,
        tile_overlap_px: int = 120,
    ):
        self.dpi = dpi
        self.max_side_px = max_side_px
        self._ocr = ocr_router
        self._vlm = vlm_router
        self.groq_client = groq_client  # compat : client SDK OpenAI-like optionnel
        self.max_dist_px = max_dist_px
        self.tile_px = tile_px
        self.tile_overlap_px = tile_overlap_px
        self.last_scale: float = dpi / 72.0
        self.re_label = RE_LABEL
        self.re_section = RE_SECTION
        self.re_rebar = RE_LONG_BAR
        self.re_stirrup = RE_STIRRUP

    # ------------------------------------------------------------------
    # Routeurs paresseux
    # ------------------------------------------------------------------
    @property
    def ocr(self):
        if self._ocr is None:
            from extractors.ocr_router import LocalOCRRouter
            self._ocr = LocalOCRRouter()
        return self._ocr

    @property
    def vlm(self):
        if self._vlm is None:
            from extractors.vlm_router import VLMRouter
            self._vlm = VLMRouter()
        return self._vlm

    # ==================================================================
    # COUCHE 1 : Diagnostic & rendu
    # ==================================================================
    @staticmethod
    def needs_vision_fallback(page) -> bool:
        """True si page scannee (< 15 mots) ou texte SHX explose
        (> 1500 vecteurs et ratio vecteurs/mots > 25)."""
        try:
            words = page.get_text("words")
        except Exception:
            words = []
        if len(words) < VISION_MIN_WORDS:
            return True
        try:
            n_draw = len(page.get_drawings())
        except Exception:
            return False
        return (n_draw > VISION_MIN_DRAWINGS
                and (n_draw / max(len(words), 1)) > VISION_DRAWINGS_PER_WORD)

    def render_page_high_res(self, page):
        """Page PDF -> matrice OpenCV BGR a `dpi` (cap `max_side_px`)."""
        import cv2
        import fitz
        import numpy as np

        zoom = self.dpi / 72.0
        rect = page.rect
        longest_pt = max(rect.width, rect.height)
        if longest_pt * zoom > self.max_side_px:
            zoom = self.max_side_px / longest_pt
        self.last_scale = zoom
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False,
                              colorspace=fitz.csRGB)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = img[:, :, :3]
        return cv2.cvtColor(np.ascontiguousarray(img), cv2.COLOR_RGB2BGR)

    @staticmethod
    def preprocess(img_bgr, deskew: bool = True):
        """Niveaux de gris + binarisation adaptative (+ redressement leger)."""
        import cv2
        import numpy as np

        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        if deskew:
            edges = cv2.Canny(gray, 50, 150)
            lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 200,
                                    minLineLength=gray.shape[1] // 4,
                                    maxLineGap=20)
            if lines is not None and len(lines) >= 5:
                angles = []
                for x1, y1, x2, y2 in lines[:, 0]:
                    a = math.degrees(math.atan2(y2 - y1, x2 - x1))
                    if abs(a) < 10:
                        angles.append(a)
                if angles:
                    angle = float(np.median(angles))
                    if 0.2 < abs(angle) < 10:
                        h, w = gray.shape
                        M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
                        gray = cv2.warpAffine(gray, M, (w, h),
                                              flags=cv2.INTER_LINEAR,
                                              borderValue=255)
        binary = cv2.adaptiveThreshold(
            ~gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, -2)
        return gray, binary

    # ==================================================================
    # COUCHE 2 : Zonage morphologique
    # ==================================================================
    def detect_table_and_schedule_rois(self, img_bgr) -> List[BBox]:
        """Grilles H/V par morphologie : bbox des tableaux/nomenclatures
        (w > 250, h > 120, >= 8 intersections)."""
        import cv2

        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        thresh = cv2.adaptiveThreshold(
            ~gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, -2)

        h_scale = max(30, img_bgr.shape[1] // 80)
        v_scale = max(30, img_bgr.shape[0] // 80)
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_scale, 1))
        v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_scale))
        h_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, h_kernel)
        v_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, v_kernel)

        table_mask = cv2.add(h_lines, v_lines)
        joints = cv2.bitwise_and(h_lines, v_lines)
        # Dilatation legere pour relier les cellules d'un meme tableau
        table_mask = cv2.dilate(table_mask, cv2.getStructuringElement(
            cv2.MORPH_RECT, (3, 3)), iterations=1)

        contours, _ = cv2.findContours(table_mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        rois: List[BBox] = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if w > 250 and h > 120:
                n_joints, _ = cv2.connectedComponents(joints[y:y + h, x:x + w])
                if n_joints - 1 >= 8:  # composante 0 = fond
                    rois.append((int(x), int(y), int(w), int(h)))
        return sorted(rois, key=lambda b: (b[1], b[0]))

    def detect_structural_elements_contours(self, img_bgr) -> List[BBox]:
        """Rectangles fermes convexes (poteaux / semelles en vue en plan)."""
        import cv2

        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        edges = cv2.Canny(blurred, 50, 150)
        edges = cv2.dilate(edges, cv2.getStructuringElement(
            cv2.MORPH_RECT, (3, 3)), iterations=1)
        contours, _ = cv2.findContours(edges, cv2.RETR_TREE,
                                       cv2.CHAIN_APPROX_SIMPLE)
        boxes: List[BBox] = []
        for cnt in contours:
            peri = cv2.arcLength(cnt, True)
            if peri < 80:
                continue
            approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
            if len(approx) == 4 and cv2.isContourConvex(approx):
                x, y, w, h = cv2.boundingRect(approx)
                ar = float(w) / max(h, 1)
                if 25 <= w <= 800 and 25 <= h <= 800 and 0.2 <= ar <= 5.0:
                    boxes.append((int(x), int(y), int(w), int(h)))
        return self._dedupe_boxes(boxes)

    @staticmethod
    def _dedupe_boxes(boxes: List[BBox], iou_thr: float = 0.7) -> List[BBox]:
        kept: List[BBox] = []
        for b in sorted(boxes, key=lambda b: b[2] * b[3], reverse=True):
            x, y, w, h = b
            dup = False
            for k in kept:
                kx, ky, kw, kh = k
                ix = max(0, min(x + w, kx + kw) - max(x, kx))
                iy = max(0, min(y + h, ky + kh) - max(y, ky))
                inter = ix * iy
                union = w * h + kw * kh - inter
                if union > 0 and inter / union >= iou_thr:
                    dup = True
                    break
            if not dup:
                kept.append(b)
        return kept

    def detect_grid_axis_bubbles(self, img_bgr,
                                 ocr_items: Sequence[Dict[str, Any]]
                                 ) -> List[Dict[str, Any]]:
        """Bulles d'axes : HoughCircles + token OCR `^[A-Z]$|^\\d{1,2}$`
        situe dans le cercle. Retourne [{'axis','kind','center','r'}]."""
        import cv2
        import numpy as np

        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.medianBlur(gray, 5)
        scale = self.last_scale if self.last_scale else self.dpi / 72.0
        r_min = max(8, int(8 * scale / 4.17 * 2))     # ~ 4 mm de rayon
        r_max = max(r_min + 5, int(r_min * 6))
        circles = cv2.HoughCircles(
            gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=r_min * 2,
            param1=120, param2=30, minRadius=r_min, maxRadius=r_max)
        if circles is None:
            return []
        circles = np.round(circles[0]).astype(int)

        tokens = []
        for it in ocr_items:
            t = str(it["text"]).strip().upper()
            if RE_AXIS_LETTER.match(t) or RE_AXIS_NUMBER.match(t):
                x, y, w, h = it["bbox"]
                tokens.append((t, x + w / 2.0, y + h / 2.0))
        bubbles: List[Dict[str, Any]] = []
        for cx, cy, r in circles:
            best, best_d = None, float("inf")
            for t, tx, ty in tokens:
                d = math.hypot(tx - cx, ty - cy)
                if d <= r * 0.9 and d < best_d:
                    best, best_d = t, d
            if best is None:
                continue
            bubbles.append({
                "axis": best,
                "kind": "letter" if RE_AXIS_LETTER.match(best) else "number",
                "center": (float(cx), float(cy)),
                "r": float(r),
            })
        # Une meme bulle ne porte qu'un axe : dedoublonnage par centre
        uniq: Dict[Tuple[int, int], Dict[str, Any]] = {}
        for b in bubbles:
            key = (int(b["center"][0] // 10), int(b["center"][1] // 10))
            uniq.setdefault(key, b)
        return list(uniq.values())

    @staticmethod
    def assign_grid_axes(points: Sequence[Tuple[float, float]],
                         bubbles: Sequence[Dict[str, Any]],
                         max_dist_px: float = 4000.0) -> List[List[str]]:
        """Pour chaque point, 'LETTRE-NUMERO' des axes les plus proches.
        L'orientation (lettres en x ou en y) est deduite de la dispersion
        des bulles."""
        import numpy as np

        letters = [b for b in bubbles if b["kind"] == "letter"]
        numbers = [b for b in bubbles if b["kind"] == "number"]
        if not letters and not numbers:
            return [[] for _ in points]

        def axis_of(group):
            if len(group) < 2:
                return 0  # par defaut : lettres sur x
            xs = np.array([g["center"][0] for g in group])
            ys = np.array([g["center"][1] for g in group])
            return 0 if xs.std() >= ys.std() else 1

        letter_axis = axis_of(letters) if letters else 0
        number_axis = axis_of(numbers) if numbers else 1 - letter_axis
        if letters and numbers and letter_axis == number_axis:
            number_axis = 1 - letter_axis

        def nearest(group, axis, pt):
            best, best_d = None, max_dist_px
            for g in group:
                d = abs(g["center"][axis] - pt[axis])
                if d < best_d:
                    best, best_d = g["axis"], d
            return best

        out: List[List[str]] = []
        for pt in points:
            lt = nearest(letters, letter_axis, pt) if letters else None
            nb = nearest(numbers, number_axis, pt) if numbers else None
            if lt and nb:
                out.append([f"{lt}-{nb}"])
            elif lt or nb:
                out.append([lt or nb])
            else:
                out.append([])
        return out

    # ==================================================================
    # COUCHE 3 : OCR + graphe spatial
    # ==================================================================
    def ocr_page(self, img_bgr) -> List[Dict[str, Any]]:
        """OCR par tuiles (haute resolution preservee) + dedoublonnage."""
        H, W = img_bgr.shape[:2]
        step = max(200, self.tile_px - self.tile_overlap_px)
        items: List[Dict[str, Any]] = []
        for y in range(0, H, step):
            for x in range(0, W, step):
                tile = img_bgr[y:min(y + self.tile_px, H),
                               x:min(x + self.tile_px, W)]
                if tile.shape[0] < 20 or tile.shape[1] < 20:
                    continue
                items.extend(self.ocr.recognize(tile, offset=(x, y)))
        return self._dedupe_ocr(items)

    @staticmethod
    def _dedupe_ocr(items: List[Dict[str, Any]], tol_px: float = 12.0
                    ) -> List[Dict[str, Any]]:
        kept: List[Dict[str, Any]] = []
        for it in sorted(items, key=lambda i: -float(i.get("conf", 0))):
            x, y, w, h = it["bbox"]
            cx, cy = x + w / 2.0, y + h / 2.0
            dup = False
            for k in kept:
                kx, ky, kw, kh = k["bbox"]
                if (k["text"] == it["text"]
                        and abs(kx + kw / 2.0 - cx) <= tol_px
                        and abs(ky + kh / 2.0 - cy) <= tol_px):
                    dup = True
                    break
            if not dup:
                kept.append(it)
        return kept

    @staticmethod
    def _section_dims(groups: Sequence[Optional[str]], category: str
                      ) -> Dict[str, float]:
        """'25x40' -> {b,h} ; '140x140x40' -> {A,B,H} (cm -> m)."""
        vals = [float(g) / 100.0 for g in groups if g is not None]
        if len(vals) == 2:
            if category == "semelle":
                return {"A": vals[0], "B": vals[1]}
            return {"b": vals[0], "h": vals[1]}
        if len(vals) == 3:
            semelle_like = vals[0] >= 0.60 and vals[1] >= 0.60
            if category in ("poutre", "longrine", "chainage", "poteau"):
                # '150x150x40' pres d'une etiquette couplee (S1,P1) est la
                # semelle, pas le poteau : on ne l'attribue pas au poteau.
                if semelle_like:
                    return {}
                return {"b": vals[0], "h": vals[1], "L": vals[2]}
            return {"A": vals[0], "B": vals[1], "H": vals[2]}
        return {}

    @staticmethod
    def geometric_sanity_check(dims: Dict[str, float],
                               contour_wh: Optional[Tuple[float, float]],
                               tol_factor: float = 2.5
                               ) -> Tuple[Dict[str, float], List[str]]:
        """Compare le ratio b/h lu a l'allongement pixel du contour et
        corrige les erreurs de decimale OCR (25x400 -> 0.25x0.40)."""
        warnings: List[str] = []
        kb, kh = ("b", "h") if "b" in dims and "h" in dims else (
            ("A", "B") if "A" in dims and "B" in dims else (None, None))
        if kb is None:
            return dict(dims), warnings
        b, h = float(dims[kb]), float(dims[kh])
        if b <= 0 or h <= 0:
            return dict(dims), warnings

        candidates: List[Tuple[float, float]] = []
        for eb in (0, -1, -2, 1):
            for eh in (0, -1, -2, 1):
                candidates.append((b * 10 ** eb, h * 10 ** eh))

        def plausible(v: float) -> bool:
            return SECTION_MIN_M <= v <= SECTION_MAX_M

        def score(cand: Tuple[float, float]) -> float:
            cb, ch = cand
            if contour_wh is None:
                return 0.0 if (plausible(cb) and plausible(ch)) else 1e9
            w, hh = contour_wh
            if w <= 0 or hh <= 0:
                return 1e9
            r_px = w / hh
            r_c = cb / ch
            dev = min(abs(math.log(r_c / r_px)), abs(math.log(r_c * r_px)))
            if not (plausible(cb) and plausible(ch)):
                dev += 5.0
            return dev

        raw_score = score((b, h))
        best = min(candidates, key=score)
        best_score = score(best)
        if best != (b, h) and (raw_score > math.log(tol_factor)
                               or not (plausible(b) and plausible(h))):
            if best_score < math.log(1.6) + (0 if contour_wh else 1):
                out = dict(dims)
                out[kb] = round(best[0], 3)
                out[kh] = round(best[1], 3)
                warnings.append(
                    f"Correction décimale CV : {kb}x{kh} {b}x{h} -> "
                    f"{out[kb]}x{out[kh]} (ratio contour).")
                return out, warnings
        if contour_wh and raw_score > math.log(tol_factor):
            warnings.append(
                f"Ratio {kb}/{kh} lu ({b}x{h}) incohérent avec le contour "
                f"({contour_wh[0]:.0f}x{contour_wh[1]:.0f} px) : à vérifier.")
        return dict(dims), warnings

    def associate_ocr_tokens_spatially(
        self,
        ocr_items: Sequence[Dict[str, Any]],
        page_num: int,
        max_dist_px: Optional[float] = None,
        contours: Optional[Sequence[BBox]] = None,
        axis_bubbles: Optional[Sequence[Dict[str, Any]]] = None,
        img_bgr=None,
        level: str = "Fondations",
    ) -> List[DrawingElement]:
        """Graphe spatial cKDTree : Contour <-> Repere <-> Section <-> Aciers."""
        import numpy as np

        max_d = float(max_dist_px or self.max_dist_px)
        labels, sections, rebars = [], [], []
        for it in ocr_items:
            txt = str(it.get("text", "")).strip()
            if not txt:
                continue
            x, y, w, h = it["bbox"]
            pt = (x + w / 2.0, y + h / 2.0)
            m_sec = self.re_section.search(txt)
            m_st = self.re_stirrup.search(txt)
            has_bar = bool(self.re_rebar.search(txt))

            if not m_sec and not has_bar:
                # Etiquettes couplees '(S1,P1)' : un repere par ouvrage
                for m_lbl in self.re_label.finditer(txt):
                    prefix = m_lbl.group(1).upper()
                    labels.append({
                        "prefix": prefix,
                        "label": f"{prefix}{m_lbl.group(2).upper()}",
                        "pt": pt, "item": it})
            if m_sec:
                sections.append({"groups": m_sec.groups(), "pt": pt, "item": it})
            if m_st or has_bar:
                rebars.append({"spec": txt, "pt": pt, "item": it})

        if not labels:
            return []

        sec_tree = _spatial_index([s["pt"] for s in sections]) if sections else None
        reb_tree = _spatial_index([r["pt"] for r in rebars]) if rebars else None
        cnt_centers = [(c[0] + c[2] / 2.0, c[1] + c[3] / 2.0)
                       for c in (contours or [])]
        cnt_tree = _spatial_index(cnt_centers) if cnt_centers else None

        grid_axes_all = (self.assign_grid_axes([l["pt"] for l in labels],
                                               axis_bubbles)
                         if axis_bubbles else [[] for _ in labels])

        used_sections: set = set()
        elements: List[DrawingElement] = []
        for lbl, axes in zip(labels, grid_axes_all):
            cat = LABEL_PREFIX_TO_CATEGORY.get(lbl["prefix"], "autre")
            dims: Dict[str, float] = {}
            warnings: List[str] = []
            conf = float(lbl["item"].get("conf", 0.85))

            contour_wh = None
            contour_box: Optional[BBox] = None
            if cnt_tree is not None:
                d, i = cnt_tree.query(lbl["pt"], distance_upper_bound=max_d)
                if np.isfinite(d):
                    contour_box = tuple(contours[i])
                    contour_wh = (float(contour_box[2]), float(contour_box[3]))

            if sec_tree is not None:
                dists, idxs = sec_tree.query(
                    lbl["pt"], k=min(3, len(sections)),
                    distance_upper_bound=max_d)
                dists = np.atleast_1d(dists)
                idxs = np.atleast_1d(idxs)
                for d, i in zip(dists, idxs):
                    if not np.isfinite(d):
                        continue
                    sec = sections[i]
                    dims = self._section_dims(sec["groups"], cat)
                    if dims:
                        used_sections.add(int(i))
                        conf = min(conf, float(sec["item"].get("conf", conf)))
                        break

            if dims:
                dims, warn = self.geometric_sanity_check(dims, contour_wh)
                warnings.extend(warn)

            specs: List[str] = []
            if reb_tree is not None:
                for i in reb_tree.query_ball_point(lbl["pt"], r=max_d * 1.4):
                    specs.append(rebars[i]["spec"])
            specs = list(dict.fromkeys(specs))

            bbox: BBox = tuple(int(v) for v in lbl["item"]["bbox"])
            crop = None
            if img_bgr is not None:
                union = self._union_box(bbox, contour_box)
                crop = self._crop(img_bgr, union, margin=40)

            elements.append(DrawingElement(
                category=cat,
                label=lbl["label"],
                dimensions=dims,
                rebar_spec=specs,
                quantity=1,
                grid_axes=axes,
                page_num=page_num,
                bbox=bbox,
                confidence=round(conf * (0.9 if warnings else 1.0), 3),
                source_engine="cv_spatial_graph",
                crop_image_bgr=crop,
                level=level,
                warnings=warnings,
            ))
        return elements

    @staticmethod
    def _union_box(a: BBox, b: Optional[BBox]) -> BBox:
        if b is None:
            return a
        x0 = min(a[0], b[0])
        y0 = min(a[1], b[1])
        x1 = max(a[0] + a[2], b[0] + b[2])
        y1 = max(a[1] + a[3], b[1] + b[3])
        return (x0, y0, x1 - x0, y1 - y0)

    @staticmethod
    def _crop(img_bgr, box: BBox, margin: int = 0):
        H, W = img_bgr.shape[:2]
        x, y, w, h = box
        x0, y0 = max(0, x - margin), max(0, y - margin)
        x1, y1 = min(W, x + w + margin), min(H, y + h + margin)
        if x1 <= x0 or y1 <= y0:
            return None
        return img_bgr[y0:y1, x0:x1].copy()

    # ==================================================================
    # COUCHE 3 bis : VLM sur crops ROI
    # ==================================================================
    def extract_roi_with_vlm(self, img_bgr, roi: BBox, page_num: int,
                             model_name: Optional[str] = None,
                             level: str = "Fondations") -> List[DrawingElement]:
        """Crop ROI -> VLM (Ollama local / Groq free) -> DrawingElement.
        Retourne [] si aucun backend n'est disponible."""
        crop = self._crop(img_bgr, roi)
        if crop is None:
            return []

        raw: Optional[dict] = None
        if self.groq_client is not None:  # client SDK OpenAI-like fourni
            raw = self._call_sdk_client(crop, model_name)
        else:
            router = self.vlm
            if model_name and router.backend is not None:
                router.backend.model = model_name
            raw = router.chat_json(crop)
        if raw is None:
            return []

        from extractors.vlm_router import VLMRouter
        elements = VLMRouter.parse_elements(raw, page_num, roi)
        for el in elements:
            el.level = level
            el.crop_image_bgr = crop
        return elements

    def _call_sdk_client(self, crop, model_name: Optional[str]) -> Optional[dict]:
        import json
        from extractors.vlm_router import (
            VLM_SYSTEM_PROMPT, VLM_USER_PROMPT, VLMRouter, _extract_json)
        b64 = VLMRouter.encode_png_b64(crop)
        if b64 is None:
            return None
        try:
            resp = self.groq_client.chat.completions.create(
                model=model_name or "meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[
                    {"role": "system", "content": VLM_SYSTEM_PROMPT},
                    {"role": "user", "content": [
                        {"type": "text", "text": VLM_USER_PROMPT},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{b64}"}},
                    ]},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content
            return _extract_json(content if isinstance(content, str)
                                 else json.dumps(content))
        except Exception as e:
            logger.warning("Client VLM SDK en echec : %s", e)
            return None

    # ==================================================================
    # COUCHE 4 : Overlay d'audit
    # ==================================================================
    def render_audit_overlay(
        self,
        img_bgr,
        elements: Sequence[DrawingElement],
        anomaly_labels: Optional[Sequence[str]] = None,
        out_path: Optional[str] = None,
        page_num: int = 1,
        table_rois: Optional[Sequence[BBox]] = None,
        max_side_px: int = 4000,
        metre_annotations: Optional[Sequence[Any]] = None,
    ) -> str:
        """Dessine les bbox (vert conf>=0.90 et valide, orange a verifier,
        rouge anomalie BET) et sauvegarde output/audit_overlays/page_{n}.png.

        ``metre_annotations`` : sequence alignee sur ``elements``, chaque
        entree etant un objet exposant ``text`` et ``color_bgr`` (typiquement
        ``core.dimension_resolver.OverlayAnnotation``). Le label de metre
        (ex. ``S=4,25 m2 [SOM OK 1.00]``) est ajoute sous la bbox avec la
        meme charte vert / orange que le label principal.
        """
        import cv2

        anomalies = {str(a).upper() for a in (anomaly_labels or [])}
        canvas = img_bgr.copy()
        for x, y, w, h in (table_rois or []):
            cv2.rectangle(canvas, (x, y), (x + w, y + h), (200, 120, 0), 3)
        for idx, el in enumerate(elements):
            x, y, w, h = el.bbox
            if el.label in anomalies or el.warnings and el.confidence < 0.6:
                color, tag = (0, 0, 255), "ANOMALIE"
            elif el.confidence >= 0.90 and not el.warnings:
                color, tag = (0, 180, 0), "OK"
            else:
                color, tag = (0, 140, 255), "A VERIFIER"
            cv2.rectangle(canvas, (x, y), (x + w, y + h), color, 3)
            dims = " ".join(f"{k}={v}" for k, v in el.dimensions.items())
            text = f"{el.label} {dims} [{tag} {el.confidence:.2f}]"
            cv2.putText(canvas, text, (x, max(15, y - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)

            annotation = None
            if metre_annotations is not None and idx < len(metre_annotations):
                annotation = metre_annotations[idx]
            if annotation is not None:
                ann_text = str(getattr(annotation, "text", "") or "")
                ann_color = tuple(getattr(annotation, "color_bgr", color))
                if ann_text:
                    canvas_h = canvas.shape[0]
                    cv2.putText(canvas, ann_text,
                                (x, min(canvas_h - 6, y + h + 20)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, ann_color, 2,
                                cv2.LINE_AA)

        H, W = canvas.shape[:2]
        if max(H, W) > max_side_px:
            f = max_side_px / max(H, W)
            canvas = cv2.resize(canvas, (int(W * f), int(H * f)),
                                interpolation=cv2.INTER_AREA)

        if out_path is None:
            try:
                from core.paths import get_output_dir
                base = Path(get_output_dir())
            except Exception:
                base = Path("output")
            out_path = str(base / "audit_overlays" / f"page_{page_num}.png")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(out_path, canvas)
        return out_path

    # ==================================================================
    # Orchestration page / document
    # ==================================================================
    def extract_page(self, page, page_num: int, use_vlm: bool = True,
                     level: str = "Fondations",
                     write_overlay: bool = True) -> Dict[str, Any]:
        """Pipeline complet d'une page : rendu -> ROI -> OCR -> graphe
        spatial -> VLM (tableaux) -> overlay."""
        img = self.render_page_high_res(page)
        table_rois = self.detect_table_and_schedule_rois(img)
        contours = self.detect_structural_elements_contours(img)
        ocr_items = self.ocr_page(img) if self.ocr.available else []
        bubbles = (self.detect_grid_axis_bubbles(img, ocr_items)
                   if ocr_items else [])

        elements = self.associate_ocr_tokens_spatially(
            ocr_items, page_num, contours=contours, axis_bubbles=bubbles,
            img_bgr=img, level=level)

        vlm_elements: List[DrawingElement] = []
        if use_vlm and table_rois and self.vlm.available:
            for roi in table_rois:
                vlm_elements.extend(
                    self.extract_roi_with_vlm(img, roi, page_num, level=level))
        elif use_vlm and table_rois:
            logger.info("Page %s : %d tableau(x) detecte(s) mais aucun VLM "
                        "disponible (Ollama/Groq) — OCR seul.",
                        page_num, len(table_rois))

        all_elements = elements + vlm_elements
        overlay = None
        if write_overlay:
            overlay = self.render_audit_overlay(
                img, all_elements, page_num=page_num, table_rois=table_rois)

        return {
            "page_num": page_num,
            "elements": all_elements,
            "table_rois": table_rois,
            "n_contours": len(contours),
            "n_ocr_tokens": len(ocr_items),
            "axis_bubbles": bubbles,
            "ocr_engine": self.ocr.engine_name,
            "vlm_backend": (self.vlm.backend.name if self.vlm.backend else None),
            "overlay_path": overlay,
            "image_shape": tuple(img.shape[:2]),
        }

    def extract_pdf(self, pdf_path: str, force: bool = False,
                    use_vlm: bool = True, progress_callback=None
                    ) -> Dict[str, Any]:
        """Routage hybride page par page : seules les pages necessitant la
        vision (ou toutes si `force`) passent par le pipeline CV."""
        import fitz

        results: List[Dict[str, Any]] = []
        pages_vision: List[int] = []
        with fitz.open(str(pdf_path)) as doc:
            total = len(doc)
            for idx in range(total):
                page = doc[idx]
                num = idx + 1
                needs = force or self.needs_vision_fallback(page)
                if progress_callback:
                    progress_callback(num, total, "vision" if needs else "vector")
                if not needs:
                    continue
                pages_vision.append(num)
                try:
                    results.append(self.extract_page(page, num, use_vlm=use_vlm))
                except Exception as e:  # jamais bloquant pour le pipeline
                    logger.warning("Vision page %s en echec : %s", num, e)
        elements = [el for r in results for el in r["elements"]]
        return {
            "pdf": str(pdf_path),
            "pages_vision": pages_vision,
            "pages": results,
            "elements": elements,
            "records": self.elements_to_records(elements),
        }

    @staticmethod
    def elements_to_records(elements: Sequence[DrawingElement]
                            ) -> List[ExtractedPlanRecord]:
        return [el.to_plan_record() for el in elements
                if el.category not in ("autre", "nomenclature")]
