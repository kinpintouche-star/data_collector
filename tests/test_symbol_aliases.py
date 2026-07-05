from __future__ import annotations

from ict.data.symbols import resolve_alias_from_config


def test_symbol_alias_resolution() -> None:
    config = {
        "symbols": [
            {
                "symbol_code": "GER40",
                "aliases": [
                    {"source": "dukascopy", "source_symbol": "deuidxeur"},
                    {"source": "mt5", "source_symbol": "GER40.cash"},
                ],
            }
        ]
    }

    alias = resolve_alias_from_config(config, "GER40", "dukascopy")

    assert alias["source_symbol"] == "deuidxeur"
    assert alias["symbol_code"] == "GER40"
