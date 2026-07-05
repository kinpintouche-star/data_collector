from __future__ import annotations

import pandas as pd


def summarize_trades(trades: pd.DataFrame) -> dict[str, float | int | None]:
    if trades.empty:
        return {
            "total_trades": 0,
            "total_wins": 0,
            "total_losses": 0,
            "winrate": None,
            "avg_rr": None,
            "median_rr": None,
            "profit_factor": None,
            "expectancy": None,
            "net_profit": 0.0,
            "max_consecutive_losses": 0,
            "avg_trade_duration_seconds": None,
            "best_trade": None,
            "worst_trade": None,
        }
    pnl = pd.to_numeric(trades["pnl"], errors="coerce").fillna(0.0)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    total_trades = int(len(trades))
    total_wins = int(len(wins))
    total_losses = int(len(losses))
    gross_profit = float(wins.sum())
    gross_loss = float(abs(losses.sum()))
    rr = pd.to_numeric(trades["rr"], errors="coerce") if "rr" in trades else pd.Series(dtype=float)
    durations = None
    if {"entry_time", "exit_time"}.issubset(trades.columns):
        entry = pd.to_datetime(trades["entry_time"], utc=True)
        exit_ = pd.to_datetime(trades["exit_time"], utc=True)
        durations = (exit_ - entry).dt.total_seconds()
    return {
        "total_trades": total_trades,
        "total_wins": total_wins,
        "total_losses": total_losses,
        "winrate": total_wins / total_trades if total_trades else None,
        "avg_rr": float(rr.mean()) if not rr.empty else None,
        "median_rr": float(rr.median()) if not rr.empty else None,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "expectancy": float(pnl.mean()) if total_trades else None,
        "net_profit": float(pnl.sum()),
        "max_consecutive_losses": max_consecutive_losses(pnl),
        "avg_trade_duration_seconds": float(durations.mean()) if durations is not None else None,
        "best_trade": float(pnl.max()) if total_trades else None,
        "worst_trade": float(pnl.min()) if total_trades else None,
    }


def max_consecutive_losses(pnl: pd.Series) -> int:
    max_run = 0
    current = 0
    for value in pnl:
        if value < 0:
            current += 1
            max_run = max(max_run, current)
        else:
            current = 0
    return max_run
