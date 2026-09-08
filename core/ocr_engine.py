#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/ocr_engine.py -- Moteur OCR local paresseux (RapidOCR / ONNX, 100% CPU).

Filet de securite pour les plans SCANNES (images embarquees) :
  - RapidOCR n'est JAMAIS instancie au demarrage de l'application ;
  - il ne l'est qu'au premier appel utile (page sparse + image embarquee) ;
  - les plans vectoriels ne declenchent JAMAIS l'OCR.
Aucune valeur inventee : l'OCR produit des mots + coordonnees reellement
lus, injettes ensuite dans le parseur spatial standard.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from core.pdf_render import render_page_adaptive
from core.sanitizer import clean_cad_text

logger = logging.getLogger(__name__)

# Seuil : en dessous de ce nombre de mots vectoriels, une page est
# candidate a l'OCR (directive : 15).
OCR_MIN_WORDS = 15
OCR_DPI = 200
OCR_TIMEOUT_SECONDS = 30
# Taille image minimale (pixels) pour qualifier un vrai plan scanne.
# Un scan A4 a 150 dpi ~= 2,2 Mpx ; logos/icones << 100 kpx.
OCR_MIN_IMAGE_PIXELS = 500_000


class PlanOCREngine:
    """Chargeur paresseux du moteur OCR local.

    Usage :
        engine = PlanOCREngine()          # aucune dependance chargee
        words = engine.ocr_page_if_scanned(page)   # charge RapidOCR si besoin
    """

    def __init__(self, min_words: int = OCR_MIN_WORDS,
                 timeout_seconds: float = OCR_TIMEOUT_SECONDS):
        self.min_words = min_words
        self.timeout_seconds = timeout_seconds
        self._engine = None
        self._unavailable = False

    # ------------------------------------------------------------------
    # Chargement paresseux
    # ------------------------------------------------------------------
    def _ensure_engine(self):
        """Instancie RapidOCR au premier appel utile (une seule fois)."""
        if self._engine is not None:
            return self._engine
        if self._unavailable:
            return None
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception as e:
            logger.warning(
                "OCR local indisponible (%s) — pages scannées non lues. "
                "Installer : pip install rapidocr-onnxruntime", e)
            self._unavailable = True
            return None
        self._engine = RapidOCR()
        return self._engine

    @property
    def available(self) -> bool:
        return self._ensure_engine() is not None

    # ------------------------------------------------------------------
    # Decision d'OCR
    # ------------------------------------------------------------------
    @staticmethod
    def _looks_scanned(page) -> bool:
        """True si la page contient une image embarquee (plan scanne).
        Une page purement vectorielle/textuelle n'est jamais OCR-ee."""
        try:
            return bool(page.get_images(full=True))
        except Exception:
            return False

    @staticmethod
    def _has_scan_image(page) -> bool:
        """True si la page contient une image ASSEZ GRANDE pour etre un
        plan scanne (pas un simple logo/icone). Garde-fou contre l'OCR
        inutile des pages de garde riches en texte vectoriel."""
        try:
            for im in page.get_images(full=True):
                if len(im) > 3:
                    w, h = im[2] or 0, im[3] or 0
                    if w * h >= OCR_MIN_IMAGE_PIXELS:
                        return True
        except Exception:
            pass
        return False

    def ocr_page_if_scanned(self, page, min_words: int = None,
                            force: bool = False) -> list:
        """OCR paresseux d'une page PyMuPDF.

        Retourne une liste de mots normalises {text,x,y,x1,y1,page} :
          - [] si la page contient assez de texte vectoriel (>= min_words)
          - [] si la page ne semble pas scanned (aucune image embarquee)
          - les mots lus par RapidOCR sinon.
        Ne leve jamais d'exception : c'est un filet de securite.
        """
        seuil = self.min_words if min_words is None else min_words
        try:
            vector_words = page.get_text("words")
        except Exception:
            vector_words = []
        if len(vector_words) >= seuil and not force:
            return []
        if not self._has_scan_image(page):
            return []

        engine = self._ensure_engine()
        if engine is None:
            return []

        page_num = getattr(page, "number", 0) + 1
        try:
            import numpy as np
            pix = render_page_adaptive(page, normal_dpi=OCR_DPI)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n)
            if pix.n == 4:
                img = img[:, :, :3]
            pool = ThreadPoolExecutor(max_workers=1)
            future = pool.submit(engine, img)
            try:
                result, _ = future.result(timeout=self.timeout_seconds)
            except FutureTimeoutError:
                future.cancel()
                logger.warning(
                    "OCR expiré sur la page %s après %.1fs",
                    page_num, self.timeout_seconds)
                pool.shutdown(wait=False)
                return []
            finally:
                if not future.done():
                    pool.shutdown(wait=False)
                else:
                    pool.shutdown(wait=True)
        except Exception as e:
            logger.warning("OCR en echec sur la page %s : %s", page_num, e)
            return []

        words = []
        if result:
            for line in result:
                if not line or len(line) < 2:
                    continue
                box, text = line[0], clean_cad_text(line[1])
                if not text:
                    continue
                if not box or any(len(point) < 2 for point in box):
                    continue
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                words.append({
                    "text": text,
                    "x": round(float(min(xs)), 2),
                    "y": round(float(min(ys)), 2),
                    "x1": round(float(max(xs)), 2),
                    "y1": round(float(max(ys)), 2),
                    "page": page_num,
                    "ocr": True,
                })
        return words
