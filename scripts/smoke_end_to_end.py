from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from generate_synthetic_backtest_csv import main as generate_synthetic_csv
from ict.core.config import get_settings
from ict.dashboard.data import DASHBOARD_QUERIES, PAGES, dashboard_frame
from ict.db.session import build_engine


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "raw" / "synthetic_ger40_m1.csv"
START = "2025-01-01"
END = "2025-01-01T03:59:00"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local V2 end-to-end smoke test.")
    parser.add_argument("--skip-db-upgrade", action="store_true")
    parser.add_argument("--skip-grid", action="store_true")
    parser.add_argument("--skip-dashboard-queries", action="store_true")
    args = parser.parse_args()

    if not args.skip_db_upgrade:
        run([sys.executable, "-m", "ict.cli", "db", "upgrade"])
    run([sys.executable, "-m", "ict.cli", "db", "seed-defaults"])

    generate_synthetic_csv()

    first_ingest = ingest_fixture()
    second_ingest = ingest_fixture()
    assert first_ingest["rows_fetched"] == 240, first_ingest
    assert second_ingest["rows_fetched"] == 240, second_ingest
    assert second_ingest["rows_inserted"] == 0, second_ingest
    assert second_ingest["rows_updated"] == 240, second_ingest

    dataset = run_json(
        [
            sys.executable,
            "-m",
            "ict.cli",
            "datasets",
            "create",
            "--symbol",
            "GER40",
            "--source",
            "csv",
            "--timeframe",
            "M1",
            "--from",
            START,
            "--to",
            END,
            "--name",
            "GER40_SYNTHETIC_M1",
        ]
    )
    assert dataset["candles_count"] == 240, dataset
    assert dataset["quality_score"] == 1.0, dataset

    backtest = run_json(
        [
            sys.executable,
            "-m",
            "ict.cli",
            "backtest",
            "--dataset-id",
            dataset["dataset_id"],
        ]
    )
    assert backtest["trades"] >= 1, backtest
    assert backtest["net_profit"] is not None, backtest

    metrics = run_json(
        [
            sys.executable,
            "-m",
            "ict.cli",
            "metrics",
            "refresh",
            "--run-id",
            backtest["run_id"],
        ]
    )
    assert metrics["metrics"]["total_trades"] >= 1, metrics

    if not args.skip_grid:
        run(
            [
                sys.executable,
                "-m",
                "ict.cli",
                "grid",
                "--symbols",
                "GER40",
                "--sources",
                "csv",
                "--timeframe",
                "M1",
                "--from",
                START,
                "--to",
                END,
                "--grid",
                "configs/grid_example.yaml",
                "--limit",
                "2",
            ]
        )

    if not args.skip_dashboard_queries:
        validate_dashboard_queries()

    print("Smoke test passed.")


def ingest_fixture() -> dict[str, Any]:
    return run_json(
        [
            sys.executable,
            "-m",
            "ict.cli",
            "ingest-csv",
            "--symbol",
            "GER40",
            "--source",
            "csv",
            "--file",
            str(FIXTURE),
            "--timeframe",
            "M1",
            "--from",
            START,
            "--to",
            END,
            "--mapping",
            "configs/csv_ger40_mapping.yaml",
        ]
    )


def validate_dashboard_queries() -> None:
    try:
        import pyarrow as pa
    except ImportError:
        pa = None

    engine = build_engine(get_settings().database_url)
    for page in PAGES:
        frame = dashboard_frame(pd.read_sql(DASHBOARD_QUERIES[page], engine))
        if pa is not None:
            pa.Table.from_pandas(frame)
        print(f"Dashboard query {page}: rows={len(frame)}")


def run_json(command: list[str]) -> dict[str, Any]:
    completed = run(command, capture_output=True)
    return json.loads(completed.stdout)


def run(command: list[str], capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    print("$ " + " ".join(command), flush=True)
    return subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=capture_output,
    )


if __name__ == "__main__":
    main()
