from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Pair:
    name: str
    official: str
    api: str
    api_provider: str
    workspace: str | None = None
    title: str | None = None
    title_mode: str = "auto"


def load_pairs(path: Path) -> list[Pair]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Pair(**item) for item in data.get("pairs", [])]


def save_pairs(path: Path, pairs: list[Pair]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"pairs": [pair.__dict__ for pair in pairs]}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pair_names(pairs: list[Pair]) -> set[str]:
    return {pair.name for pair in pairs}


def paired_ids(pairs: list[Pair]) -> set[str]:
    ids: set[str] = set()
    for pair in pairs:
        ids.add(pair.official)
        ids.add(pair.api)
    return ids
