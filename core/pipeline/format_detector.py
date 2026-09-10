"""
FormatDetector — detection du format d'entree.
Aucune supposition : lit la signature du fichier et la structure interne.
"""
from __future__ import annotations
from pathlib import Path
from enum import Enum
from typing import Optional


class PlanFormat(Enum):
    PDF_VECTOR = "pdf_vector"
    PDF_SCANNED = "pdf_scanned"
    PDF_MIXED = "pdf_mixed"
    DXF = "dxf"
    IFC = "ifc"
    IMAGE = "image"
    UNKNOWN = "unknown"


class FormatDetector:
    """Detecte le format du fichier d'entree sans supposition."""

    @staticmethod
    def detect(file_path: str) -> PlanFormat:
        path = Path(file_path)
        ext = path.suffix.lower()

        if ext == ".pdf":
            return FormatDetector._detect_pdf(path)
        elif ext == ".dxf":
            return PlanFormat.DXF
        elif ext == ".ifc":
            return PlanFormat.IFC
        elif ext in (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"):
            return PlanFormat.IMAGE
        else:
            return PlanFormat.UNKNOWN

    @staticmethod
    def _detect_pdf(path: Path) -> PlanFormat:
        try:
            import pymupdf
            doc = pymupdf.open(str(path))
            if doc.page_count == 0:
                doc.close()
                return PlanFormat.UNKNOWN

            page = doc[0]
            text = page.get_text("text").strip()
            images = page.get_images(full=True)
            vector_paths = page.get_drawings()

            has_text = len(text) > 50
            has_images = len(images) > 0
            has_vectors = len(vector_paths) > 0

            doc.close()

            if has_text and has_vectors:
                return PlanFormat.PDF_VECTOR
            elif has_text and has_images and not has_vectors:
                return PlanFormat.PDF_MIXED
            elif has_text and not has_images and not has_vectors:
                return PlanFormat.PDF_SCANNED
            elif has_images and not has_text:
                return PlanFormat.PDF_SCANNED
            elif has_vectors:
                return PlanFormat.PDF_VECTOR
            else:
                return PlanFormat.PDF_SCANNED

        except Exception:
            return PlanFormat.UNKNOWN

    @staticmethod
    def get_page_info(file_path: str) -> dict:
        """Retourne les informations de base du document."""
        path = Path(file_path)
        ext = path.suffix.lower()
        info = {"format": FormatDetector.detect(file_path).value, "pages": 0, "size": None}

        if ext == ".pdf":
            try:
                import pymupdf
                doc = pymupdf.open(str(path))
                info["pages"] = doc.page_count
                if doc.page_count > 0:
                    rect = doc[0].rect
                    info["size"] = {"width": rect.width, "height": rect.height}
                doc.close()
            except Exception:
                pass
        return info
