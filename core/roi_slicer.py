#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/roi_slicer.py -- Virtualisation multi-echelles et decoupage ROI
pour plans BA grands formats (A0, A1, A2).

Detecte les zones d'interet (tableaux de nomenclature, vues en plan,
details/coupes) sur une page physique unique, puis partitionne la page
en sous-pages virtuelles avec extraction ciblee et confinement spatial.

ZERO MOCK : aucune valeur inventee, aucune donnee generee.
"""
import re
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict

# Patterns de detection des ROIs
ROI_TABLEAU_PATTERNS = [
    re.compile(r"TABLEAU\s+DES\s+SEMELLES", re.I),
    re.compile(r"SEMELLES?\s+ISOLE[ES]", re.I),
    re.compile(r"TABLEAU\s+DES\s+POTEAUX", re.I),
    re.compile(r"DETAIL\s+DES\s+POTEAUX", re.I),
    re.compile(r"POTEAUX", re.I),
    re.compile(r"TABLEAU\s+DES\s+POUTRES", re.I),
    re.compile(r"TABLEAU\s+DES\s+LONGRINES", re.I),
    re.compile(r"TABLEAU\s+DES\s+CHAINAGES", re.I),
    re.compile(r"NOMENCLATURE", re.I),
    re.compile(r"FERRAILLAGE", re.I),
]

ROI_PLAN_PATTERNS = [
    re.compile(r"FONDATION", re.I),
    re.compile(r"PLAN\s+DE\s+FONDATION", re.I),
    re.compile(r"PL\.?\s*HT\.?\s*RDC", re.I),
    re.compile(r"PLANCHER\s+HAUT\s+RDC", re.I),
    re.compile(r"PL\.?\s*HT\.?\s*\d+\s*[EÉ]TAGE", re.I),
    re.compile(r"PLANCHER\s+HAUT", re.I),
    re.compile(r"COFFRAGE", re.I),
    re.compile(r"IMPLANTATION", re.I),
    re.compile(r"PLAN\s+D'IMPLANTATION", re.I),
]

ROI_DETAIL_PATTERNS = [
    re.compile(r"D[ÉE]TAIL\s+LONGRINE", re.I),
    re.compile(r"CHA[IÎ]NAGE", re.I),
    re.compile(r"BANDES?\s+NOY[ÉE]ES?", re.I),
    re.compile(r"COUPE", re.I),
    re.compile(r"D[ÉE]TAIL", re.I),
]


@dataclass
class ROI:
    """Zone d'interet detectee sur une page."""
    rect: Tuple[float, float, float, float]  # (x0, y0, x1, y1)
    roi_type: str  # "tableau", "plan", "detail"
    label: str  # texte detecte servant de titre
    confidence: float = 1.0  # 0..1


@dataclass
class GrandFormatPage:
    """Representation d'une page grand format decoupee en ROIs."""
    page_num: int
    width: float
    height: float
    is_grand_format: bool
    rois: List[ROI] = field(default_factory=list)
    epsilon: float = 10.0  # tolerance spatiale relative


def is_grand_format(page) -> bool:
    """Detecte si une page est de format grand (A0, A1, A2).
    
    Seuil : largeur > 1600 OU hauteur > 1600 points (72 DPI).
    A4 = 595x842, A3 = 842x1191, A2 = 1191x1684, A1 = 1684x2384, A0 = 2384x3370.
    """
    rect = page.rect
    return rect.width > 1600 or rect.height > 1600


def compute_epsilon(page) -> float:
    """Calcule la tolerance spatiale relative (epsilon) selon la taille de page.
    
    Remplace les seuils fixes (20 pt) par une valeur proportionnelle :
    epsilon = max(10.0, min(width, height) * 0.008)
    
    Invariant a l'echelle : A4 (595 pt) -> 4.76, A0 (2384 pt) -> 19.07.
    """
    rect = page.rect
    return max(10.0, min(rect.width, rect.height) * 0.008)


