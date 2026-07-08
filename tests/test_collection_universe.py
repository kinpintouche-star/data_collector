from __future__ import annotations

from ict.cli import _load_collection_plan, _plan_summary


def test_default_universe_has_40_assets_with_half_forex() -> None:
    plan = _load_collection_plan(
        "configs/universe_default_40.yaml",
        symbols=None,
        sources=None,
        group=None,
        limit=None,
    )

    assert len(plan) == 40
    assert sum(1 for item in plan if item["group"] == "forex") == 20
    assert {item["source"] for item in plan if item["group"] == "crypto"} == {"binance"}


def test_default_universe_summary_tracks_sources_and_forex_share() -> None:
    plan = _load_collection_plan(
        "configs/universe_default_40.yaml",
        symbols=None,
        sources=None,
        group=None,
        limit=None,
    )

    summary = _plan_summary(plan)

    assert summary["assets"] == 40
    assert summary["groups"]["forex"] == 20
    assert summary["sources"]["binance"] == 9
    assert summary["forex_share"] == 0.5
