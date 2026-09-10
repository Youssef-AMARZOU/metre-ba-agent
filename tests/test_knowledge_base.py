#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_knowledge_base.py — Tests du module RAG (Knowledge Base).
"""
from __future__ import annotations

import textwrap
import tempfile
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Sample OCR text (fake BTP content)
# ---------------------------------------------------------------------------
SAMPLE_OCR = textwrap.dedent("""\
    --- PAGE 1 ---
    TERRASSEMENTS
    Les terrassements comprennent les fouilles, les deblais et les remblais.
    La fouille d'une semelle isolée se calcule avec un encaissement de 0.20 m
    de chaque côté et une profondeur de propreté de 0.10 m.

    FONDATIONS
    Les fondations superficielles (semelles isolées, semelles filantes)
    transmettent les charges au sol. L'enrobage des armatures est de 5 cm minimum.

    --- PAGE 5 ---
    BÉTON ARMÉ
    Le béton armé est composé de béton et d'aciers d'armature.
    Le dosage minimum est de 250 kg/m3 pour les ouvrages enterrés.
    L'enrobage est de 5 cm pour les semelles, 3 cm pour les poteaux et poutres.

    Les formules de calcul :
    Volume béton = Longueur × Largeur × Hauteur (M3)
    Surface coffrage = Périmètre × Hauteur (M2)
    Poids acier = Nombre × Longueur × Masse linéaire (KG)

    --- PAGE 12 ---
    SEMELLES
    La semelle isolée a pour fonction de répartir la charge du poteau sur le sol.
    Dimensions typiques : A × B × H (en cm sur le plan).
    Le volume de béton de propreté = (A+0.10) × (B+0.10) × 0.10 M3.
    Le volume de fouille = (A+0.20) × (B+0.20) × (H+0.20) M3.
    Les aciers de recouvrement sont majorés de 50 φ (Manteau).

    --- PAGE 20 ---
    POUTRES
    La poutre reprend les charges de plancher et les transmet aux poteaux.
    Volume béton = b × h × Portée M3.
    Surface coffrage joues = 2 × h × Portée M2.
    Surface coffrage sous-face = b × Portée M2.
    Enrobage : 3 cm minimum.
    Ratio d'acier : 120-180 KG/M3.

    --- PAGE 25 ---
    COFFRAGES
    Le coffrage horizontal (sous-face de dalle) se mesure en M2.
    Le coffrage vertical (joues de poutre, joues de semelle) en M2.
    Majoration pour hauteur > 3.00 m (Série Centrale).

    --- PAGE 30 ---
    SÉRIE CENTRALE 1985
    Article 3/020 : Fourniture et pose des armatures.
    Article 3/021 : Bétonneau doseur, transport, mise en œuvre.
    Article 3/022 : Coffrage à raison de X M2/JourHomme.
    Article 3/023 : Décoffrage.
""")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def sample_ocr_file(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Ecrit le texte OCR sample dans un fichier temporaire."""
    d = tmp_path_factory.mktemp("ocr")
    f = d / "manteau_sample.txt"
    f.write_text(SAMPLE_OCR, encoding="utf-8")
    return str(f)


@pytest.fixture(scope="module")
def knowledge_base(sample_ocr_file: str):
    """Cree et peuple une base de connaissances de test."""
    from core.knowledge_base import MetreKnowledgeBase, ingest_manteau_ocr

    kb = MetreKnowledgeBase(db_path=str(Path(sample_ocr_file).parent / "db"))
    chunks = ingest_manteau_ocr(sample_ocr_file)
    assert len(chunks) > 0, "Aucun chunk ingere depuis le fichier sample"
    kb.ingest(chunks)
    return kb


@pytest.fixture(scope="module")
def rag_engine(knowledge_base):
    """Cree un moteur RAG avec la base de test."""
    from core.knowledge_base import MetreExpertRAG
    return MetreExpertRAG(kb=knowledge_base)