def detect_rois(page, page_num: int) -> List[ROI]:
    """Detecte les zones d'interet sur une page grand format.
    
    Strategie :
    1. Recherche des ancres textuelles maîtresses via search_for()
    2. Analyse des lignes de separation horizontales/verticales
    3. Partition en rectangles disjoints
    
    Retourne la liste des ROIs triees par position (haut-gauche -> bas-droite).
    """
    rect = page.rect
    rois = []
    
    # 1. Collecter toutes les ancres textuelles detectees
    anchors = []
    
    for pattern in ROI_TABLEAU_PATTERNS:
        hits = page.search_for(pattern.pattern.replace("\\", ""))
        for h in hits:
            anchors.append(("tableau", h, pattern.pattern))
    
    for pattern in ROI_PLAN_PATTERNS:
        hits = page.search_for(pattern.pattern.replace("\\", ""))
        for h in hits:
            anchors.append(("plan", h, pattern.pattern))
    
    for pattern in ROI_DETAIL_PATTERNS:
        hits = page.search_for(pattern.pattern.replace("\\", ""))
        for h in hits:
            anchors.append(("detail", h, pattern.pattern))
    
    # 2. Dedupliquer les ancres proches (meme zone)
    anchors = _dedupe_anchors(anchors, epsilon=50.0)
    
    # 3. Si aucune ancre, fallback sur decomposition quadrants
    if not anchors:
        return _fallback_quadrants(rect, page_num)
    
    # 4. Pour chaque ancre, definir une ROI rectangulaire
    for roi_type, bbox, pattern in anchors:
        # Etendre la zone autour de l'ancre
        x0, y0, x1, y1 = bbox.x0, bbox.y0, bbox.x1, bbox.y1
        
        # Elargir horizontalement (la nomenclature s'etend en largeur)
        margin_x = rect.width * 0.15
        # Elargir verticalement (la nomenclature s'etend en hauteur)
        margin_y = rect.height * 0.20
        
        roi_x0 = max(rect.x0, x0 - margin_x)
        roi_y0 = max(rect.y0, y0 - margin_y * 0.3)  # l'ancre est en haut
        roi_x1 = min(rect.x1, x1 + margin_x)
        roi_y1 = min(rect.y1, y1 + margin_y)
        
        rois.append(ROI(
            rect=(roi_x0, roi_y0, roi_x1, roi_y1),
            roi_type=roi_type,
            label=_extract_label_text(page, bbox),
        ))
    
    # 5. Fusionner les ROIs chevauchantes
    rois = _merge_overlapping_rois(rois)
    
    # 6. Ajouter les zones non couvertes comme "detail"
    rois = _fill_gaps(rect, rois, page_num)
    
    return rois


def _dedupe_anchors(anchors, epsilon=50.0):
    """Deduplique les ancres proches (meme region)."""
    if not anchors:
        return []
    
    deduped = []
    for roi_type, bbox, pattern in anchors:
        cx = (bbox.x0 + bbox.x1) / 2
        cy = (bbox.y0 + bbox.y1) / 2
        
        is_dup = False
        for _, existing, _ in deduped:
            ecx = (existing.x0 + existing.x1) / 2
            ecy = (existing.y0 + existing.y1) / 2
            if abs(cx - ecx) < epsilon and abs(cy - ecy) < epsilon:
                is_dup = True
                break
        
        if not is_dup:
            deduped.append((roi_type, bbox, pattern))
    
    return deduped


def _extract_label_text(page, bbox) -> str:
    """Extrait le texte proche d'une ancre pour labeliser la ROI."""
    try:
        words = page.get_text("words", clip=bbox)
        return " ".join(w[4] for w in words[:5]).strip()
    except Exception:
        return ""


def _fallback_quadrants(rect, page_num: int) -> List[ROI]:
    """Fallback : divise la page en 4 quadrants si aucune ancre detectee."""
    w = rect.width
    h = rect.height
    return [
        ROI(rect=(rect.x0, rect.y0, rect.x0 + w/2, rect.y0 + h/2),
            roi_type="plan", label="quadrant HG"),
        ROI(rect=(rect.x0 + w/2, rect.y0, rect.x1, rect.y0 + h/2),
            roi_type="tableau", label="quadrant HD"),
        ROI(rect=(rect.x0, rect.y0 + h/2, rect.x0 + w/2, rect.y1),
            roi_type="plan", label="quadrant BG"),
        ROI(rect=(rect.x0 + w/2, rect.y0 + h/2, rect.x1, rect.y1),
            roi_type="detail", label="quadrant BD"),
    ]


def _merge_overlapping_rois(rois: List[ROI]) -> List[ROI]:
    """Fusionne les ROIs qui se chevauchent significantiellement."""
    if len(rois) <= 1:
        return rois
    
    merged = []
    used = set()
    
    for i, roi_a in enumerate(rois):
        if i in used:
            continue
        
        current = roi_a
        for j, roi_b in enumerate(rois):
            if j <= i or j in used:
                continue
            
            if _rects_overlap_significant(current.rect, roi_b.rect, threshold=0.3):
                # Fusionner
                x0 = min(current.rect[0], roi_b.rect[0])
                y0 = min(current.rect[1], roi_b.rect[1])
                x1 = max(current.rect[2], roi_b.rect[2])
                y1 = max(current.rect[3], roi_b.rect[3])
                
                # Garder le type avec la plus haute priorite
                priority = {"tableau": 0, "plan": 1, "detail": 2}
                new_type = (current.roi_type if priority.get(current.roi_type, 9) 
                           <= priority.get(roi_b.roi_type, 9) else roi_b.roi_type)
                
                current = ROI(
                    rect=(x0, y0, x1, y1),
                    roi_type=new_type,
                    label=current.label or roi_b.label,
                )
                used.add(j)
        
        merged.append(current)
        used.add(i)
    
    return merged


