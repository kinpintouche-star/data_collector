from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path


OUTPUT = Path("data/raw/synthetic_ger40_m1.csv")


def main() -> None:
    rows = synthetic_rows()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["DateTime", "Open", "High", "Low", "Close", "Volume"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {OUTPUT}")


def synthetic_rows() -> list[dict[str, float | int | str]]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bars = []
    for offset in range(240):
        timestamp = start + timedelta(minutes=offset)
        bars.append(
            {
                "DateTime": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "Open": 100.0,
                "High": 101.0,
                "Low": 99.0,
                "Close": 100.0,
                "Volume": 10,
            }
        )

    def setbar(timestamp: str, **values: float) -> None:
        target = datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc)
        idx = int((target - start).total_seconds() // 60)
        for key, value in values.items():
            bars[idx][key] = value

    setbar("2025-01-01 00:10:00", High=110)
    setbar("2025-01-01 00:20:00", Low=90)
    setbar("2025-01-01 00:59:00", Close=100)
    setbar("2025-01-01 01:05:00", High=112)
    setbar("2025-01-01 01:30:00", High=109)
    setbar("2025-01-01 01:59:00", Close=99)
    setbar("2025-01-01 02:00:00", Low=90)
    setbar("2025-01-01 02:15:00", Low=80)
    setbar("2025-01-01 02:30:00", Low=88)
    setbar("2025-01-01 02:08:00", Open=104, High=104, Low=103, Close=103.5)
    setbar("2025-01-01 02:10:00", Open=98, High=99, Low=96, Close=97)
    setbar("2025-01-01 02:50:00", Open=104, High=104, Low=100, Close=101)
    setbar("2025-01-01 02:55:00", Open=100, High=101, Low=89, Close=90)
    return bars


if __name__ == "__main__":
    main()
