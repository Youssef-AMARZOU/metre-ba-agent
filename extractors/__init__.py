"""extractors -- Moteurs d'extraction amont (Computer Vision, OCR, VLM).

Tous convergent vers `core.bet_contracts.DrawingElement`.
Les imports lourds (cv2, fitz, OCR) sont paresseux : importer ce paquet
ne charge aucune dependance native.
"""
from core.bet_contracts import DrawingElement, ExtractedPlanRecord  # noqa: F401

__all__ = ["DrawingElement", "ExtractedPlanRecord"]
