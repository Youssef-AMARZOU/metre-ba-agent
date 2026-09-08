import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.local_extractor import VectorPlanExtractor, decompose_etiquette_technique
from core.normalization import normalize_plan_data


def test_tor_bar_is_decomposed_as_semelle_reinforcement():
    result = decompose_etiquette_technique("S1 6 TOR 10")
    assert result["reference"] == "S1"
    assert result["ferr_x"] == {"nb": 6, "phi": 10}


def test_dimensions_are_not_overwritten_by_later_reinforcement_page():
    extractor = VectorPlanExtractor()
    extractor._reset()
    extractor._parse_words_page([
        {"text": "S1 90 x 90 x 25", "x": 10, "y": 10, "page": 1},
    ], 1, role_auto="tableau")
    extractor._parse_words_page([
        {"text": "S1 6 TOR 10", "x": 10, "y": 10, "page": 2},
    ], 2, role_auto="tableau")
    assert extractor.global_catalogue["semelles"]["S1"]["a"] == 0.9
    assert extractor.global_catalogue["semelles"]["S1"]["h"] == 0.25
    assert extractor.global_catalogue["semelles"]["S1"]["ferr_x"] == {
        "nb": 6, "phi": 10}
    assert extractor._semelles_pages["S1"] == [1, 2]


def test_nomenclature_enriches_without_spatial_implantation():
    extractor = VectorPlanExtractor()
    data = extractor.extract_from_words([
        {"text": "Semelle S1 90 x 90 x 25", "x": 10, "y": 10, "page": 4},
        {"text": "S1 6 TOR 10", "x": 10, "y": 30, "page": 4},
    ])
    assert data["catalogue_types"]["semelles"]["S1"]["ferr_x"] == {
        "nb": 6, "phi": 10}
    assert data["implantations"]["semelles"] == []


def test_normalized_element_contract_and_excel_adapter():
    extractor = VectorPlanExtractor()
    data = extractor.extract_from_words([
        {"text": "S1 90 x 90 x 25", "x": 10, "y": 10, "page": 4},
        {"text": "A", "x": 100, "y": 20, "page": 4},
        {"text": "1", "x": 100, "y": 100, "page": 4},
        {"text": "S1", "x": 100, "y": 100, "page": 4},
    ])
    element = normalize_plan_data(data)["elements"][0]
    for key in ("reference", "family", "dimensions_m", "reinforcement",
                "quantity", "source", "confidence", "warnings"):
        assert key in element
