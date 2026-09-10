#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_moroccan_conventions.py -- Conventions marocaines (R+2).

  - Poteaux prefixes Q (Q1..Q4) avec tableau '8T12 / 2CAD T6 e=15'
  - Etiquettes couplees (S1,Q1) / tolerance OCR (S4,04)
  - Poutres N / PN / PC avec sections (25x30)
  - Tolerance semelles implantees sans tableau de dimensions
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.local_extractor import VectorPlanExtractor, ExtractionError
from extract_plan import LocalPlanExtractor


# ============================================================================
# Helpers
# ============================================================================

def _tableau_poteaux_q(page):
    """Tableau borde : Niveaux | Q1 | Q2 | Q3 | Q4 avec cellules marocaines."""
    import fitz
    cols = [100, 220, 340, 460, 580]
    y0, rh = 100, 60
    y1 = y0 + rh * 3
    page.draw_rect(fitz.Rect(cols[0], y0, cols[-1], y1), width=1)
    for i, x in enumerate(cols):
        page.draw_line(fitz.Point(x, y0), fitz.Point(x, y1))
        if 0 < i < len(cols):
            pass
    for yl in (y0 + rh, y0 + 2 * rh):
        page.draw_line(fitz.Point(cols[0], yl), fitz.Point(cols[-1], yl))

    labels = ["Niveaux", "Q1", "Q2", "Q3", "Q4"]
    for i, lab in enumerate(labels):
        page.insert_text((cols[i] + 10, y0 + 35), lab)
    cell_txt = "(25x30)\n8T12\n2CAD T6 e=15"
    # insert_text ne gere pas \n : une ligne par niveau
    for row in range(2):
        yy = y0 + rh * (row + 1) + 20
        for i in range(1, 5):
            page.insert_text((cols[i] + 10, yy), "(25x30)")
            page.insert_text((cols[i] + 10, yy + 15), "8T12")
            page.insert_text((cols[i] + 10, yy + 30), "2CAD T6 e=15")


def _page_plan_groupe(page, nb=4):
    """Plan d'implantation : axes + etiquettes couplees (S1,Q1)..."""
    page.insert_text((600, 125), "1")
    page.insert_text((600, 228), "2")
    page.insert_text((597, 70), "A")
    page.insert_text((438, 70), "B")
    positions = [(100, 135), (300, 135), (100, 240), (300, 240)]
    for i in range(min(nb, len(positions))):
        x, y = positions[i]
        page.insert_text((x, y), f"(S{i + 1},Q{i + 1})")


# ============================================================================
# Poteaux Q (conventions marocaines)
# ============================================================================

class TestPoteauxQ:
    def test_tableau_poteaux_q_complet(self, tmp_path):
        """Tableau Q1..Q4 : (25x30) -> 0.25x0.30, 8T12, 2CAD T6 e=15."""
        import fitz
        pdf = tmp_path / "r2.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=1191, height=842)
        _tableau_poteaux_q(p1)
        p2 = doc.new_page(width=1191, height=842)
        p2.insert_text((600, 125), "1")
        p2.insert_text((100, 135), "S1(90x90x25)")
        doc.save(str(pdf))
        doc.close()

        data = LocalPlanExtractor().extract_from_pdf(str(pdf))
        pot = data["catalogue_types"]["poteaux"]
        assert "Q1" in pot and "Q4" in pot
        assert pot["Q1"]["a"] == 0.25 and pot["Q1"]["b"] == 0.30
        assert pot["Q1"]["long_bars"] == [{"nb": 8, "phi": 12}]
        assert pot["Q1"]["cadres"]["phi"] == 6
        assert pot["Q1"]["cadres"]["esp"] == 0.15

    def test_poteau_label_q_mot_isole(self):
        """Word 'Q1' isole : reconnu comme poteau (label elargi [PQ]\\d+)."""
        ex = VectorPlanExtractor()
        words = [{"text": "Q1", "x": 100, "y": 100, "page": 1},
                 {"text": "(25x30)", "x": 110, "y": 105, "page": 1},
                 {"text": "8T12", "x": 105, "y": 110, "page": 1},
                 {"text": "S1(90x90x25)", "x": 400, "y": 300, "page": 1}]
        data = ex.extract_from_words(words)
        pot = data["catalogue_types"]["poteaux"]["Q1"]
        assert pot["a"] == 0.25 and pot["b"] == 0.30
        assert pot["long_bars"] == [{"nb": 8, "phi": 12}]


