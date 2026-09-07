#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/ingestion.py — Module d'ingestion universel pour plans BA.

Supporte : DXF, DWG, PDF vectoriel, PDF scanné, PNG, JPG, TIFF, BMP, WEBP.
Tous les flux convergent vers une structure normalisée (dict) validée par
le schéma Pydantic ProjetBAParseOutput.

Usage :
    from core.ingestion import UniversalPlanIngestor
    ingestor = UniversalPlanIngestor("mon_plan.pdf")
    data = ingestor.ingest()
"""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ============================================================================
# Constantes
# ============================================================================

SUPPORTED_EXTENSIONS = {
    ".dxf", ".dwg",
    ".pdf",
    ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp",
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}
VECTOR_EXTENSIONS = {".dxf", ".dwg"}
PDF_EXTENSIONS = {".pdf"}

TILE_MAX_DIM = 2048
DPI_RASTER = 300


# ============================================================================
# Énumérations
# ============================================================================

class PlanSource(str, Enum):
    DXF = "dxf"
    DWG = "dwg"
    PDF_VECTORIEL = "pdf_vectoriel"
    PDF_RASTER = "pdf_raster"
    PDF_HYBRIDE = "pdf_hybride"
    IMAGE = "image"
    UNKNOWN = "unknown"


class IngestionStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    ERROR = "error"


# ============================================================================
# Résultat d'ingestion
# ============================================================================

@dataclass
class IngestionResult:
    """Résultat normalisé de l'ingestion d'un plan."""
    source: PlanSource
    status: IngestionStatus
    file_path: str
    file_hash: str = ""
    pages: int = 0
    text_blocks: List[Dict[str, Any]] = field(default_factory=list)
    drawing_elements: List[Dict[str, Any]] = field(default_factory=list)
    table_rows: List[Dict[str, Any]] = field(default_factory=list)
    dimensions: List[Dict[str, Any]] = field(default_factory=list)
    images: List[bytes] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convertit en dict sérialisable (sans les bytes d'images)."""
        return {
            "source": self.source.value,
            "status": self.status.value,
            "file_path": self.file_path,
            "file_hash": self.file_hash,
            "pages": self.pages,
            "nb_text_blocks": len(self.text_blocks),
            "nb_drawing_elements": len(self.drawing_elements),
            "nb_table_rows": len(self.table_rows),
            "nb_dimensions": len(self.dimensions),
            "nb_images": len(self.images),
            "errors": self.errors,
            "metadata": self.metadata,
        }


def optional_dependencies() -> Dict[str, bool]:
    """Retourne la disponibilité des adaptateurs sans les rendre obligatoires."""
    names = {
        "pymupdf": "fitz",
        "pdfplumber": "pdfplumber",
        "pillow": "PIL",
        "ezdxf": "ezdxf",
        "opencv": "cv2",
        "paddleocr": "paddleocr",
    }
    available = {}
    for name, module in names.items():
        try:
            __import__(module)
            available[name] = True
        except ImportError:
            available[name] = False
    return available


def fuse_spatial_elements(elements: List[Dict[str, Any]],
                          distance: float = 4.0) -> List[Dict[str, Any]]:
    """Fusionne les fragments proches tout en conservant leur provenance."""
    fused: List[Dict[str, Any]] = []
    for element in elements:
        bbox = element.get("bbox")
        if not bbox:
            fused.append(dict(element))
            continue
        match = None
        for candidate in fused:
            cb = candidate.get("bbox")
            if not cb or candidate.get("page") != element.get("page"):
                continue
            if (bbox[0] <= cb[2] + distance and cb[0] <= bbox[2] + distance
                    and bbox[1] <= cb[3] + distance
                    and cb[1] <= bbox[3] + distance):
                match = candidate
                break
        if match is None:
            fused.append(dict(element))
            continue
        match["text"] = f"{match.get('text', '')} {element.get('text', '')}".strip()
        old = match.setdefault("provenance", {})
        old.setdefault("fragments", []).append(element.get("id"))
        match["bbox"] = (
            min(match["bbox"][0], bbox[0]), min(match["bbox"][1], bbox[1]),
            max(match["bbox"][2], bbox[2]), max(match["bbox"][3], bbox[3]),
        )
        match["confidence"] = min(float(match.get("confidence", 0.0)),
                                  float(element.get("confidence", 0.0)))
    return fused


