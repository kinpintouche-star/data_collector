from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class LiveSource:
    symbol_code: str
    source_name: str
    provider: str
    source_symbol: str
    provider_symbol: str | None
    fallback_provider: str | None
    fallback_provider_symbol: str | None
    timeframe: str
    poll_interval_minutes: int
    retention_days: int
    enabled: bool
    priority: int
    collection_mode: str = "daily"
    dataset: str | None = None
    schema: str | None = None
    max_cost_usd: float | None = None
    pending_reason: str | None = None


def load_live_sources(path: str | Path = "configs/live_sources.yaml") -> list[LiveSource]:
    config_path = Path(path)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    defaults = {
        "timeframe": payload.get("default_timeframe", "M1"),
        "poll_interval_minutes": int(payload.get("default_poll_interval_minutes", 1440)),
        "retention_days": int(payload.get("default_retention_days", 30)),
        "collection_mode": payload.get("default_collection_mode", "daily"),
    }
    sources = []
    for row in payload.get("assets", []):
        merged = {**defaults, **row}
        sources.append(
            LiveSource(
                symbol_code=str(merged["symbol_code"]),
                source_name=str(merged["source_name"]),
                provider=str(merged["provider"]),
                source_symbol=str(merged["source_symbol"]),
                provider_symbol=str(merged["provider_symbol"]) if merged.get("provider_symbol") else None,
                fallback_provider=str(merged["fallback_provider"]) if merged.get("fallback_provider") else None,
                fallback_provider_symbol=str(merged["fallback_provider_symbol"])
                if merged.get("fallback_provider_symbol")
                else None,
                timeframe=str(merged["timeframe"]).upper(),
                poll_interval_minutes=int(merged["poll_interval_minutes"]),
                retention_days=int(merged["retention_days"]),
                enabled=bool(merged.get("enabled", True)),
                priority=int(merged.get("priority", 100)),
                collection_mode=str(merged.get("collection_mode", defaults["collection_mode"])),
                dataset=str(merged["dataset"]) if merged.get("dataset") else None,
                schema=str(merged["schema"]) if merged.get("schema") else None,
                max_cost_usd=float(merged["max_cost_usd"]) if merged.get("max_cost_usd") is not None else None,
                pending_reason=str(merged["pending_reason"]) if merged.get("pending_reason") else None,
            )
        )
    return sources