# ============================================================================
# Etiquettes couplees (S1,Q1)
# ============================================================================

class TestEtiquettesGroupees:
    def test_groupe_S_Q_implante_semelle_et_poteau(self, tmp_path):
        """(S1,Q1) sur le plan : semelle S1 implantee + poteau Q1 positionne
        sur les memes axes + liaison tracee."""
        import fitz
        pdf = tmp_path / "groupe.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=1191, height=842)
        _page_plan_groupe(p1, nb=4)
        p2 = doc.new_page(width=1191, height=842)
        _tableau_poteaux_q(p2)
        doc.save(str(pdf))
        doc.close()

        data = LocalPlanExtractor().extract_from_pdf(str(pdf))
        impl = data["implantations"]["semelles"]
        assert len(impl) == 4
        assert {i["type"] for i in impl} == {"S1", "S2", "S3", "S4"}
        # Poteaux positionnes sur les vrais axes (proches de x=438..600)
        pot_impl = {p["type"]: (p["axe"], p["file"])
                    for p in data["implantations"]["poteaux"]}
        assert "Q1" in pot_impl
        assert pot_impl["Q1"][0] in ("A", "B")
        assert pot_impl["Q1"][1] in ("1", "2")
        # Liaison semelle-poteau tracee
        liens = data["_meta"]["liens_semelle_poteau"]
        assert {"semelle": "S1", "poteau": "Q1", "page": 1} in liens

    def test_tolerance_ocr_zero_pour_q(self, tmp_path):
        """(S4,04) OCR-degrade -> poteau Q4."""
        import fitz
        pdf = tmp_path / "ocr_degrade.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=1191, height=842)
        p1.insert_text((600, 125), "1")
        p1.insert_text((597, 70), "A")
        p1.insert_text((100, 135), "(S4,04)")
        doc.save(str(pdf))
        doc.close()

        data = LocalPlanExtractor().extract_from_pdf(str(pdf))
        assert "Q4" in data["catalogue_types"]["poteaux"]
        assert data["_meta"]["liens_semelle_poteau"] == [
            {"semelle": "S4", "poteau": "Q4", "page": 1}]


# ============================================================================
# Poutres N / PN / PC
# ============================================================================

class TestPoutresMarocaines:
    def test_labels_pn_et_pc_mots_isoles(self):
        ex = VectorPlanExtractor()
        words = [
            {"text": "PN1", "x": 500, "y": 200, "page": 1},
            {"text": "20x40", "x": 490, "y": 180, "page": 1},
            {"text": "PC", "x": 700, "y": 300, "page": 1},
            {"text": "30x50", "x": 690, "y": 280, "page": 1},
            {"text": "S1(90x90x25)", "x": 100, "y": 400, "page": 1},
        ]
        data = ex.extract_from_words(words)
        pou = data["catalogue_types"]["poutres"]
        assert pou["PN1"]["b"] == 0.20 and pou["PN1"]["h"] == 0.40
        assert pou["PC"]["b"] == 0.30 and pou["PC"]["h"] == 0.50

    def test_section_inline_n2(self):
        """Ligne 'N2 (25x30)' -> section 0.25 x 0.30."""
        ex = VectorPlanExtractor()
        words = [{"text": "N2 (25x30)", "x": 100, "y": 100, "page": 1},
                 {"text": "S1(90x90x25)", "x": 400, "y": 300, "page": 1}]
        data = ex.extract_from_words(words)
        pou = data["catalogue_types"]["poutres"]["N2"]
        assert pou["b"] == 0.25 and pou["h"] == 0.30

    def test_label_n_bis(self):
        ex = VectorPlanExtractor()
        words = [{"text": "N3BIS", "x": 100, "y": 100, "page": 1},
                 {"text": "20x35", "x": 105, "y": 90, "page": 1},
                 {"text": "S1(90x90x25)", "x": 400, "y": 300, "page": 1}]
        data = ex.extract_from_words(words)
        pou = data["catalogue_types"]["poutres"]["N3BIS"]
        assert pou["b"] == 0.20 and pou["h"] == 0.35

    def test_repere_isole_lg_ch_sans_section(self):
        """LG1 / CH sans section lue : types conserves dans les bons catalogues
        (longrines/chainages) avec flag 'dimensions_manquantes'."""
        ex = VectorPlanExtractor()
        words = [{"text": "LG1", "x": 100, "y": 100, "page": 1},
                 {"text": "CH", "x": 250, "y": 100, "page": 1},
                 {"text": "S1(90x90x25)", "x": 400, "y": 300, "page": 1}]
        data = ex.extract_from_words(words)
        cat = data["catalogue_types"]
        lg = cat.get("longrines", {})
        ch = cat.get("chainages", {})
        assert lg.get("LG1", {}).get("dimensions_manquantes") is True
        assert ch.get("CH", {}).get("dimensions_manquantes") is True


