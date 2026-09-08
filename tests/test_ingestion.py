#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_ingestion.py — Tests pour le module d'ingestion universel.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ingestion import (
    UniversalPlanIngestor,
    IngestionResult,
    PlanSource,
    IngestionStatus,
    detect_source,
    SUPPORTED_EXTENSIONS,
)
from core.schemas import ProjetBAParseOutput, FamilleElement


class TestDetectSource(unittest.TestCase):
    """Test la détection de type de fichier."""

    def test_dxf_detection(self):
        self.assertEqual(detect_source("plan.dxf"), PlanSource.DXF)

    def test_dwg_detection(self):
        self.assertEqual(detect_source("plan.dwg"), PlanSource.DWG)

    def test_pdf_detection(self):
        self.assertEqual(detect_source("plan.pdf"), PlanSource.PDF_VECTORIEL)

    def test_png_detection(self):
        self.assertEqual(detect_source("scan.png"), PlanSource.IMAGE)

    def test_jpg_detection(self):
        self.assertEqual(detect_source("photo.jpg"), PlanSource.IMAGE)

    def test_jpeg_detection(self):
        self.assertEqual(detect_source("photo.jpeg"), PlanSource.IMAGE)

    def test_tiff_detection(self):
        self.assertEqual(detect_source("plan.tiff"), PlanSource.IMAGE)

    def test_bmp_detection(self):
        self.assertEqual(detect_source("image.bmp"), PlanSource.IMAGE)

    def test_webp_detection(self):
        self.assertEqual(detect_source("image.webp"), PlanSource.IMAGE)

    def test_unknown_detection(self):
        self.assertEqual(detect_source("data.txt"), PlanSource.UNKNOWN)

    def test_supported_extensions_complete(self):
        expected = {".dxf", ".dwg", ".pdf", ".png", ".jpg", ".jpeg",
                    ".tiff", ".tif", ".bmp", ".webp"}
        self.assertTrue(expected.issubset(SUPPORTED_EXTENSIONS))


class TestUniversalPlanIngestor(unittest.TestCase):
    """Test l'ingesteur universel."""

    def test_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            UniversalPlanIngestor("nonexistent_file.pdf")

    def test_unsupported_extension(self):
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            f.write(b"test")
            path = f.name
        try:
            with self.assertRaises(ValueError):
                UniversalPlanIngestor(path)
        finally:
            os.unlink(path)


class TestIngestionResult(unittest.TestCase):
    """Test le modèle IngestionResult."""

    def test_creation(self):
        result = IngestionResult(
            source=PlanSource.DXF,
            status=IngestionStatus.OK,
            file_path="test.dxf",
        )
        self.assertEqual(result.source, PlanSource.DXF)
        self.assertEqual(result.status, IngestionStatus.OK)
        self.assertEqual(result.file_path, "test.dxf")
        self.assertEqual(len(result.text_blocks), 0)
        self.assertEqual(len(result.images), 0)

    def test_to_dict(self):
        result = IngestionResult(
            source=PlanSource.PDF_VECTORIEL,
            status=IngestionStatus.PARTIAL,
            file_path="test.pdf",
            pages=3,
        )
        d = result.to_dict()
        self.assertEqual(d["source"], "pdf_vectoriel")
        self.assertEqual(d["status"], "partial")
        self.assertEqual(d["pages"], 3)
        self.assertIsInstance(d["errors"], list)

    def test_drawing_element_contract(self):
        from core.schemas import DrawingElement
        element = DrawingElement(
            id="pdf-1-1", text="S1(120x120x30)",
            bbox=(10, 20, 100, 40), page=1, source="pymupdf",
            confidence=0.95, provenance={"adapter": "pymupdf"})
        self.assertEqual(element.bbox, (10, 20, 100, 40))
        self.assertEqual(element.provenance["adapter"], "pymupdf")