def _drawing_element(text: str, bbox, page: int, source: str,
                     element_id: str, confidence: float = 1.0) -> Dict[str, Any]:
    return {
        "id": element_id,
        "text": text,
        "bbox": tuple(round(float(v), 2) for v in bbox) if bbox else None,
        "page": page,
        "source": source,
        "confidence": confidence,
        "provenance": {"adapter": source, "page": page},
        "warnings": [],
    }


# ============================================================================
# Détecteur de type de fichier
# ============================================================================

def detect_source(file_path: str) -> PlanSource:
    """Détecte le type de source à partir de l'extension et du contenu."""
    ext = Path(file_path).suffix.lower()

    if ext == ".dxf":
        return PlanSource.DXF
    if ext == ".dwg":
        return PlanSource.DWG
    if ext == ".pdf":
        return PlanSource.PDF_VECTORIEL  # sera raffiné lors de l'analyse
    if ext in IMAGE_EXTENSIONS:
        return PlanSource.IMAGE
    return PlanSource.UNKNOWN


def file_hash(file_path: str) -> str:
    """Hash SHA256 du fichier (pour traçabilité)."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


# ============================================================================
# Branche 1 : AutoCAD DXF
# ============================================================================

def _parse_dxf(file_path: str) -> IngestionResult:
    """Parse un fichier DXF nativement via ezdxf."""
    try:
        import ezdxf
    except ImportError:
        return IngestionResult(
            source=PlanSource.DXF, status=IngestionStatus.ERROR,
            file_path=file_path, file_hash=file_hash(file_path),
            errors=["Adaptateur ezdxf absent : installez l'option DXF "
                    "ou exportez le plan en PDF/DXF."],
        )
    from ezdxf import recover

    result = IngestionResult(
        source=PlanSource.DXF,
        status=IngestionStatus.OK,
        file_path=file_path,
        file_hash=file_hash(file_path),
    )

    try:
        doc, auditor = recover.readfile(file_path)
    except Exception as e:
        result.status = IngestionStatus.ERROR
        result.errors.append(f"Impossible de lire le DXF : {e}")
        return result

    msp = doc.modelspace()

    # Extraction des textes
    for entity in msp:
        etype = entity.dxftype()
        layer = entity.dxf.layer if hasattr(entity.dxf, "layer") else ""

        if etype in ("TEXT", "MTEXT"):
            text = entity.plain_text() if etype == "MTEXT" else entity.dxf.text
            if text and text.strip():
                x = entity.dxf.insert.x if hasattr(entity.dxf, "insert") else 0
                y = entity.dxf.insert.y if hasattr(entity.dxf, "insert") else 0
                result.text_blocks.append({
                    "text": text.strip(),
                    "x": round(x, 2),
                    "y": round(y, 2),
                    "layer": layer,
                    "type": etype,
                })
                result.drawing_elements.append(_drawing_element(
                    text.strip(), (x, y, x, y), 1, "ezdxf",
                    f"dxf-text-{len(result.drawing_elements) + 1}"))

        elif etype == "INSERT":
            block_name = entity.dxf.name
            x = entity.dxf.insert.x
            y = entity.dxf.insert.y
            result.text_blocks.append({
                "text": f"[BLOCK:{block_name}]",
                "x": round(x, 2),
                "y": round(y, 2),
                "layer": layer,
                "type": "INSERT",
            })

        elif etype == "DIMENSION":
            try:
                dim_text = entity.dxf.text if hasattr(entity.dxf, "text") else ""
                if dim_text and dim_text.strip():
                    result.dimensions.append({
                        "value": dim_text.strip(),
                        "layer": layer,
                        "type": "DIMENSION",
                    })
            except Exception:
                pass

        elif etype == "LWPOLYLINE":
            try:
                pts = list(entity.get_points(format="xy"))
                if len(pts) >= 2:
                    # Calculer la longueur
                    total = 0
                    for i in range(1, len(pts)):
                        dx = pts[i][0] - pts[i - 1][0]
                        dy = pts[i][1] - pts[i - 1][1]
                        total += (dx ** 2 + dy ** 2) ** 0.5
                    result.dimensions.append({
                        "value": f"{round(total / 1000, 3)}" if total > 100 else f"{round(total, 3)}",
                        "layer": layer,
                        "type": "LWPOLYLINE_LENGTH",
                    })
            except Exception:
                pass

    result.metadata["nb_layers"] = len(doc.layers)
    result.metadata["layers"] = [d.dxf.name for d in doc.layers]

    if auditor.has_errors:
        result.errors.append(f"DXF auditor : {len(auditor.errors)} erreurs détectées")

    return result


# ============================================================================
# Branche 2 : PDF Vectoriel
# ============================================================================

def _parse_pdf_vectoriel(file_path: str) -> IngestionResult:
    """Parse un PDF vectoriel via PyMuPDF."""
    import fitz

    result = IngestionResult(
        source=PlanSource.PDF_VECTORIEL,
        status=IngestionStatus.OK,
        file_path=file_path,
        file_hash=file_hash(file_path),
    )

    try:
        doc = fitz.open(file_path)
    except Exception as e:
        result.status = IngestionStatus.ERROR
        result.errors.append(f"Impossible d'ouvrir le PDF : {e}")
        return result

    result.pages = len(doc)

    for pi in range(len(doc)):
        page = doc[pi]
        text = page.get_text("text")

        # Vérifier si le PDF est vectoriel (texte extractible)
        if len(text.strip()) > 100:
            # Extraction des blocs de texte avec positions
            blocks = page.get_text("dict")["blocks"]
            for block in blocks:
                if block["type"] == 0:  # Text block
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            t = span["text"].strip()
                            if t:
                                bbox = span["bbox"]
                                result.text_blocks.append({
                                    "text": t,
                                    "x": round(bbox[0], 2),
                                    "y": round(bbox[1], 2),
                                    "x1": round(bbox[2], 2),
                                    "y1": round(bbox[3], 2),
                                    "page": pi + 1,
                                    "font_size": round(span.get("size", 0), 1),
                                })
        else:
            result.metadata.setdefault("raster_pages", []).append(pi + 1)
            result.status = IngestionStatus.PARTIAL

    doc.close()
    return result


# ============================================================================
# Branche 3 : PDF Scanné / Images
# ============================================================================

def _parse_image(file_path: str, target_dpi: int = DPI_RASTER) -> IngestionResult:
    """Charge une image et la normalise en RGB."""
    try:
        from PIL import Image
    except ImportError:
        return IngestionResult(
            source=PlanSource.IMAGE, status=IngestionStatus.ERROR,
            file_path=file_path, file_hash=file_hash(file_path),
            errors=["Adaptateur Pillow absent : installez Pillow pour "
                    "les images raster."],
        )

    result = IngestionResult(
        source=PlanSource.IMAGE,
        status=IngestionStatus.OK,
        file_path=file_path,
        file_hash=file_hash(file_path),
    )

    try:
        img = Image.open(file_path)
        if img.mode != "RGB":
            img = img.convert("RGB")
        result.pages = 1
        result.metadata["original_size"] = f"{img.width}x{img.height}"
        result.metadata["mode"] = img.mode

        # Tuilage si trop grand
        tiles = _tile_image(img, file_path)
        result.images = tiles
        result.metadata["nb_tiles"] = len(tiles)

    except Exception as e:
        result.status = IngestionStatus.ERROR
        result.errors.append(f"Erreur image : {e}")

    return result


def _parse_pdf_scanned(file_path: str, dpi: int = DPI_RASTER) -> IngestionResult:
    """Convertit les pages d'un PDF scanné en images."""
    import fitz

    result = IngestionResult(
        source=PlanSource.PDF_RASTER,
        status=IngestionStatus.OK,
        file_path=file_path,
        file_hash=file_hash(file_path),
    )

    try:
        doc = fitz.open(file_path)
    except Exception as e:
        result.status = IngestionStatus.ERROR
        result.errors.append(f"Impossible d'ouvrir le PDF : {e}")
        return result

    result.pages = len(doc)

    for pi in range(len(doc)):
        page = doc[pi]
        text = page.get_text("text").strip()

        # Si le texte est minimal → scanné
        if len(text) <= 100:
            try:
                from core.pdf_render import render_page_adaptive
                pix = render_page_adaptive(page, normal_dpi=dpi)
                result.images.append(pix.tobytes("png"))
                result.metadata.setdefault("raster_pages", []).append(pi + 1)
            except Exception as exc:
                result.status = IngestionStatus.PARTIAL
                result.errors.append(
                    f"Rendu de la page {pi + 1} impossible : {exc}")
        else:
            # PDF vectoriel, extraire le texte normalement
            blocks = page.get_text("dict")["blocks"]
            for block in blocks:
                if block["type"] == 0:
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            t = span["text"].strip()
                            if t:
                                bbox = span["bbox"]
                                result.text_blocks.append({
                                    "text": t,
                                    "x": round(bbox[0], 2),
                                    "y": round(bbox[1], 2),
                                    "x1": round(bbox[2], 2),
                                    "y1": round(bbox[3], 2),
                                    "page": pi + 1,
                                })

    doc.close()

    if result.images:
        result.status = IngestionStatus.PARTIAL

    return result


