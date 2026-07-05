from __future__ import annotations

from pathlib import Path

from ict.backtest.grid import expand_grid
from ict.strategy.params import StrategyParams


def test_grid_expansion_is_strategy_params_compatible(tmp_path: Path) -> None:
    grid_path = tmp_path / "grid.yaml"
    grid_path.write_text(
        """
base:
  timezone: America/New_York
  execution:
    initial_balance: 100000
    fill_policy: signal_close
grid:
  strategy_mode:
    - A_INVALIDATION_S2
    - B_NO_S2_INVALIDATION
  pd_mode:
    - FVG
    - OB_SOLID
""",
        encoding="utf-8",
    )

    expanded = expand_grid(str(grid_path))
    params = [StrategyParams.model_validate(payload) for payload in expanded]

    assert len(params) == 4
    assert {item.strategy_mode for item in params} == {"A_INVALIDATION_S2", "B_NO_S2_INVALIDATION"}
    assert {item.pd_mode for item in params} == {"FVG", "OB_SOLID"}
