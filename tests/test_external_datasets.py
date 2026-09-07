import json
import os
import sys

import fitz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.blind_benchmark import build_blind_benchmark
from core.dataset_adapters import adapt_file
from core.external_datasets import (
    DatasetSpec,
    load_dataset_specs,
    validate_dataset_structure,
)


def test_config_declares_both_external_sources_without_downloading():
    specs = load_dataset_specs()
    assert {spec.kaggle_slug for spec in specs} == {
        "masterpn/rc-beams-dataset-v1-25-reinforced-concrete-beam",
        "masterpn/walls-dataset-v2-25-reinforced-wall",
    }
    assert all(spec.license == "CC BY-NC-SA 4.0" for spec in specs)


def test_missing_dataset_is_explicit_and_non_fatal(tmp_path):
    spec = DatasetSpec(
        "fixture", "masterpn/fixture", "POUTRE", "CC BY-NC-SA 4.0",
        str(tmp_path / "absent"), {}, "masterpn",
    )
    report = validate_dataset_structure(spec)
    assert report["status"] == "missing"
    assert report["errors"]


def test_structure_keeps_train_validation_test_separate(tmp_path):
    for split in ("train", "validation", "test"):
        folder = tmp_path / split
        folder.mkdir()
        (folder / f"project-{split}.png").write_bytes(b"fixture")
    spec = DatasetSpec(
        "fixture", "masterpn/fixture", "POUTRE", "CC BY-NC-SA 4.0",
        str(tmp_path), {}, "masterpn",
    )
    report = validate_dataset_structure(spec)
    assert report["status"] == "valid"
    assert [report["splits"][s]["count"] for s in ("train", "validation", "test")] == [1, 1, 1]


def test_pdf_adapter_maps_beam_and_wall_to_normalized_contract(tmp_path):
    path = tmp_path / "drawing.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 80), "POUTRE B12 250x500 4HA16")
    page.insert_text((50, 120), "VOILE V3 200x3000 12HA12")
    doc.save(path)
    doc.close()

    beams = adapt_file(path, source="fixture", project="project-a",
                       dataset_id="beam-fixture")
    assert [item["family"] for item in beams] == ["POUTRE", "VOILE"]
    assert beams[0]["dimensions_m"] == {"a": 0.25, "b": 0.5}
    assert beams[0]["reinforcement"][0]["diameter_mm"] == 16
    assert beams[1]["source"]["project"] == "project-a"
    assert beams[0]["bbox"] is not None


def test_blind_benchmark_is_stable_and_reports_missing_inputs():
    specs = load_dataset_specs()
    first = build_blind_benchmark(specs, seed=17)
    second = build_blind_benchmark(specs, seed=17)
    assert first == second
    assert first["protocol"] == "blind-v1"
    assert all(entry["status"] == "missing" for entry in first["entries"])
