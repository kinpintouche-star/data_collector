from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yaml
from sqlalchemy import text

from ict.core.config import get_settings
from ict.data.ingest import ingest_market_data
from ict.dashboard.data import DASHBOARD_QUERIES, PAGES, dashboard_frame
from ict.db.session import build_engine
from ict.live.config import load_live_sources
from ict.live.sync import sync_remote_candles


ALL_BOTS = "All bots"
ALL_VALUES = "All"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UNIVERSE_PATH = PROJECT_ROOT / "configs" / "universe_default_40.yaml"


@dataclass(frozen=True)
class BotSelection:
    label: str
    run_ids: set[str]
    dataset_ids: set[str]
    parameter_set_ids: set[str]
    symbol: str = ALL_VALUES
    source: str = ALL_VALUES


@st.cache_resource
def dashboard_engine():
    return build_engine(get_settings().database_url)


@st.cache_data(ttl=30)
def read_sql(query: str) -> pd.DataFrame:
    return dashboard_frame(pd.read_sql(query, dashboard_engine()))


@st.cache_data(ttl=30)
def read_sql_params(query: str, params: dict) -> pd.DataFrame:
    return dashboard_frame(pd.read_sql(text(query), dashboard_engine(), params=params))


def live_remote_database_url() -> str | None:
    return os.getenv("LIVE_REMOTE_DATABASE_URL") or get_settings().live_remote_database_url


