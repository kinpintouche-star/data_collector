from __future__ import annotations

import uuid
from decimal import Decimal

import pandas as pd
import pytest

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
