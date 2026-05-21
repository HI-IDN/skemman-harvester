from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import CollectionSeed


def load_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def iter_seeds(config: dict[str, Any]) -> list[CollectionSeed]:
    seeds: list[CollectionSeed] = []
    for inst in config.get("institutions", []):
        if not inst.get("enabled", True):
            continue
        for handle in inst.get("seed_handles", []):
            seeds.append(
                CollectionSeed(
                    institution=inst["name"],
                    short_name=inst["short_name"],
                    handle=handle,
                    target_labels=inst.get("target_labels", []),
                )
            )
    return seeds