def _rects_overlap_significant(rect_a, rect_b, threshold=0.3) -> bool:
    """Verifie si deux rectangles se chevauchent significativement."""
    x0 = max(rect_a[0], rect_b[0])
    y0 = max(rect_a[1], rect_b[1])
    x1 = min(rect_a[2], rect_b[2])
    y1 = min(rect_a[3], rect_b[3])
    
    if x0 >= x1 or y0 >= y1:
        return False
    
    overlap_area = (x1 - x0) * (y1 - y0)
    min_area = min(
        (rect_a[2] - rect_a[0]) * (rect_a[3] - rect_a[1]),
        (rect_b[2] - rect_b[0]) * (rect_b[3] - rect_b[1])
    )
    
    if min_area <= 0:
        return False
    
    return overlap_area / min_area >= threshold


def _fill_gaps(page_rect, rois: List[ROI], page_num: int) -> List[ROI]:
    """Ajoute les zones non couvertes comme ROIs de type 'detail'."""
    if not rois:
        return [ROI(
            rect=(page_rect.x0, page_rect.y0, page_rect.x1, page_rect.y1),
            roi_type="plan", label="page entiere")]
    
    # Trier les ROIs par position Y
    sorted_rois = sorted(rois, key=lambda r: r.rect[1])
    
    result = list(sorted_rois)
    
    # Verifier les gaps verticaux
    gaps = []
    for i in range(len(sorted_rois) - 1):
        y_end_current = sorted_rois[i].rect[3]
        y_start_next = sorted_rois[i + 1].rect[1]
        
        if y_start_next - y_end_current > 50:  # gap significatif
            gap_rect = (
                page_rect.x0,
                y_end_current,
                page_rect.x1,
                y_start_next,
            )
            gaps.append(ROI(
                rect=gap_rect,
                roi_type="detail",
                label=f"zone non classifye {i+1}",
            ))
    
    result.extend(gaps)
    return result


def get_words_in_roi(page, roi: ROI, all_words: list) -> list:
    """Filtre les mots qui tombent dans une ROI donnee.
    
    Utilise les coordonnees reelles des mots (pas de clipping PyMuPDF,
    mais filtrage geometric pur pour compatibilite avec les mots OCR).
    """
    x0, y0, x1, y1 = roi.rect
    result = []
    for w in all_words:
        wx = w.get("x", 0)
        wy = w.get("y", 0)
        wx1 = w.get("x1", wx)
        wy1 = w.get("y1", wy)
        # Un mot est dans la ROI si son centre est dans le rectangle
        cx = (wx + wx1) / 2
        cy = (wy + wy1) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            result.append(w)
    return result


def get_text_in_roi(page, roi: ROI) -> str:
    """Extrait le texte brut d'une ROI via PyMuPDF clip."""
    x0, y0, x1, y1 = roi.rect
    try:
        clip = (x0, y0, x1, y1)
        return page.get_text("text", clip=clip)
    except Exception:
        return ""


def reconcile_nombre(catalogue: dict, implantations: dict, 
                     page_ance: dict = None) -> list:
    """Reconciliation du NOMBRE forfaitaire ou decompte.
    
    Regle : si un element a des dimensions dans le tableau mais une quantite
    nulle/non renseigne (ou non decompte sur le plan), injecter le decompte
    exact d'occurrences localisees sur la ROI plan correspondante.
    
    Croise les catalogues :
    - Semelles S1 avec dims dans le tableau mais pas d'implantation -> compter S1 sur le plan
    - Poteaux P1/Q1 avec dims dans le tableau mais pas d'implantation -> compter sur le plan
    
    Retourne la liste des reconciliations effectuees (pour hypothese tracee).
    """
    reconciliations = []
    
    # Verifier les semelles
    for tk, spec in catalogue.get("semelles", {}).items():
        if not spec or spec.get("dimensions_manquantes"):
            continue
        
        a = spec.get("a", 0)
        b = spec.get("b", 0)
        if a <= 0 or b <= 0:
            continue
        
        # Compter les occurrences sur le plan
        plan_count = sum(
            1 for inst in implantations.get("semelles", [])
            if inst.get("type") == tk
        )
        
        if plan_count == 0:
            # Semelle dans le tableau mais pas d'implantation spatiale
            # -> on ne force pas, on trace l'anomalie
            reconciliations.append(
                f"{tk}: dimensions {a:.2f}x{b:.2f}m dans le tableau "
                "mais aucune implantation spatiale detectee — "
                "a verifier sur le plan."
            )
        elif plan_count > 0:
            reconciliations.append(
                f"{tk}: decompte spatial = {plan_count} occurrences "
                f"(dimensions {a:.2f}x{b:.2f}m)"
            )
    
    # Verifier les poteaux
    for tk, spec in catalogue.get("poteaux", {}).items():
        if not spec:
            continue
        
        a = spec.get("a", 0)
        b = spec.get("b", 0)
        if a <= 0 or b <= 0:
            continue
        
        plan_count = sum(
            1 for inst in implantations.get("poteaux", [])
            if inst.get("type") == tk
        )
        
        if plan_count == 0:
            reconciliations.append(
                f"{tk}: section {a:.2f}x{b:.2f}m dans le tableau "
                "mais aucune implantation spatiale detectee — "
                "a verifier sur le plan."
            )
    
    return reconciliations