def apply_theme() -> None:
    st.markdown(
        """
        <style>
        .stApp {
            background:
                linear-gradient(180deg, #f7fbff 0%, #eef4f7 42%, #f9faf7 100%);
            color: #172026;
        }
        section[data-testid="stSidebar"] {
            background: #10181d;
            border-right: 1px solid rgba(42, 125, 145, 0.24);
        }
        section[data-testid="stSidebar"] * {
            color: #f3f8fa;
        }
        div[data-testid="stMetric"] {
            background: rgba(255, 255, 255, 0.82);
            border: 1px solid rgba(26, 77, 96, 0.12);
            border-radius: 8px;
            padding: 14px 16px;
            box-shadow: 0 8px 24px rgba(17, 32, 39, 0.06);
        }
        div[data-testid="stDataFrame"] {
            border: 1px solid rgba(26, 77, 96, 0.10);
            border-radius: 8px;
            overflow: hidden;
        }
        .bot-title {
            font-size: 30px;
            line-height: 1.15;
            font-weight: 700;
            color: #13272f;
            margin: 0 0 4px 0;
        }
        .bot-subtitle {
            color: #4d636d;
            font-size: 14px;
            margin: 0 0 18px 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def load_dashboard_data() -> dict[str, pd.DataFrame]:
    return {page: read_sql(query) for page, query in DASHBOARD_QUERIES.items()}


@st.cache_data(ttl=30)
def load_universe_targets(path: str = str(DEFAULT_UNIVERSE_PATH)) -> pd.DataFrame:
    universe_path = Path(path)
    if not universe_path.exists():
        return pd.DataFrame(columns=["symbol_code", "source_name", "group"])
    payload = yaml.safe_load(universe_path.read_text(encoding="utf-8")) or {}
    rows = []
    for asset in payload.get("assets", []):
        source_value = asset.get("sources", asset.get("source"))
        if isinstance(source_value, str):
            sources = [source.strip() for source in source_value.split(",") if source.strip()]
        else:
            sources = [str(source) for source in source_value or []]
        for source in sources:
            rows.append({"symbol_code": asset["symbol"], "source_name": source, "group": asset.get("group")})
    return pd.DataFrame.from_records(rows)


def build_selection(runs: pd.DataFrame, bot_label: str, symbol: str, source: str) -> BotSelection:
    selected = runs.copy()
    if bot_label != ALL_BOTS and not selected.empty:
        name, version = bot_label.split(" / ", 1)
        selected = selected[(selected["strategy_name"] == name) & (selected["strategy_version"] == version)]
    if symbol != ALL_VALUES and not selected.empty:
        selected = selected[selected["symbol_code"] == symbol]
    if source != ALL_VALUES and not selected.empty:
        selected = selected[selected["source_name"] == source]

    return BotSelection(
        label=bot_label,
        run_ids=set(selected["run_id"].astype(str)) if "run_id" in selected else set(),
        dataset_ids=set(selected["dataset_id"].astype(str)) if "dataset_id" in selected else set(),
        parameter_set_ids=set(selected["parameter_set_id"].astype(str)) if "parameter_set_id" in selected else set(),
        symbol=symbol,
        source=source,
    )


def filter_by_ids(frame: pd.DataFrame, column: str, ids: set[str]) -> pd.DataFrame:
    if frame.empty or column not in frame or not ids:
        return frame.iloc[0:0] if column in frame else frame
    return frame[frame[column].astype(str).isin(ids)]


def selected_runs(runs: pd.DataFrame, selection: BotSelection) -> pd.DataFrame:
    return filter_by_ids(runs, "run_id", selection.run_ids)


def selected_datasets(datasets: pd.DataFrame, selection: BotSelection) -> pd.DataFrame:
    return filter_by_ids(datasets, "dataset_id", selection.dataset_ids)


def selected_parameters(parameters: pd.DataFrame, selection: BotSelection) -> pd.DataFrame:
    return filter_by_ids(parameters, "parameter_set_id", selection.parameter_set_ids)


def metric_value(value, digits: int = 2):
    if pd.isna(value):
        return "-"
    if isinstance(value, float):
        return round(value, digits)
    return value


def render_shell_header(runs: pd.DataFrame, selection: BotSelection) -> None:
    title = selection.label if selection.label != ALL_BOTS else "Bot Lab"
    st.markdown(f'<div class="bot-title">{title}</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="bot-subtitle">{len(runs)} selected runs across '
        f'{runs["symbol_code"].nunique() if not runs.empty else 0} symbols</div>',
        unsafe_allow_html=True,
    )


def render_overview(data: dict[str, pd.DataFrame], selection: BotSelection) -> None:
    runs = selected_runs(data["Runs"], selection)
    trades = filter_by_ids(data["Trades"], "run_id", selection.run_ids)
    equity = filter_by_ids(data["Performance"], "run_id", selection.run_ids)
    sources = filter_by_ids(data["Sources"], "run_id", selection.run_ids)

    render_shell_header(runs, selection)
    cols = st.columns(5)
    cols[0].metric("Runs", len(runs))
    cols[1].metric("Trades", int(runs["total_trades"].fillna(0).sum()) if not runs.empty else 0)
    cols[2].metric("Net PnL", metric_value(runs["net_profit"].fillna(0).sum() if not runs.empty else 0))
    cols[3].metric("Winrate", metric_value(runs["winrate"].dropna().mean() if not runs.empty else None))
    cols[4].metric("Max DD", metric_value(runs["max_drawdown_pct"].dropna().min() if not runs.empty else None))

    left, right = st.columns([1.3, 1])
    with left:
        if equity.empty:
            st.info("No equity curve data for this selection.")
        else:
            st.plotly_chart(
                px.line(equity, x="time", y="equity", color="run_id", title="Equity by run"),
                width="stretch",
            )
    with right:
        if sources.empty:
            st.info("No source performance data for this selection.")
        else:
            source_rollup = (
                sources.groupby(["symbol_code", "source_name"], as_index=False)
                .agg(trades=("trades", "sum"), pnl=("pnl", "sum"), avg_rr=("avg_rr", "mean"))
                .sort_values("pnl", ascending=False)
            )
            st.plotly_chart(
                px.bar(source_rollup, x="symbol_code", y="pnl", color="source_name", title="PnL by source"),
                width="stretch",
            )

    if not trades.empty:
        st.dataframe(
            trades[["entry_time", "symbol_code", "source_name", "direction", "pnl", "rr", "pd_type", "exit_reason"]],
            width="stretch",
        )


def _run_label(row) -> str:
    created = row.created_at.strftime("%Y-%m-%d %H:%M") if hasattr(row.created_at, "strftime") else row.created_at
    return (
        f"{row.symbol_code} / {row.source_name} | {row.strategy_name} {row.strategy_version} | "
        f"{created} | {str(row.run_id)[:8]}"
    )


@st.cache_data(ttl=30)
def load_run_trades(run_id: str) -> pd.DataFrame:
    return read_sql_params(
        """
        SELECT
            t.id AS trade_id,
            t.run_id,
            t.setup_id,
            t.direction,
            t.entry_time,
            t.entry_price,
            t.exit_time,
            t.exit_price,
            t.volume,
            t.sl,
            t.tp,
            t.exit_reason,
            t.pnl,
            t.pnl_points,
            t.rr,
            t.pd_type,
            t.strategy_mode,
            t.session_name,
            t.metadata,
            s.symbol_code,
            ds.name AS source_name
        FROM trades t
        JOIN symbols s ON s.id = t.symbol_id
        JOIN data_sources ds ON ds.id = t.source_id
        WHERE t.run_id = CAST(:run_id AS uuid)
        ORDER BY t.entry_time
        """,
        {"run_id": run_id},
    )


@st.cache_data(ttl=30)
def load_run_candles(
    run_id: str,
    start_time,
    end_time,
    limit: int,
) -> pd.DataFrame:
    if start_time is None or end_time is None:
        query = """
            WITH scoped AS (
                SELECT
                    c.time_open,
                    c.open,
                    c.high,
                    c.low,
                    c.close,
                    c.tick_volume,
                    c.real_volume,
                    c.spread,
                    c.source_symbol
                FROM backtest_runs r
                JOIN datasets d ON d.id = r.dataset_id
                JOIN market_candles c
                    ON c.symbol_id = r.symbol_id
                    AND c.source_id = r.source_id
                    AND c.timeframe = d.timeframe
                WHERE r.id = CAST(:run_id AS uuid)
                    AND c.time_open >= r.start_time
                    AND c.time_open <= r.end_time
                ORDER BY c.time_open DESC
                LIMIT :limit
            )
            SELECT * FROM scoped ORDER BY time_open
        """
        params = {"run_id": run_id, "limit": limit}
    else:
        query = """
            SELECT
                c.time_open,
                c.open,
                c.high,
                c.low,
                c.close,
                c.tick_volume,
                c.real_volume,
                c.spread,
                c.source_symbol
            FROM backtest_runs r
            JOIN datasets d ON d.id = r.dataset_id
            JOIN market_candles c
                ON c.symbol_id = r.symbol_id
                AND c.source_id = r.source_id
                AND c.timeframe = d.timeframe
            WHERE r.id = CAST(:run_id AS uuid)
                AND c.time_open >= :start_time
                AND c.time_open <= :end_time
            ORDER BY c.time_open
            LIMIT :limit
        """
        params = {
            "run_id": run_id,
            "start_time": pd.Timestamp(start_time).to_pydatetime(),
            "end_time": pd.Timestamp(end_time).to_pydatetime(),
            "limit": limit,
        }
    return read_sql_params(query, params)


@st.cache_data(ttl=30)
def load_run_events(run_id: str, start_time, end_time) -> pd.DataFrame:
    return read_sql_params(
        """
        SELECT
            e.id AS event_id,
            e.run_id,
            e.setup_id,
            e.event_type,
            e.event_time,
            e.direction,
            e.price,
            e.state_before,
            e.state_after,
            e.metadata
        FROM setup_events e
        WHERE e.run_id = CAST(:run_id AS uuid)
            AND e.event_time >= :start_time
            AND e.event_time <= :end_time
        ORDER BY e.event_time
        """,
        {
            "run_id": run_id,
            "start_time": pd.Timestamp(start_time).to_pydatetime(),
            "end_time": pd.Timestamp(end_time).to_pydatetime(),
        },
    )


def _trade_focus_options(trades: pd.DataFrame) -> tuple[list[int], dict[int, str]]:
    options = [0]
    labels = {0: "Full run, latest candles"}
    for idx, row in enumerate(trades.itertuples(), start=1):
        pnl = f"{row.pnl:.2f}" if pd.notna(row.pnl) else "-"
        labels[idx] = f"Trade {idx} | {row.direction} | {row.entry_time} | PnL {pnl}"
        options.append(idx)
    return options, labels


def _focus_window(trades: pd.DataFrame, focus_index: int):
    if focus_index == 0 or trades.empty:
        return None, None, None
    row = trades.iloc[focus_index - 1]
    entry_time = pd.Timestamp(row["entry_time"])
    exit_time = pd.Timestamp(row["exit_time"]) if pd.notna(row.get("exit_time")) else entry_time
    start_time = entry_time - timedelta(hours=4)
    end_time = exit_time + timedelta(hours=2)
    return start_time, end_time, row


def _visible_window(frame: pd.DataFrame, time_column: str, start_time, end_time) -> pd.DataFrame:
    if frame.empty or time_column not in frame:
        return frame
    times = pd.to_datetime(frame[time_column])
    return frame[(times >= start_time) & (times <= end_time)]


def render_trade_chart(
    candles: pd.DataFrame,
    trades: pd.DataFrame,
    events: pd.DataFrame,
    event_types: list[str],
    focus_trade,
) -> None:
    candles = candles.copy()
    candles["time_open"] = pd.to_datetime(candles["time_open"])
    start_time = candles["time_open"].min()
    end_time = candles["time_open"].max()
    visible_trades = trades.copy()
    if not visible_trades.empty:
        visible_trades["entry_time"] = pd.to_datetime(visible_trades["entry_time"])
        visible_trades["exit_time"] = pd.to_datetime(visible_trades["exit_time"])
        visible_trades = visible_trades[
            ((visible_trades["entry_time"] >= start_time) & (visible_trades["entry_time"] <= end_time))
            | ((visible_trades["exit_time"] >= start_time) & (visible_trades["exit_time"] <= end_time))
        ]

    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=candles["time_open"],
            open=candles["open"],
            high=candles["high"],
            low=candles["low"],
            close=candles["close"],
            name="M1 candles",
            increasing_line_color="#11825f",
            decreasing_line_color="#c44545",
            increasing_fillcolor="#5cc8a6",
            decreasing_fillcolor="#f08d86",
        )
    )

    for direction, color, marker_symbol, name in [
        ("bullish", "#0f9f6e", "triangle-up", "Bullish entry"),
        ("bearish", "#d64d4d", "triangle-down", "Bearish entry"),
    ]:
        entries = visible_trades[visible_trades["direction"] == direction] if not visible_trades.empty else pd.DataFrame()
        if entries.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=entries["entry_time"],
                y=entries["entry_price"],
                mode="markers",
                name=name,
                marker=dict(symbol=marker_symbol, size=13, color=color, line=dict(color="#ffffff", width=1)),
                customdata=entries[["setup_id", "pnl", "rr", "pd_type"]].to_numpy(),
                hovertemplate=(
                    "Entry %{x}<br>"
                    "Price %{y}<br>"
                    "Setup %{customdata[0]}<br>"
                    "PnL %{customdata[1]}<br>"
                    "RR %{customdata[2]}<br>"
                    "PD %{customdata[3]}<extra></extra>"
                ),
            )
        )

    exits = visible_trades.dropna(subset=["exit_time", "exit_price"]) if not visible_trades.empty else pd.DataFrame()
    if not exits.empty:
        exit_colors = ["#0f9f6e" if (pnl or 0) > 0 else "#d64d4d" for pnl in exits["pnl"]]
        fig.add_trace(
            go.Scatter(
                x=exits["exit_time"],
                y=exits["exit_price"],
                mode="markers",
                name="Exits",
                marker=dict(symbol="x", size=12, color=exit_colors, line=dict(width=2)),
                customdata=exits[["setup_id", "exit_reason", "pnl", "rr"]].to_numpy(),
                hovertemplate=(
                    "Exit %{x}<br>"
                    "Price %{y}<br>"
                    "Setup %{customdata[0]}<br>"
                    "Reason %{customdata[1]}<br>"
                    "PnL %{customdata[2]}<br>"
                    "RR %{customdata[3]}<extra></extra>"
                ),
            )
        )

    visible_events = events.copy()
    if not visible_events.empty:
        visible_events["event_time"] = pd.to_datetime(visible_events["event_time"])
        visible_events = visible_events[
            visible_events["event_type"].isin(event_types)
            & visible_events["price"].notna()
            & (visible_events["event_time"] >= start_time)
            & (visible_events["event_time"] <= end_time)
        ]
    if not visible_events.empty:
        event_palette = {
            "H1_SIGNAL": "#46535b",
            "M15_DOUBLE_SWING_VALIDATED": "#2d7dd2",
            "LEG_FOUND": "#2081c3",
            "OB_SELECTED": "#6b5ca5",
            "FVG_SELECTED": "#b56576",
            "PD_TOUCHED": "#f4a261",
            "REJECTION_CONFIRMED": "#2a9d8f",
            "TRADE_OPENED": "#111827",
            "TRADE_CLOSED_TP": "#0f9f6e",
            "TRADE_CLOSED_SL": "#d64d4d",
            "RISK_REJECTED": "#8c2f39",
        }
        fig.add_trace(
            go.Scatter(
                x=visible_events["event_time"],
                y=visible_events["price"],
                mode="markers",
                name="Setup triggers",
                marker=dict(
                    symbol="diamond",
                    size=8,
                    color=[event_palette.get(event, "#59656f") for event in visible_events["event_type"]],
                    line=dict(color="#ffffff", width=0.5),
                ),
                customdata=visible_events[
                    ["event_type", "setup_id", "direction", "state_before", "state_after", "metadata"]
                ].to_numpy(),
                hovertemplate=(
                    "%{customdata[0]} %{x}<br>"
                    "Price %{y}<br>"
                    "Setup %{customdata[1]}<br>"
                    "Direction %{customdata[2]}<br>"
                    "%{customdata[3]} -> %{customdata[4]}<br>"
                    "%{customdata[5]}<extra></extra>"
                ),
            )
        )

    if focus_trade is not None:
        fig.add_hline(y=float(focus_trade["entry_price"]), line_dash="solid", line_color="#495057", annotation_text="Entry")
        fig.add_hline(y=float(focus_trade["sl"]), line_dash="dot", line_color="#d64d4d", annotation_text="SL")
        fig.add_hline(y=float(focus_trade["tp"]), line_dash="dot", line_color="#0f9f6e", annotation_text="TP")

    fig.update_layout(
        template="plotly_white",
        height=720,
        margin=dict(l=20, r=20, t=36, b=20),
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    fig.update_xaxes(showgrid=True, gridcolor="rgba(23, 32, 38, 0.08)")
    fig.update_yaxes(showgrid=True, gridcolor="rgba(23, 32, 38, 0.08)")
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False, "scrollZoom": True})


def render_chart(data: dict[str, pd.DataFrame], selection: BotSelection) -> None:
    runs = selected_runs(data["Runs"], selection)
    render_shell_header(runs, selection)
    if runs.empty:
        st.info("No completed run for this selection yet.")
        return

    runs = runs.sort_values("created_at", ascending=False)
    labels = {str(row.run_id): _run_label(row) for row in runs.itertuples()}
    run_id = st.selectbox("Run", list(labels), format_func=lambda value: labels[value])
    trades = load_run_trades(run_id)
    focus_options, focus_labels = _trade_focus_options(trades)
    default_focus = 1 if len(focus_options) > 1 else 0

    controls = st.columns([1.2, 0.8])
    focus_index = controls[0].selectbox(
        "Focus",
        focus_options,
        index=default_focus,
        format_func=lambda value: focus_labels[value],
    )
    candle_limit = controls[1].slider("Candles", min_value=300, max_value=5000, value=2500, step=100)
    start_time, end_time, focus_trade = _focus_window(trades, focus_index)
    candles = load_run_candles(run_id, start_time, end_time, candle_limit)
    if candles.empty:
        st.warning("No candles available for this run/window.")
        return

    event_start = pd.to_datetime(candles["time_open"]).min()
    event_end = pd.to_datetime(candles["time_open"]).max()
    events = load_run_events(run_id, event_start, event_end)
    default_events = [
        "H1_SIGNAL",
        "OB_SELECTED",
        "FVG_SELECTED",
        "PD_TOUCHED",
        "REJECTION_CONFIRMED",
        "TRADE_OPENED",
        "TRADE_CLOSED_TP",
        "TRADE_CLOSED_SL",
    ]
    available_events = sorted(events["event_type"].dropna().unique()) if not events.empty else []
    event_types = st.multiselect(
        "Triggers",
        available_events,
        default=[event for event in default_events if event in available_events],
    )
    render_trade_chart(candles, trades, events, event_types, focus_trade)

    left, right = st.columns([1, 1])
    with left:
        st.dataframe(
            _visible_window(trades, "entry_time", event_start, event_end),
            width="stretch",
        )
    with right:
        st.dataframe(events, width="stretch")


def render_runs(data: dict[str, pd.DataFrame], selection: BotSelection) -> None:
    runs = selected_runs(data["Runs"], selection)
    render_shell_header(runs, selection)
    st.dataframe(runs, width="stretch")


def render_coverage(data: dict[str, pd.DataFrame], selection: BotSelection) -> None:
    runs = selected_runs(data["Runs"], selection)
    coverage = data["Coverage"].copy()
    gaps = data.get("Gaps", pd.DataFrame()).copy()
    targets = load_universe_targets()
    if selection.symbol != ALL_VALUES and "symbol_code" in coverage:
        coverage = coverage[coverage["symbol_code"] == selection.symbol]
        if not gaps.empty:
            gaps = gaps[gaps["symbol_code"] == selection.symbol]
        if not targets.empty:
            targets = targets[targets["symbol_code"] == selection.symbol]
    if selection.source != ALL_VALUES and "source_name" in coverage:
        coverage = coverage[coverage["source_name"] == selection.source]
        if not gaps.empty:
            gaps = gaps[gaps["source_name"] == selection.source]
        if not targets.empty:
            targets = targets[targets["source_name"] == selection.source]

    render_shell_header(runs, selection)
    if not coverage.empty:
        coverage["first_candle_time"] = pd.to_datetime(coverage["first_candle_time"])
        coverage["last_candle_time"] = pd.to_datetime(coverage["last_candle_time"])
        coverage["days_covered"] = (
            coverage["last_candle_time"] - coverage["first_candle_time"]
        ).dt.total_seconds() / 86400
        coverage["flag_rate"] = coverage["flagged_candles"] / coverage["candle_rows"].replace(0, pd.NA)

    cols = st.columns(4)
    cols[0].metric("Assets", coverage["symbol_code"].nunique() if not coverage.empty else 0)
    cols[1].metric("Candles", int(coverage["candle_rows"].fillna(0).sum()) if not coverage.empty else 0)
    cols[2].metric("Sources", coverage["source_name"].nunique() if not coverage.empty else 0)
    cols[3].metric("Max Days", metric_value(coverage["days_covered"].max()) if not coverage.empty else "-")

    gap_cols = st.columns(3)
    gap_cols[0].metric("Gap Events", int(gaps["gap_events"].fillna(0).sum()) if not gaps.empty else 0)
    gap_cols[1].metric("Missing M1", int(gaps["missing_candles"].fillna(0).sum()) if not gaps.empty else 0)
    gap_cols[2].metric("Largest Gap", int(gaps["largest_gap_candles"].fillna(0).max()) if not gaps.empty else 0)

    if not targets.empty:
        target = targets.merge(coverage, on=["symbol_code", "source_name"], how="left")
        target["days_covered"] = target["days_covered"].fillna(0)
        target["candle_rows"] = target["candle_rows"].fillna(0)
        target["target_ok"] = target["days_covered"] >= 180
        target_cols = st.columns(4)
        target_cols[0].metric("Target Assets", target["symbol_code"].nunique())
        target_cols[1].metric("With Data", int((target["candle_rows"] > 0).sum()))
        target_cols[2].metric(">= 180 Days", int(target["target_ok"].sum()))
        target_cols[3].metric("Forex Targets", int((target["group"] == "forex").sum()))
        st.plotly_chart(
            px.bar(
                target.groupby("group", as_index=False).agg(
                    targets=("symbol_code", "count"),
                    with_data=("candle_rows", lambda values: int((values > 0).sum())),
                    target_ok=("target_ok", "sum"),
                ),
                x="group",
                y=["targets", "with_data", "target_ok"],
                barmode="group",
                title="Universe collection progress",
            ),
            width="stretch",
        )

    if coverage.empty:
        st.info("No candles stored yet for this market selection.")
        if not targets.empty:
            st.dataframe(targets, width="stretch")
        return

    left, right = st.columns([1.15, 0.85])
    with left:
        st.plotly_chart(
            px.bar(
                coverage.sort_values("candle_rows", ascending=False),
                x="symbol_code",
                y="candle_rows",
                color="source_name",
                title="Stored candle rows by asset",
            ),
            width="stretch",
        )
    with right:
        st.plotly_chart(
            px.scatter(
                coverage,
                x="first_candle_time",
                y="last_candle_time",
                color="asset_type",
                size="candle_rows",
                hover_data=["symbol_code", "source_name", "timeframe", "sample_source_symbol"],
                title="Historical coverage window",
            ),
            width="stretch",
        )

    st.dataframe(
        coverage[
            [
                "symbol_code",
                "asset_type",
                "source_name",
                "timeframe",
                "candle_rows",
                "first_candle_time",
                "last_candle_time",
                "last_ingested_at",
                "sample_source_symbol",
                "flagged_candles",
                "flag_rate",
                "avg_spread",
            ]
        ],
        width="stretch",
    )
    if not gaps.empty:
        st.dataframe(
            gaps[
                [
                    "symbol_code",
                    "source_name",
                    "timeframe",
                    "gap_events",
                    "missing_candles",
                    "largest_gap_candles",
                    "latest_gap_before",
                ]
            ],
            width="stretch",
        )


def _utc_timestamp(value) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.to_pydatetime()


def _source_config_value(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        import json

        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _build_data_management_table(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    targets = load_universe_targets()
    coverage = data.get("Coverage", pd.DataFrame()).copy()
    live = data.get("Live Collector", pd.DataFrame()).copy()
    sources = data.get("Data Sources", pd.DataFrame()).copy()

    if targets.empty:
        return pd.DataFrame()

    base = targets.rename(columns={"source_name": "local_source"}).copy()
    if not coverage.empty:
        coverage = coverage[coverage["timeframe"].astype(str).str.upper() == "M1"].copy()
        base = base.merge(
            coverage[
                [
                    "symbol_code",
                    "source_name",
                    "asset_type",
                    "timeframe",
                    "candle_rows",
                    "first_candle_time",
                    "last_candle_time",
                    "last_ingested_at",
                    "flagged_candles",
                    "sample_source_symbol",
                ]
            ].rename(columns={"source_name": "local_source"}),
            on=["symbol_code", "local_source"],
            how="left",
        )
    else:
        base["candle_rows"] = 0
        base["first_candle_time"] = pd.NaT
        base["last_candle_time"] = pd.NaT
        base["last_ingested_at"] = pd.NaT
        base["flagged_candles"] = 0

    if not sources.empty:
        source_meta = sources.rename(columns={"source_name": "local_source"})
        base = base.merge(source_meta, on="local_source", how="left")

    if not live.empty:
        live = live.copy()
        live["neon_last_candle_time"] = pd.to_datetime(live["last_candle_time"], errors="coerce", utc=True)
        live_rollup = (
            live.groupby("symbol_code", as_index=False)
            .agg(
                neon_last_candle_time=("neon_last_candle_time", "max"),
                neon_enabled=("enabled", "max"),
                neon_status=("status", lambda values: ", ".join(sorted({str(value) for value in values if pd.notna(value)}))),
                neon_sources=("source_name", lambda values: ", ".join(sorted({str(value) for value in values if pd.notna(value)}))),
            )
        )
        base = base.merge(live_rollup, on="symbol_code", how="left")

    if "source_type" not in base:
        base["source_type"] = None
    if "neon_enabled" not in base:
        base["neon_enabled"] = False
    if "neon_status" not in base:
        base["neon_status"] = None
    if "neon_last_candle_time" not in base:
        base["neon_last_candle_time"] = pd.NaT

    base["candle_rows"] = pd.to_numeric(base["candle_rows"], errors="coerce").fillna(0).astype(int)
    base["flagged_candles"] = pd.to_numeric(base["flagged_candles"], errors="coerce").fillna(0).astype(int)
    base["local_last"] = pd.to_datetime(base["last_candle_time"], errors="coerce", utc=True)
    base["neon_last"] = pd.to_datetime(base["neon_last_candle_time"], errors="coerce", utc=True)
    first_candle = pd.to_datetime(base["first_candle_time"], errors="coerce", utc=True)
    base["days_local"] = (
        (base["local_last"] - first_candle).dt.total_seconds()
        / 86400
    )
    base["missing_from_neon_min"] = (
        (base["neon_last"] - base["local_last"]).dt.total_seconds() / 60
    ).where(base["neon_last"].notna() & base["local_last"].notna())
    base["fetch_channel"] = base.apply(_fetch_channel, axis=1)
    base["fetch_action"] = base.apply(_fetch_label, axis=1)
    base["needs_attention"] = (base["candle_rows"] == 0) | (base["missing_from_neon_min"].fillna(0) > 0)
    return base.sort_values(["needs_attention", "group", "symbol_code", "local_source"], ascending=[False, True, True, True])


def _selected_data_row(table: pd.DataFrame) -> pd.Series | None:
    if table.empty:
        return None
    labels = {
        index: (
            f"{row.symbol_code} / {row.local_source} | rows {int(row.candle_rows)} | "
            f"local {row.local_last if pd.notna(row.local_last) else '-'}"
        )
        for index, row in table.iterrows()
    }
    selected_index = st.selectbox("Asset source", list(labels), format_func=lambda index: labels[index])
    return table.loc[selected_index]


def _missing_start(row: pd.Series, fallback_days: int, overlap_minutes: int) -> datetime:
    local_last = _utc_timestamp(row.get("local_last"))
    if local_last:
        return local_last - timedelta(minutes=overlap_minutes)
    return datetime.now(timezone.utc) - timedelta(days=fallback_days)


def _fetch_channel(row: pd.Series) -> str:
    source_type = str(row.get("source_type") or "").lower()
    if source_type == "dukascopy":
        return "Dukascopy"
    if source_type == "databento":
        return "Databento"
    return "Neon"


def _fetch_label(row: pd.Series) -> str:
    return f"{row['symbol_code']} / {row['local_source']} ({_fetch_channel(row)})"


def _latest_complete_utc_day(now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


def _json_rows_from_stdout(stdout: str) -> list[dict]:
    rows = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{") or not line.endswith("}"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "symbol" in payload:
            rows.append(payload)
    return rows


def _run_dukascopy_node_fetch(
    symbol_code: str,
    source_name: str,
    start: datetime,
    until: datetime,
    retries: int,
) -> dict:
    if until <= start:
        return {
            "symbol_code": symbol_code,
            "source_name": source_name,
            "channel": "Dukascopy",
            "status": "skipped",
            "message": "No complete UTC day is missing.",
        }

    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "collect_dukascopy_node.py"),
        "--symbols",
        symbol_code,
        "--source",
        source_name,
        "--from",
        start.isoformat(),
        "--to",
        until.isoformat(),
        "--timeframe",
        "M1",
        "--skip-existing",
        "--continue-on-error",
        "--no-datasets",
        "--retries",
        str(retries),
    ]
    completed = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True)
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(message[-1200:] or f"Dukascopy command failed with exit code {completed.returncode}.")

    summaries = _json_rows_from_stdout(completed.stdout)
    summary = summaries[-1] if summaries else {}
    if summary.get("status") == "failed":
        raise RuntimeError(str(summary.get("error") or "Dukascopy fetch failed."))
    return {
        "symbol_code": symbol_code,
        "source_name": source_name,
        "channel": "Dukascopy",
        "status": "completed",
        "rows_fetched": summary.get("rows_fetched"),
        "rows_inserted": summary.get("rows_inserted"),
        "rows_updated": summary.get("rows_updated"),
        "rows_skipped": summary.get("rows_skipped"),
    }


def _fetch_missing_for_row(
    row: pd.Series,
    fallback_days: int,
    overlap_minutes: int,
    neon_limit: int,
    max_cost: float,
    dukascopy_retries: int,
    remote_url: str | None,
    now: datetime | None = None,
) -> dict:
    symbol_code = str(row["symbol_code"])
    local_source = str(row["local_source"])
    channel = _fetch_channel(row)
    start = _missing_start(row, fallback_days, overlap_minutes)
    now_until = now or datetime.now(timezone.utc)

    if channel == "Dukascopy":
        until = _latest_complete_utc_day(now_until)
        return _run_dukascopy_node_fetch(symbol_code, local_source, start, until, dukascopy_retries)

    if channel == "Databento":
        result = ingest_market_data(
            symbol_code,
            local_source,
            "M1",
            start,
            now_until,
            max_cost_usd=max_cost,
        )
        return {
            "symbol_code": symbol_code,
            "source_name": local_source,
            "channel": "Databento",
            "status": "completed",
            "rows_fetched": result["rows_fetched"],
            "rows_inserted": result["rows_inserted"],
            "rows_updated": result["rows_updated"],
            "rows_skipped": result["rows_skipped"],
            "rows_written": result["rows_written"],
        }

    if not remote_url:
        raise RuntimeError("LIVE_REMOTE_DATABASE_URL is not configured.")
    neon_until = _utc_timestamp(row.get("neon_last"))
    result = sync_remote_candles(
        remote_url,
        since=start,
        until=neon_until,
        symbols=[symbol_code],
        limit=neon_limit,
    )
    return {
        "symbol_code": symbol_code,
        "source_name": local_source,
        "channel": "Neon",
        "status": "completed",
        "rows_read": result.rows_read,
        "rows_inserted": result.rows_inserted,
        "rows_updated": result.rows_updated,
        "rows_written": result.rows_written,
    }


def _api_usage_rows(table: pd.DataFrame) -> pd.DataFrame:
    channels = table.copy()
    channels["fetch_channel"] = channels.apply(_fetch_channel, axis=1)
    grouped = (
        channels.groupby("fetch_channel", as_index=False)
        .agg(
            assets=("symbol_code", lambda values: ", ".join(sorted(set(map(str, values))))),
            asset_count=("symbol_code", "nunique"),
            sources=("local_source", lambda values: ", ".join(sorted(set(map(str, values))))),
        )
        .sort_values("fetch_channel")
    )
    info = {
        "Dukascopy": {
            "usage": "Historical M1 backfill for forex and selected CFD indices.",
            "limits": "No hard public quota found; our rule is monthly/day-file batches, existing-file cache, retries, and no parallel blast from the UI.",
            "current_split": "One selected asset per action; each request delegates to dukascopy-node, which downloads daily artifacts in small batches.",
            "cost": "Free.",
        },
        "Neon": {
            "usage": "Remote warehouse/buffer, then local canonical DB sync.",
            "limits": "Bound by Neon free storage/compute and our row limit control.",
            "current_split": "Pull missing candles per selected asset with overlap; remote keeps the shared buffer.",
            "cost": "Free tier target.",
        },
        "Databento": {
            "usage": "Paid/native market data for assets not covered cleanly for free, especially MNQ.",
            "limits": "Every fetch uses a max USD guard before download.",
            "current_split": "Per selected asset; currently M1 OHLCV historical requests.",
            "cost": "Metered. MNQ M1 Jan 1 to Jul 1 2026 was estimated around $0.64; UI guard defaults to $5/request.",
        },
    }
    grouped["usage"] = grouped["fetch_channel"].map(lambda channel: info.get(channel, {}).get("usage", "-"))
    grouped["limits"] = grouped["fetch_channel"].map(lambda channel: info.get(channel, {}).get("limits", "-"))
    grouped["current_split"] = grouped["fetch_channel"].map(lambda channel: info.get(channel, {}).get("current_split", "-"))
    grouped["cost"] = grouped["fetch_channel"].map(lambda channel: info.get(channel, {}).get("cost", "-"))
    return grouped[["fetch_channel", "asset_count", "sources", "assets", "usage", "limits", "current_split", "cost"]]


def render_api_usage_info(table: pd.DataFrame) -> None:
    st.subheader("API Usage")
    usage = _api_usage_rows(table)
    st.dataframe(usage, width="stretch")


def render_data_management(data: dict[str, pd.DataFrame], selection: BotSelection) -> None:
    st.markdown('<div class="bot-title">Data Management</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="bot-subtitle">Coverage locale, canaux de fetch, rattrapage Neon, Dukascopy et Databento</div>',
        unsafe_allow_html=True,
    )

    table = _build_data_management_table(data)
    if table.empty:
        st.info("No data universe configured yet.")
        return

    cols = st.columns(6)
    cols[0].metric("Assets", table["symbol_code"].nunique())
    cols[1].metric("Local Rows", int(table["candle_rows"].sum()))
    cols[2].metric("No Local Data", int((table["candle_rows"] == 0).sum()))
    cols[3].metric("Dukascopy", int((table["fetch_channel"] == "Dukascopy").sum()))
    cols[4].metric("Neon", int((table["fetch_channel"] == "Neon").sum()))
    cols[5].metric("Databento", int((table["fetch_channel"] == "Databento").sum()))

    view = table.copy()
    display_columns = [
        "symbol_code",
        "group",
        "local_source",
        "source_type",
        "fetch_channel",
        "candle_rows",
        "local_last",
        "neon_last",
        "missing_from_neon_min",
        "neon_enabled",
        "neon_status",
        "flagged_candles",
    ]
    st.dataframe(view[[column for column in display_columns if column in view.columns]], width="stretch")

    render_api_usage_info(table)

    st.subheader("Actions")
    settings_cols = st.columns(5)
    fallback_days = settings_cols[0].number_input("Fallback days", min_value=1, max_value=365, value=180, step=1)
    overlap_minutes = settings_cols[1].number_input("Overlap minutes", min_value=0, max_value=240, value=5, step=1)
    neon_limit = settings_cols[2].number_input("Neon row limit", min_value=1000, max_value=2_000_000, value=250_000, step=10_000)
    max_cost = settings_cols[3].number_input("Max Databento USD", min_value=0.01, max_value=125.0, value=5.0, step=0.25)
    dukascopy_retries = settings_cols[4].number_input("Dukascopy retries", min_value=0, max_value=15, value=3, step=1)
    remote_url = live_remote_database_url()

    single_tab, bulk_tab = st.tabs(["Single Asset", "Bulk Fetch"])
    with single_tab:
        row = _selected_data_row(table)
        if row is not None:
            symbol_code = str(row["symbol_code"])
            channel = _fetch_channel(row)
            disabled = channel == "Neon" and not remote_url
            if st.button(f"Fetch Missing ({channel})", disabled=disabled, use_container_width=True):
                try:
                    with st.spinner(f"Fetching {symbol_code} via {channel}..."):
                        result = _fetch_missing_for_row(
                            row,
                            int(fallback_days),
                            int(overlap_minutes),
                            int(neon_limit),
                            float(max_cost),
                            int(dukascopy_retries),
                            remote_url,
                        )
                    st.success(f"{symbol_code} fetched via {channel}.")
                    st.json(result)
                    st.cache_data.clear()
                except Exception as exc:
                    st.error(str(exc))
            if disabled:
                st.caption("Neon fetch needs LIVE_REMOTE_DATABASE_URL.")
            if channel == "Dukascopy":
                st.caption("Dukascopy fetch targets the latest complete UTC day to avoid unstable partial daily files.")

    with bulk_tab:
        bulk_key = "data_management_bulk_assets"
        options = list(table.index)
        if bulk_key not in st.session_state:
            st.session_state[bulk_key] = []
        left, right = st.columns(2)
        if left.button("Select All", use_container_width=True):
            st.session_state[bulk_key] = options
        if right.button("Clear", use_container_width=True):
            st.session_state[bulk_key] = []

        selected_indices = st.multiselect(
            "Assets",
            options,
            key=bulk_key,
            format_func=lambda index: _fetch_label(table.loc[index]),
        )
        if st.button("Fetch Selected", disabled=not selected_indices, use_container_width=True):
            results = []
            progress = st.progress(0)
            for position, index in enumerate(selected_indices, start=1):
                selected_row = table.loc[index]
                channel = _fetch_channel(selected_row)
                symbol_code = str(selected_row["symbol_code"])
                try:
                    result = _fetch_missing_for_row(
                        selected_row,
                        int(fallback_days),
                        int(overlap_minutes),
                        int(neon_limit),
                        float(max_cost),
                        int(dukascopy_retries),
                        remote_url,
                    )
                except Exception as exc:
                    result = {
                        "symbol_code": symbol_code,
                        "source_name": str(selected_row["local_source"]),
                        "channel": channel,
                        "status": "failed",
                        "error": str(exc),
                    }
                results.append(result)
                progress.progress(position / len(selected_indices))
            st.dataframe(pd.DataFrame.from_records(results), width="stretch")
            st.cache_data.clear()


def live_symbol_options(live: pd.DataFrame) -> list[str]:
    if not live.empty and "symbol_code" in live:
        return sorted(live["symbol_code"].dropna().astype(str).unique())
    return sorted({source.symbol_code for source in load_live_sources() if source.enabled})


def render_live_sync_controls(live: pd.DataFrame) -> None:
    st.subheader("Neon -> Local")
    remote_url = live_remote_database_url()
    symbols = live_symbol_options(live)
    selected_symbols = st.multiselect("Assets", symbols, default=symbols)
    left, right = st.columns(2)
    lookback_days = left.number_input("Lookback Days", min_value=1, max_value=180, value=3, step=1)
    limit = right.number_input("Row Limit", min_value=1000, max_value=2_000_000, value=250_000, step=10_000)

    if not remote_url:
        st.warning("LIVE_REMOTE_DATABASE_URL is not configured.")
    if st.button("Sync Remote Candles", disabled=not remote_url or not selected_symbols):
        since = datetime.now(timezone.utc) - timedelta(days=int(lookback_days))
        try:
            with st.spinner("Syncing remote candles..."):
                result = sync_remote_candles(
                    remote_url,
                    since=since,
                    symbols=selected_symbols,
                    limit=int(limit),
                )
            st.success(
                f"{result.rows_written} rows written "
                f"({result.rows_inserted} inserted, {result.rows_updated} updated)."
            )
            if result.groups:
                st.dataframe(pd.DataFrame.from_records(result.groups), width="stretch")
            st.cache_data.clear()
        except Exception as exc:
            st.error(str(exc))


def render_live_collector(data: dict[str, pd.DataFrame], selection: BotSelection) -> None:
    live = data.get("Live Collector", pd.DataFrame()).copy()
    runs = data.get("Live Runs", pd.DataFrame()).copy()
    incidents = data.get("Live Incidents", pd.DataFrame()).copy()

    st.markdown('<div class="bot-title">Live Collector</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="bot-subtitle">Daily free-source collection with 180-day remote retention target</div>',
        unsafe_allow_html=True,
    )

    if not live.empty:
        live["lag_hours"] = pd.to_numeric(live["lag_seconds"], errors="coerce") / 3600
    open_incidents = incidents[incidents["status"] == "open"] if not incidents.empty else pd.DataFrame()
    stale = live[(live["enabled"] == True) & (live["lag_hours"] > 36)] if "lag_hours" in live else pd.DataFrame()
    last_run = runs.iloc[0] if not runs.empty else None

    cols = st.columns(5)
    cols[0].metric("Enabled Sources", int(live["enabled"].fillna(False).sum()) if not live.empty else 0)
    cols[1].metric("Stale Sources", len(stale))
    cols[2].metric("Open Incidents", len(open_incidents))
    cols[3].metric("Last Rows", int(last_run["rows_written"]) if last_run is not None else 0)
    cols[4].metric("Last Status", str(last_run["status"]) if last_run is not None else "-")

    render_live_sync_controls(live)

    if not open_incidents.empty:
        st.error(f"{len(open_incidents)} live collector incident(s) open.")
        st.dataframe(
            open_incidents[
                [
                    "severity",
                    "status",
                    "title",
                    "message",
                    "failure_count",
                    "first_seen_at",
                    "last_seen_at",
                ]
            ],
            width="stretch",
        )
    elif not stale.empty:
        st.warning(f"{len(stale)} enabled source(s) are stale by the 36h daily-batch threshold.")

    left, right = st.columns([1.1, 0.9])
    with left:
        if live.empty:
            st.info("No live collector source state yet. Deploy/run the Cloudflare worker, then refresh.")
        else:
            st.plotly_chart(
                px.bar(
                    live.sort_values("lag_hours", ascending=False),
                    x="symbol_code",
                    y="lag_hours",
                    color="status",
                    title="Live source lag in hours",
                ),
                width="stretch",
            )
    with right:
        if runs.empty:
            st.info("No collector runs recorded yet.")
        else:
            st.dataframe(
                runs[
                    [
                        "started_at",
                        "finished_at",
                        "trigger_type",
                        "status",
                        "assets_requested",
                        "assets_succeeded",
                        "assets_failed",
                        "rows_written",
                        "error_message",
                    ]
                ],
                width="stretch",
            )

    if not live.empty:
        st.dataframe(
            live[
                [
                    "symbol_code",
                    "source_name",
                    "source_symbol",
                    "provider",
                    "timeframe",
                    "enabled",
                    "retention_days",
                    "collection_mode",
                    "status",
                    "last_candle_time",
                    "last_success_at",
                    "lag_hours",
                    "consecutive_failures",
                    "open_incidents",
                    "last_error_message",
                ]
            ],
            width="stretch",
        )


def render_funnel(data: dict[str, pd.DataFrame], selection: BotSelection) -> None:
    runs = selected_runs(data["Runs"], selection)
    funnel = filter_by_ids(data["Funnel"], "run_id", selection.run_ids)
    render_shell_header(runs, selection)
    st.dataframe(funnel, width="stretch")
    if not funnel.empty:
        steps = ["h1_signals", "double_swings", "legs_found", "pd_selected", "pd_touched", "rejections", "trades_opened"]
        totals = funnel[steps].sum(numeric_only=True)
        chart = pd.DataFrame({"step": steps, "count": [totals[step] for step in steps]})
        st.plotly_chart(px.bar(chart, x="step", y="count", title="Setup funnel"), width="stretch")


def render_datasets(data: dict[str, pd.DataFrame], selection: BotSelection) -> None:
    runs = selected_runs(data["Runs"], selection)
    datasets = selected_datasets(data["Datasets"], selection)
    render_shell_header(runs, selection)
    st.dataframe(datasets, width="stretch")
    if not datasets.empty:
        st.plotly_chart(
            px.bar(datasets, x="symbol_code", y="quality_score", color="source_name", title="Dataset quality"),
            width="stretch",
        )


def render_trades(data: dict[str, pd.DataFrame], selection: BotSelection) -> None:
    runs = selected_runs(data["Runs"], selection)
    trades = filter_by_ids(data["Trades"], "run_id", selection.run_ids)
    render_shell_header(runs, selection)
    st.dataframe(trades, width="stretch")
    st.download_button("Export CSV", trades.to_csv(index=False), "trades.csv")


def render_performance(data: dict[str, pd.DataFrame], selection: BotSelection) -> None:
    runs = selected_runs(data["Runs"], selection)
    equity = filter_by_ids(data["Performance"], "run_id", selection.run_ids)
    render_shell_header(runs, selection)
    if equity.empty:
        st.info("No equity curve data for this selection.")
        return
    st.plotly_chart(px.line(equity, x="time", y="equity", color="run_id", title="Equity"), width="stretch")
    st.plotly_chart(px.line(equity, x="time", y="drawdown_pct", color="run_id", title="Drawdown"), width="stretch")


def render_parameters(data: dict[str, pd.DataFrame], selection: BotSelection) -> None:
    runs = selected_runs(data["Runs"], selection)
    params = selected_parameters(data["Parameters"], selection)
    render_shell_header(runs, selection)
    st.dataframe(params, width="stretch")
    if not params.empty:
        st.plotly_chart(px.bar(params, x="name", y="pnl", color="winrate", title="Parameter PnL"), width="stretch")


def render_sources(data: dict[str, pd.DataFrame], selection: BotSelection) -> None:
    runs = selected_runs(data["Runs"], selection)
    sources = filter_by_ids(data["Sources"], "run_id", selection.run_ids)
    render_shell_header(runs, selection)
    st.dataframe(sources, width="stretch")
    if not sources.empty:
        rollup = (
            sources.groupby(["symbol_code", "source_name"], as_index=False)
            .agg(trades=("trades", "sum"), pnl=("pnl", "sum"), avg_rr=("avg_rr", "mean"), winrate=("winrate", "mean"))
            .sort_values("pnl", ascending=False)
        )
        st.plotly_chart(px.bar(rollup, x="symbol_code", y="pnl", color="source_name", title="Source PnL"), width="stretch")


def sidebar_selection(runs: pd.DataFrame) -> tuple[str, str, str, str]:
    st.sidebar.title("Bot Lab")
    page = st.sidebar.radio("Dashboard", PAGES)
    if runs.empty:
        return page, ALL_BOTS, ALL_VALUES, ALL_VALUES

    bots = sorted({f"{row.strategy_name} / {row.strategy_version}" for row in runs.itertuples()})
    bot = st.sidebar.selectbox("Bot", [ALL_BOTS, *bots])
    symbol = st.sidebar.selectbox("Symbol", [ALL_VALUES, *sorted(runs["symbol_code"].dropna().unique())])
    source = st.sidebar.selectbox("Source", [ALL_VALUES, *sorted(runs["source_name"].dropna().unique())])
    return page, bot, symbol, source


def main() -> None:
    st.set_page_config(page_title="ICT Bot Lab", layout="wide")
    apply_theme()

    data = load_dashboard_data()
    page, bot, symbol, source = sidebar_selection(data["Runs"])
    selection = build_selection(data["Runs"], bot, symbol, source)
    renderers = {
        "Overview": render_overview,
        "Chart": render_chart,
        "Coverage": render_coverage,
        "Data Management": render_data_management,
        "Live Collector": render_live_collector,
        "Runs": render_runs,
        "Datasets": render_datasets,
        "Funnel": render_funnel,
        "Trades": render_trades,
        "Performance": render_performance,
        "Parameters": render_parameters,
        "Sources": render_sources,
    }
    renderers[page](data, selection)


if __name__ == "__main__":
    main()
