from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


UTC = timezone.utc


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def convert_utc_to(value: datetime, tz_name: str) -> datetime:
    return ensure_utc(value).astimezone(ZoneInfo(tz_name))
