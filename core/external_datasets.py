"""Kaggle dataset metadata, structure checks, and deterministic split handling.

This module deliberately never downloads data.  It validates a user-provided
local checkout and keeps the dataset's source/project/split identity explicit.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "external_datasets.yaml"


@dataclass(frozen=True)
class DatasetSpec:
    dataset_id: str
    kaggle_slug: str
    family: str
    license: str
    local_dir: str
    expected: dict[str, Any]
    attribution: str


def load_dataset_specs(path: str | Path = CONFIG_PATH) -> list[DatasetSpec]:
    """Load external dataset declarations without touching the network."""
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return [
        DatasetSpec(
            dataset_id=item["id"],
            kaggle_slug=item["kaggle_slug"],
            family=item["family"],
            license=item["license"],
            local_dir=item["local_dir"],
            expected=item.get("expected", {}),
            attribution=item["attribution"],
        )
        for item in payload.get("datasets", [])
    ]


def _files(root: Path) -> list[Path]:
    return sorted((p for p in root.rglob("*") if p.is_file()),
                  key=lambda p: p.relative_to(root).as_posix())


def validate_dataset_structure(
    spec: DatasetSpec, root: str | Path | None = None
) -> dict[str, Any]:
    """Return a JSON-serializable validation report for one local checkout.

    Missing data is an explicit, non-error ``missing`` status so CI and a
    clean clone can run the metadata/benchmark tests without raw datasets.
    """
    dataset_root = Path(root or spec.local_dir)
    report: dict[str, Any] = {
        "dataset_id": spec.dataset_id,
        "kaggle_slug": spec.kaggle_slug,
        "family": spec.family,
        "license": spec.license,
        "root": str(dataset_root),
        "status": "missing",
        "errors": [],
        "splits": {
            split: {"present": False, "files": [], "count": 0}
            for split in ("train", "validation", "test")
        },
    }
    if not dataset_root.is_dir():
        report["errors"].append("local dataset directory is absent")
        return report

    for split in ("train", "validation", "test"):
        split_root = dataset_root / split
        files = _files(split_root) if split_root.is_dir() else []
        report["splits"][split] = {
            "present": split_root.is_dir(),
            "files": [str(p.relative_to(dataset_root)) for p in files],
            "count": len(files),
        }
    metadata = dataset_root / "metadata.json"
    report["metadata_present"] = metadata.is_file()
    report["status"] = "valid"
    if not any(item["count"] for item in report["splits"].values()):
        report["status"] = "incomplete"
        report["errors"].append("no files found in train/validation/test")
    return report


def validate_all_datasets(
    specs: Iterable[DatasetSpec] | None = None,
) -> list[dict[str, Any]]:
    return [validate_dataset_structure(spec) for spec in specs or load_dataset_specs()]


def split_inventory(report: dict[str, Any]) -> dict[str, list[str]]:
    """Return stable, source-scoped inventories for benchmark inputs."""
    return {
        split: sorted(data.get("files", []))
        for split, data in report.get("splits", {}).items()
    }


def inventory_digest(report: dict[str, Any]) -> str:
    encoded = json.dumps(split_inventory(report), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
