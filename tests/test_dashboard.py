from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest

from ict.dashboard import app as dashboard_app
from ict.dashboard.data import dashboard_frame


def test_dashboard_frame_is_arrow_compatible() -> None:
    pa = pytest.importorskip("pyarrow")
    frame = pd.DataFrame(
        {
            "run_id": [uuid.uuid4()],
            "net_profit": [Decimal("11.5")],
            "metadata": [{"pd_type": "FVG"}],
        }
    )

    converted = dashboard_frame(frame)

    assert isinstance(converted.loc[0, "run_id"], str)
    assert converted.loc[0, "net_profit"] == 11.5
    assert converted.loc[0, "metadata"] == '{"pd_type": "FVG"}'
    pa.Table.from_pandas(converted)


def test_data_management_fetch_channel_mapping() -> None:
    assert dashboard_app._fetch_channel(pd.Series({"source_type": "dukascopy"})) == "Dukascopy"
    assert dashboard_app._fetch_channel(pd.Series({"source_type": "databento"})) == "Databento"
    assert dashboard_app._fetch_channel(pd.Series({"source_type": "binance_public"})) == "Neon"
    assert dashboard_app._fetch_channel(pd.Series({"source_type": "mt5"})) == "Neon"


def test_dukascopy_uses_latest_complete_utc_day() -> None:
    now = datetime(2026, 7, 7, 15, 45, tzinfo=timezone.utc)

    assert dashboard_app._latest_complete_utc_day(now) == datetime(2026, 7, 7, tzinfo=timezone.utc)