# ============================================================================
# Tolerance : semelles implantees sans tableau de dimensions
# ============================================================================

class TestSemellesSansDimensions:
    def test_semelles_implantees_sans_dims_conservees(self, tmp_path):
        """16 etiquettes S1..S4 sans dims + poteaux Q connus :
        AUCUNE erreur, semelles conservees avec 'Dimensions à renseigner'."""
        import fitz
        pdf = tmp_path / "sans_tableau.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=1191, height=842)
        # 4 etiquettes couplees -> semelles implantees sans dims
        _page_plan_groupe(p1, nb=4)
        p2 = doc.new_page(width=1191, height=842)
        _tableau_poteaux_q(p2)
        doc.save(str(pdf))
        doc.close()

        data = LocalPlanExtractor().extract_from_pdf(str(pdf))
        sem = data["catalogue_types"]["semelles"]
        assert {"S1", "S2", "S3", "S4"} <= set(sem.keys())
        for tk in ("S1", "S2", "S3", "S4"):
            assert sem[tk]["dimensions_manquantes"] is True
            assert sem[tk]["a"] == 0
        assert any("Dimensions à renseigner" in w
                   for w in data["_meta"]["avertissements"])
        # Poteaux integralement connus -> livrables generables
        from core.local_extractor import verifier_livrables_ou_lever
        assert verifier_livrables_ou_lever(data) is True

    def test_catalogue_vide_reste_en_erreur(self):
        """Aucun element du tout -> toujours ExtractionError."""
        ex = VectorPlanExtractor()
        with pytest.raises(ExtractionError):
            ex.extract_from_words([])


# ============================================================================
# Indexation multi-instances + groupes multi-armatures
# ============================================================================

