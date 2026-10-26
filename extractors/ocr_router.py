#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extractors/ocr_router.py -- Routeur OCR local 100% gratuit (CPU).

Ordre de preference (premier moteur importable) :
  1. PaddleOCR        (precis, lourd)         pip install paddleocr
  2. RapidOCR         (ONNX, deja requis)     pip install rapidocr-onnxruntime
  3. EasyOCR          (multi-langue)          pip install easyocr
  4. pytesseract      (leger, binaire externe)

Sortie normalisee : [{'text': str, 'bbox': (x, y, w, h), 'conf': float}]
en pixels de l'image fournie (BGR OpenCV).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

OCRItem = Dict[str, Any]


def _clean(text: str) -> str:
    try:
        from core.sanitizer import clean_cad_text
        return clean_cad_text(text)
    except Exception:
        return str(text or "").strip()


def _box_to_xywh(points) -> tuple:
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    return (int(round(x0)), int(round(y0)),
            int(round(x1 - x0)), int(round(y1 - y0)))


class LocalOCRRouter:
    """Chargement paresseux du premier moteur OCR disponible."""

    ENGINES = ("paddle", "rapid", "easy", "tesseract")

    def __init__(self, preferred: Optional[str] = None, lang: str = "fr"):
        self.preferred = preferred
        self.lang = lang
        self.engine_name: Optional[str] = None
        self._runner: Optional[Callable[[Any], List[OCRItem]]] = None
        self._unavailable = False

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self._ensure() is not None

    def _ensure(self):
        if self._runner is not None:
            return self._runner
        if self._unavailable:
            return None
        order = list(self.ENGINES)
        if self.preferred in order:
            order.remove(self.preferred)
            order.insert(0, self.preferred)
        for name in order:
            try:
                runner = getattr(self, f"_load_{name}")()
            except Exception as e:  # moteur non installe ou casse
                logger.debug("OCR %s indisponible : %s", name, e)
                continue
            if runner is not None:
                self._runner = runner
                self.engine_name = name
                logger.info("OCR local actif : %s", name)
                return runner
        self._unavailable = True
        logger.warning("Aucun moteur OCR local disponible "
                       "(pip install rapidocr-onnxruntime).")
        return None

    # ------------------------------------------------------------------
    def _load_paddle(self):
        from paddleocr import PaddleOCR
        ocr = PaddleOCR(use_angle_cls=True, lang="fr", show_log=False)

        def run(img_bgr) -> List[OCRItem]:
            res = ocr.ocr(img_bgr, cls=True) or []
            items: List[OCRItem] = []
            for page in res:
                for line in page or []:
                    box, (text, conf) = line[0], line[1]
                    text = _clean(text)
                    if text:
                        items.append({"text": text, "bbox": _box_to_xywh(box),
                                      "conf": float(conf)})
            return items
        return run

    def _load_rapid(self):
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR()

        def run(img_bgr) -> List[OCRItem]:
            result, _ = ocr(img_bgr)
            items: List[OCRItem] = []
            for line in result or []:
                if not line or len(line) < 2:
                    continue
                box, text = line[0], _clean(line[1])
                conf = float(line[2]) if len(line) > 2 else 0.8
                if text and box:
                    items.append({"text": text, "bbox": _box_to_xywh(box),
                                  "conf": conf})
            return items
        return run

    def _load_easy(self):
        import easyocr
        reader = easyocr.Reader(["fr", "en"], gpu=False, verbose=False)

        def run(img_bgr) -> List[OCRItem]:
            items: List[OCRItem] = []
            for box, text, conf in reader.readtext(img_bgr):
                text = _clean(text)
                if text:
                    items.append({"text": text, "bbox": _box_to_xywh(box),
                                  "conf": float(conf)})
            return items
        return run

    def _load_tesseract(self):
        import pytesseract
        pytesseract.get_tesseract_version()  # leve si binaire absent

        def run(img_bgr) -> List[OCRItem]:
            import cv2
            gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
            data = pytesseract.image_to_data(
                gray, config="--psm 11", output_type=pytesseract.Output.DICT)
            items: List[OCRItem] = []
            for i, raw in enumerate(data["text"]):
                text = _clean(raw)
                if not text:
                    continue
                conf = float(data["conf"][i])
                conf = conf / 100.0 if conf >= 0 else 0.5
                items.append({
                    "text": text,
                    "bbox": (int(data["left"][i]), int(data["top"][i]),
                             int(data["width"][i]), int(data["height"][i])),
                    "conf": conf,
                })
            return items
        return run

    # ------------------------------------------------------------------
    def recognize(self, img_bgr, offset: tuple = (0, 0)) -> List[OCRItem]:
        """OCR d'une image BGR. `offset` (dx, dy) est ajoute aux bbox pour
        replacer un crop ROI dans le repere de la page entiere.
        Ne leve jamais : retourne [] en cas d'echec."""
        runner = self._ensure()
        if runner is None:
            return []
        try:
            items = runner(img_bgr)
        except Exception as e:
            logger.warning("OCR (%s) en echec : %s", self.engine_name, e)
            return []
        dx, dy = offset
        if dx or dy:
            for it in items:
                x, y, w, h = it["bbox"]
                it["bbox"] = (x + dx, y + dy, w, h)
        return items
