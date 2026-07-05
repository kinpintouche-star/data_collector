from __future__ import annotations

from typing import Any


def resolve_alias_from_config(symbols_config: dict[str, Any], symbol_code: str, source_name: str) -> dict[str, Any]:
    for symbol in symbols_config.get("symbols", []):
        if symbol.get("symbol_code") != symbol_code:
            continue
        for alias in symbol.get("aliases", []):
            if alias.get("source") == source_name:
                return {**alias, "symbol_code": symbol_code}
    raise ValueError(f"No alias in config for symbol={symbol_code}, source={source_name}.")