# ---------------------------------------------------------------------------
# Tests d'ingestion
# ---------------------------------------------------------------------------
class TestIngestion:
    """Tests de l'ingestion et du chunking BTP."""

    def test_ingest_returns_chunks(self, sample_ocr_file: str):
        from core.knowledge_base import ingest_manteau_ocr
        chunks = ingest_manteau_ocr(sample_ocr_file)
        assert isinstance(chunks, list)
        assert len(chunks) >= 1, f"Attendu >= 1 chunk, obtenu {len(chunks)}"

    def test_chunk_metadata_structure(self, sample_ocr_file: str):
        from core.knowledge_base import ingest_manteau_ocr
        chunks = ingest_manteau_ocr(sample_ocr_file)
        for c in chunks:
            assert "text" in c
            assert "metadata" in c
            meta = c["metadata"]
            assert "source" in meta
            assert meta["source"] == "Manteau"
            assert "primary_category" in meta
            assert "page_approx" in meta
            assert isinstance(meta["page_approx"], int)

    def test_chunk_categories_detected(self, sample_ocr_file: str):
        from core.knowledge_base import ingest_manteau_ocr
        chunks = ingest_manteau_ocr(sample_ocr_file)
        all_cats = set()
        for c in chunks:
            all_cats.update(c["metadata"]["categories"])
        # Doit detecter au moins ces categories du texte sample
        expected = {"SEMELLE", "POUTRE", "COFFRAGE", "TERRASSEMENT"}
        assert expected.issubset(all_cats), f"Manque : {expected - all_cats}"

    def test_ingest_missing_file(self):
        from core.knowledge_base import ingest_manteau_ocr
        chunks = ingest_manteau_ocr("/nonexistent/path.txt")
        assert chunks == []

    def test_ingest_empty_file(self, tmp_path: Path):
        from core.knowledge_base import ingest_manteau_ocr
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        chunks = ingest_manteau_ocr(str(f))
        assert chunks == []


# ---------------------------------------------------------------------------
# Tests du Vector Store
# ---------------------------------------------------------------------------
class TestVectorStore:
    """Tests de la base vectorielle ChromaDB."""

    def test_count(self, knowledge_base):
        assert knowledge_base.count > 0

    def test_search_semelle(self, knowledge_base):
        results = knowledge_base.search(
            "volume beton propreté semelle",
            category="SEMELLE",
            n_results=3,
        )
        assert len(results) > 0
        # Les resultats doivent etre related a semelle
        for r in results:
            assert "text" in r
            assert "relevance_score" in r

    def test_search_poutre(self, knowledge_base):
        results = knowledge_base.search(
            "coffrage poutre joues",
            category="POUTRE",
            n_results=3,
        )
        assert len(results) > 0

    def test_search_without_category(self, knowledge_base):
        results = knowledge_base.search(
            "recouvrement armatures",
            n_results=3,
        )
        assert len(results) > 0

    def test_search_empty_db(self, sample_ocr_file: str):
        from core.knowledge_base import MetreKnowledgeBase
        kb = MetreKnowledgeBase(
            db_path=str(Path(sample_ocr_file).parent / "empty_db")
        )
        results = kb.search("test query", n_results=3)
        assert results == []


# ---------------------------------------------------------------------------
# Tests du moteur RAG (sans LLM)
# ---------------------------------------------------------------------------
class TestRAGEngine:
    """Tests du moteur RAG (recherche uniquement, sans appel LLM)."""

    def test_query_rule_semelle(self, rag_engine):
        response = rag_engine.query_rule(
            "SEMELLE",
            "Comment calculer le volume du béton de propreté ?",
            use_llm=False,
        )
        assert isinstance(response, str)
        assert len(response) > 50
        # Doit contenir une reference au beton de propreté
        assert any(kw in response.lower() for kw in ["propret", "0.10", "volume"])

    def test_query_rule_poutre(self, rag_engine):
        response = rag_engine.query_rule(
            "POUTRE",
            "Quel est le ratio d'acier pour une poutre ?",
            use_llm=False,
        )
        assert isinstance(response, str)
        assert len(response) > 50

    def test_query_rule_not_found(self, rag_engine):
        response = rag_engine.query_rule(
            "INEXISTANT",
            "Question sur un element inexistant",
            use_llm=False,
        )
        # Avec une petite base (<10 chunks), le filtre est desactive
        # donc on recoit toujours un resultat. Verifier que c'est un
        # texte quelconque (pas une erreur).
        assert isinstance(response, str)
        assert len(response) > 0

    def test_get_ratio_fallback_semelle(self):
        from core.knowledge_base import get_ratio_or_rule_fallback
        result = get_ratio_or_rule_fallback("SEMELLE")
        assert result["element_type"] == "SEMELLE"
        assert "rules" in result
        assert "enrobage_cm" in result["rules"]
        assert result["rules"]["enrobage_cm"] == 5

    def test_get_ratio_fallback_poutre(self):
        from core.knowledge_base import get_ratio_or_rule_fallback
        result = get_ratio_or_rule_fallback("POUTRE")
        assert result["element_type"] == "POUTRE"
        assert "ratio_acier_kg_m3" in result["rules"]

    def test_get_ratio_fallback_unknown(self):
        from core.knowledge_base import get_ratio_or_rule_fallback
        result = get_ratio_or_rule_fallback("ELEMENT_INCONNU")
        assert "error" in result