class TestPoteauxMultiInstances:
    def test_meme_type_q_compile_plusieurs_ids_uniques(self, tmp_path):
        """3 etiquettes (S1,Q1) : 3 instances Q1_1, Q1_2, Q1_3 — jamais
        d'ecrasement (17 poteaux attendus sur le dossier R+2)."""
        import fitz
        pdf = tmp_path / "multi_inst.pdf"
        doc = fitz.open()
        p1 = doc.new_page(width=1191, height=842)
        p1.insert_text((600, 125), "1")
        p1.insert_text((600, 228), "2")
        p1.insert_text((597, 70), "A")
        p1.insert_text((438, 70), "B")
        p1.insert_text((100, 135), "(S1,Q1)")
        p1.insert_text((400, 135), "(S2,Q1)")
        p1.insert_text((100, 240), "(S3,Q1)")
        doc.save(str(pdf))
        doc.close()

        data = LocalPlanExtractor().extract_from_pdf(str(pdf))
        ids = [p["id"] for p in data["implantations"]["poteaux"]]
        assert ids == ["Q1_1", "Q1_2", "Q1_3"]
        assert len(set(ids)) == len(ids)

    def test_cellule_4t12_plus_4t10_multi_groupes(self, tmp_path):
        """Cellule 'Poteau Q2 (25x30) 4T12+4T10 2CAD T6 e=15' :
        aciers_longitudinaux + section_str + cadres_str."""
        pdf = tmp_path / "multi_groupes.pdf"

        def p1(page):
            import fitz
            page.draw_rect(fitz.Rect(100, 100, 420, 200), width=1)
            page.draw_line(fitz.Point(260, 100), fitz.Point(260, 200))
            page.draw_line(fitz.Point(100, 150), fitz.Point(420, 150))
            page.insert_text((110, 135), "Poteaux")
            page.insert_text((270, 135), "Q2")
            # Meme colonne Q2 : toutes les infos sous le repere
            page.insert_text((270, 172), "Poteau Q2 (25x30) 4T12+4T10")
            page.insert_text((270, 187), "2CAD T6 e=15")

        def p2(page):
            page.insert_text((600, 125), "1")
            page.insert_text((100, 135), "S1(90x90x25)")

        doc_rows = []
        import fitz
        doc = fitz.open()
        pa = doc.new_page(width=1191, height=842)
        p1(pa)
        pb = doc.new_page(width=1191, height=842)
        p2(pb)
        doc.save(str(pdf))
        doc.close()

        data = LocalPlanExtractor().extract_from_pdf(str(pdf))
        spec = data["catalogue_types"]["poteaux"]["Q2"]
        assert spec["a"] == 0.25 and spec["b"] == 0.30
        assert spec["section_str"] == "25x30"
        assert spec["aciers_longitudinaux"] == [
            {"nb": 4, "phi": 12}, {"nb": 4, "phi": 10}]
        assert spec["long_bars"] == [
            {"nb": 4, "phi": 12}, {"nb": 4, "phi": 10}]
        assert spec["cadres"]["phi"] == 6
        assert spec["cadres"]["esp"] == 0.15
        assert spec["cadres_str"] == "T6 e=15"

    def test_structure_cible_page5_maroc(self, tmp_path):
        """Structure cible exacte de la mission pour un Q du tableau."""
        pdf = tmp_path / "cible.pdf"

        def p1(page):
            page.insert_text((100, 200),
                             "Poteau Q3 (25x30) 8T12 2CAD T6 e=15")

        def p2(page):
            page.insert_text((600, 125), "1")
            page.insert_text((100, 135), "S1(90x90x25)")

        doc_rows = []
        import fitz
        doc = fitz.open()
        pa = doc.new_page(width=1191, height=842)
        p1(pa)
        pb = doc.new_page(width=1191, height=842)
        p2(pb)
        doc.save(str(pdf))
        doc.close()

        data = LocalPlanExtractor().extract_from_pdf(str(pdf))
        spec = data["catalogue_types"]["poteaux"]["Q3"]
        attendu = {"a": 0.25, "b": 0.30, "section_str": "25x30",
                   "aciers_longitudinaux": [{"nb": 8, "phi": 12}],
                   "cadres": "T6 e=15"}
        for k, v in attendu.items():
            if k == "cadres":
                assert spec["cadres_str"] == v
            else:
                assert spec[k] == v


    def test_dedupe_groupes_identiques_cellule_fusionnee(self):
        """Cellule find_tables fusionnant 2 details identiques (RDC +
        mezzanine) : un seul groupe conserve, pas de double comptage."""
        from core.local_extractor import VectorPlanExtractor
        ex = VectorPlanExtractor()
        ex._reset()
        cell = "(25x35)\n6HA14\nCad T6+Ep T6\n6x8+e=15\n(25x35)\n6HA14"
        assert ex._parse_poteau_cell("P1", cell) is True
        spec = ex.global_catalogue["poteaux"]["P1"]
        assert spec["long_bars"] == [{"nb": 6, "phi": 14}]
        assert spec["aciers_longitudinaux"] == [{"nb": 6, "phi": 14}]

    def test_groupes_distincts_tous_conserves(self):
        """'4T12+4T10' : les deux groupes distincts sont conserves."""
        from core.local_extractor import VectorPlanExtractor
        ex = VectorPlanExtractor()
        ex._reset()
        assert ex._parse_poteau_cell(
            "Q3", "(25x30)\n4T12+4T10\n2CAD T6 e=15") is True
        spec = ex.global_catalogue["poteaux"]["Q3"]
        assert spec["long_bars"] == [{"nb": 4, "phi": 12},
                                     {"nb": 4, "phi": 10}]


# ============================================================================
# Porte R+2 si present
# ============================================================================

class TestDossierR2:
    REF = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "reference", "ilide.info-plan-ba-r-2-pr_ea99ea203cb65f5982309fe45f53d811.pdf")

    @pytest.mark.skipif(not os.path.exists(REF), reason="dossier R+2 absent")
    def test_poteaux_q_detectes(self):
        data = LocalPlanExtractor().extract_from_pdf(self.REF)
        pot = data["catalogue_types"]["poteaux"]
        assert {"Q1", "Q2", "Q3", "Q4"} <= set(pot.keys())
        assert len(data["implantations"]["semelles"]) >= 1
