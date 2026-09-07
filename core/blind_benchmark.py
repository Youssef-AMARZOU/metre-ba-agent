"""Reproducible blind benchmark inventory (metadata only, no raw data)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .external_datasets import DatasetSpec, inventory_digest, validate_dataset_structure


def build_blind_benchmark(
    specs: list[DatasetSpec], *, seed: int = 20260907
) -> dict[str, Any]:
    """Build a stable manifest whose labels remain outside the input inventory."""
    entries = []
    for spec in specs:
        report = validate_dataset_structure(spec)
        entries.append({
            "dataset_id": spec.dataset_id,
            "source": spec.kaggle_slug,
            "family": spec.family,
            "splits": {
                split: {"count": data["count"], "files": data["files"]}
                for split, data in report["splits"].items()
            },
            "inventory_sha256": inventory_digest(report),
            "status": report["status"],
        })
    payload = {"protocol": "blind-v1", "seed": seed, "entries": entries}
    payload["manifest_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return payload


def write_blind_benchmark(
    specs: list[DatasetSpec], output: str | Path, *, seed: int = 20260907
) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_blind_benchmark(specs, seed=seed), indent=2) + "\n",
        encoding="utf-8",
    )
    return path