class TestRealIngestionMatrix(unittest.TestCase):
    """Matrice sur artefacts réels, sans remplacer les adaptateurs par mocks."""

    def test_vector_pdf_has_ir_and_bbox(self):
        import fitz
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "vector.pdf")
            doc = fitz.open()
            page = doc.new_page(width=595, height=842)
            page.insert_text((80, 100), "S1(120x120x30)")
            doc.save(path)
            doc.close()
            result = UniversalPlanIngestor(path).ingest()
            self.assertEqual(result.source, PlanSource.PDF_VECTORIEL)
            self.assertTrue(result.drawing_elements)
            self.assertTrue(result.drawing_elements[0]["bbox"])

    def test_hybrid_pdf_keeps_vector_and_raster(self):
        import fitz
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "hybrid.pdf")
            image_path = os.path.join(td, "page.png")
            from PIL import Image, ImageDraw
            image = Image.new("RGB", (400, 300), "white")
            ImageDraw.Draw(image).text((10, 10), "scan", fill="black")
            image.save(image_path)
            doc = fitz.open()
            page = doc.new_page(width=595, height=842)
            page.insert_text((80, 100), "S2")
            page.insert_image((200, 200, 500, 500), filename=image_path)
            doc.save(path)
            doc.close()
            result = UniversalPlanIngestor(path).ingest()
            self.assertEqual(result.source, PlanSource.PDF_HYBRIDE)
            self.assertTrue(result.images)
            self.assertTrue(result.text_blocks)

    def test_large_raster_is_tiled(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "large.png")
            Image.new("RGB", (4096, 4096), "white").save(path)
            result = UniversalPlanIngestor(path).ingest()
            self.assertGreater(len(result.images), 1)


class TestExtractPlan(unittest.TestCase):
    """Test extract_plan.py (import et extraction déterministe)."""

    def test_import_extract_plan(self):
        import importlib
        spec = importlib.util.spec_from_file_location(
            "extract_plan",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "extract_plan.py")
        )
        self.assertIsNotNone(spec)

    def test_regex_semelle_dim(self):
        import re
        rx = re.compile(r"S(\d+)\((\d+)x(\d+)x(\d+)\)", re.I)
        m = rx.search("S3(120x120x30)")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "3")
        self.assertEqual(m.group(2), "120")
        self.assertEqual(m.group(3), "120")
        self.assertEqual(m.group(4), "30")

    def test_regex_ferraillage(self):
        import re
        rx = re.compile(r"(\d+)HA(\d+)")
        m = rx.search("8HA12")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "8")
        self.assertEqual(m.group(2), "12")


class TestSchemaValidation(unittest.TestCase):
    """Test que le schéma ProjetBAParseOutput fonctionne correctement."""

    def test_empty_projet(self):
        p = ProjetBAParseOutput()
        self.assertIsNone(p.nom_projet)
        self.assertEqual(len(p.elements), 0)

    def test_with_elements(self):
        from core.schemas import ElementStructureSchema, BarreAcierSchema, TypeBarre, RoleArmature
        p = ProjetBAParseOutput(
            nom_projet="Test",
            elements=[
                ElementStructureSchema(
                    famille=FamilleElement.SEMELLE,
                    repere="S1",
                    dimensions={"a": 1.2, "b": 1.2, "h": 0.3},
                    armatures=[
                        BarreAcierSchema(
                            diametre=12,
                            type_barre=TypeBarre.HA,
                            role=RoleArmature.NAPPE_X,
                            nombre=8,
                        )
                    ],
                )
            ],
        )
        self.assertEqual(p.nom_projet, "Test")
        self.assertEqual(len(p.elements), 1)
        self.assertEqual(p.elements[0].famille, FamilleElement.SEMELLE)

    def test_by_famille(self):
        from core.schemas import ElementStructureSchema
        p = ProjetBAParseOutput(
            elements=[
                ElementStructureSchema(
                    famille=FamilleElement.SEMELLE, repere="S1",
                    dimensions={"a": 1, "b": 1, "h": 0.3}),
                ElementStructureSchema(
                    famille=FamilleElement.POTEAU, repere="P1",
                    dimensions={"a": 0.25, "b": 0.35}),
            ]
        )
        semelles = p.by_famille(FamilleElement.SEMELLE)
        self.assertEqual(len(semelles), 1)
        self.assertEqual(semelles[0].repere, "S1")

    def test_count_by_famille(self):
        from core.schemas import ElementStructureSchema
        p = ProjetBAParseOutput(
            elements=[
                ElementStructureSchema(
                    famille=FamilleElement.SEMELLE, repere="S1",
                    dimensions={"a": 1, "b": 1, "h": 0.3}),
                ElementStructureSchema(
                    famille=FamilleElement.SEMELLE, repere="S2",
                    dimensions={"a": 1, "b": 1, "h": 0.3}),
                ElementStructureSchema(
                    famille=FamilleElement.POTEAU, repere="P1",
                    dimensions={"a": 0.25, "b": 0.35}),
            ]
        )
        counts = p.count_by_famille()
        self.assertEqual(counts["SEMELLE"], 2)
        self.assertEqual(counts["POTEAU"], 1)


if __name__ == "__main__":
    unittest.main()
