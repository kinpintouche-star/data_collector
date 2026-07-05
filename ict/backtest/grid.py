from __future__ import annotations

from copy import deepcopy
from itertools import product
from typing import Any

import yaml


def _set_nested(payload: dict[str, Any], key: str, value: Any) -> None:
    current = payload
    parts = key.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def expand_grid(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    base = config.get("base", {})
    grid = config.get("grid", {})
    if not grid:
        return [base]

    keys = list(grid)
    runs = []
    for values in product(*(grid[key] for key in keys)):
        params = deepcopy(base)
        for key, value in zip(keys, values):
            _set_nested(params, key, value)
        runs.append(params)
    return runs
