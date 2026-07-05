from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import plotly.express as px
import streamlit as st

from ict.core.config import get_settings
from ict.dashboard.data import DASHBOARD_QUERIES, PAGES, dashboard_frame
from ict.db.session import build_engine


ALL_BOTS = "All bots"
ALL_VALUES = "All"


@dataclass(frozen=True)
class BotSelection:
    label: str
    run_ids: set[str]
    dataset_ids: set[str]
    parameter_set_ids: set[str]


@st.cache_resource
def dashboard_engine():
    return build_engine(get_settings().database_url)


@st.cache_data(ttl=30)
def read_sql(query: str) -> pd.DataFrame:
    return dashboard_frame(pd.read_sql(query, dashboard_engine()))


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


def render_runs(data: dict[str, pd.DataFrame], selection: BotSelection) -> None:
    runs = selected_runs(data["Runs"], selection)
    render_shell_header(runs, selection)
    st.dataframe(runs, width="stretch")


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
    if runs.empty:
        return "Overview", ALL_BOTS, ALL_VALUES, ALL_VALUES

    bots = sorted({f"{row.strategy_name} / {row.strategy_version}" for row in runs.itertuples()})
    bot = st.sidebar.selectbox("Bot", [ALL_BOTS, *bots])
    symbol = st.sidebar.selectbox("Symbol", [ALL_VALUES, *sorted(runs["symbol_code"].dropna().unique())])
    source = st.sidebar.selectbox("Source", [ALL_VALUES, *sorted(runs["source_name"].dropna().unique())])
    page = st.sidebar.radio("Dashboard", PAGES)
    return page, bot, symbol, source


def main() -> None:
    st.set_page_config(page_title="ICT Bot Lab", layout="wide")
    apply_theme()

    data = load_dashboard_data()
    page, bot, symbol, source = sidebar_selection(data["Runs"])
    selection = build_selection(data["Runs"], bot, symbol, source)
    renderers = {
        "Overview": render_overview,
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