def _tile_image(img, source_name: str, max_dim: int = TILE_MAX_DIM) -> List[bytes]:
    """Découpe une image en tuiles si elle dépasse max_dim."""
    from PIL import Image
    import io

    w, h = img.size
    if w <= max_dim and h <= max_dim:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return [buf.getvalue()]

    tiles = []
    tile_id = 0
    for y0 in range(0, h, max_dim):
        for x0 in range(0, w, max_dim):
            x1 = min(x0 + max_dim, w)
            y1 = min(y0 + max_dim, h)
            tile = img.crop((x0, y0, x1, y1))
            buf = io.BytesIO()
            tile.save(buf, format="PNG")
            tiles.append(buf.getvalue())
            tile_id += 1

    logger.info(f"Image {source_name} tuilée en {len(tiles)} blocs ({w}x{h})")
    return tiles


# ============================================================================
# Ingesteur universel
# ============================================================================

class UniversalPlanIngestor:
    """Point d'entrée unique pour l'ingestion de plans BA.

    Détecte automatiquement le type de fichier et route vers le parser
    approprié. Le résultat est toujours un IngestionResult normalisé.
    """

    def __init__(self, file_path: str, dpi: int = DPI_RASTER):
        self.file_path = str(Path(file_path).resolve())
        self.dpi = dpi
        self.source = detect_source(self.file_path)

        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"Fichier introuvable : {self.file_path}")

        ext = Path(self.file_path).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Extension '{ext}' non supportée. "
                f"Extensions acceptées : {sorted(SUPPORTED_EXTENSIONS)}"
            )

    def ingest(self) -> IngestionResult:
        """Détecte le type et route vers le parser approprié."""
        logger.info(f"Ingestion de {self.file_path} (source={self.source.value})")

        if self.source == PlanSource.DXF:
            return _parse_dxf(self.file_path)

        if self.source == PlanSource.DWG:
            return self._parse_dwg()

        if self.source == PlanSource.PDF_VECTORIEL:
            return self._parse_pdf()

        if self.source == PlanSource.IMAGE:
            return _parse_image(self.file_path, self.dpi)

        return IngestionResult(
            source=PlanSource.UNKNOWN,
            status=IngestionStatus.ERROR,
            file_path=self.file_path,
            errors=[f"Type de fichier non reconnu : {self.file_path}"],
        )

    def _parse_dwg(self) -> IngestionResult:
        """Tente de convertir DWG → DXF, sinon lève une erreur."""
        import shutil
        import subprocess

        # Chercher dwg2dxf ou ODA File Converter
        dwg2dxf = shutil.which("dwg2dxf")
        if dwg2dxf:
            dxf_path = self.file_path.rsplit(".", 1)[0] + "_converted.dxf"
            try:
                subprocess.run([dwg2dxf, self.file_path, "-o", dxf_path],
                               check=True, timeout=120,
                               stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               text=True)
                return _parse_dxf(dxf_path)
            except (OSError, subprocess.SubprocessError) as exc:
                logger.warning("Conversion DWG échouée : %s", exc)

        # Fallback : erreur explicite
        result = IngestionResult(
            source=PlanSource.DWG,
            status=IngestionStatus.ERROR,
            file_path=self.file_path,
            file_hash=file_hash(self.file_path),
            errors=[
                "Fichier DWG détecté. Conversion automatique non disponible.",
                "Solutions : 1) Convertir en DXF avec LibreCAD/ODA File Converter,",
                "2) Exporter en PDF haute résolution depuis AutoCAD.",
            ],
        )
        return result

    def _parse_pdf(self) -> IngestionResult:
        """Analyse un PDF : vectoriel ET raster pour vision multimodale.

        Même si le PDF contient du texte vectoriel, on génère TOUJOURS
        les images 300 DPI pour l'inférence vision (plans graphiques
        avec texte dispersé graphiquement).
        """
        try:
            import fitz
        except ImportError:
            return self._parse_pdfplumber()

        result = IngestionResult(
            source=PlanSource.PDF_VECTORIEL,
            status=IngestionStatus.OK,
            file_path=self.file_path,
            file_hash=file_hash(self.file_path),
        )

        try:
            doc = fitz.open(self.file_path)
        except Exception as e:
            result.status = IngestionStatus.ERROR
            result.errors.append(f"Impossible d'ouvrir le PDF : {e}")
            return result

        result.pages = len(doc)
        has_vector = False
        has_raster = False

        for pi in range(len(doc)):
            page = doc[pi]
            has_raster = has_raster or bool(page.get_images(full=True))

            # Rendu adaptatif : les plans A0/A1 restent lisibles sans
            # produire des images démesurées en mémoire.
            try:
                from core.pdf_render import render_page_adaptive
                pix = render_page_adaptive(page, normal_dpi=DPI_RASTER)
                result.images.append(pix.tobytes("png"))
                result.metadata.setdefault("raster_pages", []).append(pi + 1)
            except Exception as exc:
                result.status = IngestionStatus.PARTIAL
                result.errors.append(
                    f"Rendu de la page {pi + 1} impossible : {exc}")

            # Aussi extraire le texte vectoriel si présent (bonus)
            text = page.get_text("text").strip()
            if len(text) > 0:
                blocks = page.get_text("dict")["blocks"]
                for block in blocks:
                    if block["type"] == 0:
                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                t = span["text"].strip()
                                if t:
                                    bbox = span["bbox"]
                                    result.text_blocks.append({
                                        "text": t,
                                        "x": round(bbox[0], 2),
                                        "y": round(bbox[1], 2),
                                        "x1": round(bbox[2], 2),
                                        "y1": round(bbox[3], 2),
                                        "page": pi + 1,
                                        "font_size": round(
                                            span.get("size", 0), 1),
                                    })
                                    result.drawing_elements.append(_drawing_element(
                                            t, bbox, pi + 1, "pymupdf",
                                            f"pdf-{pi + 1}-{len(result.drawing_elements) + 1}"))
                                    has_vector = True

        doc.close()

        if has_vector and has_raster:
            result.source = PlanSource.PDF_HYBRIDE
            result.metadata["mode"] = "hybrid"
        elif has_raster:
            result.source = PlanSource.PDF_RASTER
            result.metadata["mode"] = "raster"
        else:
            result.metadata["mode"] = "vector"
        result.drawing_elements = fuse_spatial_elements(result.drawing_elements)
        if result.images and not result.text_blocks:
            result.status = IngestionStatus.PARTIAL
        elif result.images:
            result.status = IngestionStatus.PARTIAL
        result.metadata["dependencies"] = optional_dependencies()

        return result

    def _parse_pdfplumber(self) -> IngestionResult:
        """Fallback texte léger quand PyMuPDF n'est pas disponible."""
        try:
            import pdfplumber
        except ImportError:
            return IngestionResult(
                source=PlanSource.PDF_VECTORIEL, status=IngestionStatus.ERROR,
                file_path=self.file_path, file_hash=file_hash(self.file_path),
                errors=["Aucun adaptateur PDF disponible. Installez "
                        "PyMuPDF ou pdfplumber (optionnels)."],
            )
        result = IngestionResult(
            source=PlanSource.PDF_VECTORIEL, status=IngestionStatus.PARTIAL,
            file_path=self.file_path, file_hash=file_hash(self.file_path),
            metadata={"mode": "vector", "adapter": "pdfplumber",
                      "warnings": ["Rendu raster indisponible sans PyMuPDF."]},
        )
        try:
            with pdfplumber.open(self.file_path) as doc:
                result.pages = len(doc.pages)
                for page_num, page in enumerate(doc.pages, 1):
                    for idx, word in enumerate(page.extract_words() or []):
                        text = (word.get("text") or "").strip()
                        if not text:
                            continue
                        bbox = (word.get("x0", 0), word.get("top", 0),
                                word.get("x1", 0), word.get("bottom", 0))
                        result.text_blocks.append({
                            "text": text, "x": bbox[0], "y": bbox[1],
                            "x1": bbox[2], "y1": bbox[3], "page": page_num,
                        })
                        result.drawing_elements.append(_drawing_element(
                            text, bbox, page_num, "pdfplumber",
                            f"pdf-{page_num}-{idx}"))
        except Exception as exc:
            result.status = IngestionStatus.ERROR
            result.errors.append(f"Lecture pdfplumber impossible : {exc}")
        result.drawing_elements = fuse_spatial_elements(result.drawing_elements)
        return result

    def ingest_to_json(self) -> dict:
        """Ingestion + conversion en dict JSON sérialisable."""
        result = self.ingest()
        return result.to_dict()
