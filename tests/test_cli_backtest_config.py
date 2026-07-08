from __future__ import annotations

from ict.cli import _fallback_tick_size


def test_fallback_tick_size_uses_asset_specific_defaults() -> None:
    assert _fallback_tick_size("EURUSD", "forex") == 0.00001
    assert _fallback_tick_size("USDJPY", "forex") == 0.001
    assert _fallback_tick_size("MNQ", "future") == 0.25
    assert _fallback_tick_size("GER40", "index_cfd") == 0.1
