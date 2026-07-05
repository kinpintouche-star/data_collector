# ICT CRT M1 Backtesting & Analytics Platform

Python/PostgreSQL platform for ingesting generic M1 market data, replaying the ICT CRT M1 strategy offline, and storing detailed analytics.

This repository follows `SPEC_Codex_ICT_Backtesting_Platform_v2_Generic_Data_Layer.md`. MT5 is only one optional provider; CSV and Dukascopy-style file imports use the same provider -> transformer -> loader path. Backtests read PostgreSQL datasets only.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
docker compose up -d postgres adminer
python -m ict.cli db upgrade
python -m ict.cli db seed-defaults
```

Default local services:

- PostgreSQL: `localhost:5432`
- Adminer: `http://localhost:8080`

## Main Commands

```powershell
python -m ict.cli sources sync
python -m ict.cli symbols sync
python scripts/generate_synthetic_backtest_csv.py
python -m ict.cli ingest-csv --symbol GER40 --source csv --file data/raw/synthetic_ger40_m1.csv --timeframe M1 --from 2025-01-01 --to 2025-01-01T03:59:00 --mapping configs/csv_ger40_mapping.yaml
python -m ict.cli datasets create --symbol GER40 --source csv --timeframe M1 --from 2025-01-01 --to 2025-01-01T03:59:00 --name GER40_SYNTHETIC_M1
python -m ict.cli backtest --dataset-id <uuid> --config configs/strategy_default.yaml
python -m ict.cli grid --symbols GER40 --sources csv --timeframe M1 --from 2025-01-01 --to 2025-01-01T03:59:00 --grid configs/grid_example.yaml --limit 2
python -m ict.cli metrics refresh --run-id <uuid>
python -m ict.cli dashboard
```

## Synthetic Integration Fixture

```powershell
python scripts/generate_synthetic_backtest_csv.py
python -m ict.cli ingest-csv --symbol GER40 --source csv --file data/raw/synthetic_ger40_m1.csv --timeframe M1 --from 2025-01-01 --to 2025-01-01T03:59:00 --mapping configs/csv_ger40_mapping.yaml
python -m ict.cli datasets create --symbol GER40 --source csv --timeframe M1 --from 2025-01-01 --to 2025-01-01T03:59:00 --name GER40_SYNTHETIC_M1
python -m ict.cli backtest --dataset-id <uuid>
```

The same fixture can validate the Dukascopy-file path:

```powershell
python -m ict.cli ingest --symbol GER40 --source dukascopy --file data/raw/synthetic_ger40_m1.csv --timeframe M1 --from 2025-01-01 --to 2025-01-01T03:59:00 --mapping configs/csv_ger40_mapping.yaml
```

Or run the complete local acceptance smoke:

```powershell
python scripts/smoke_end_to_end.py
```

## Bot Lab Dashboard

```powershell
python -m ict.cli dashboard
```

The Streamlit dashboard opens a bot-first view:

- choose a bot from `strategy_versions`;
- filter by symbol and source;
- inspect linked runs, datasets, funnel, trades, equity, parameters, and source comparison.

The current baseline bot is `ICT_CRT_M1 / python-v1.6`, sourced from the Pine v1.6 strategy port.

## MNQ / MT5 First Test

This broker terminal did not expose a native `MNQ` symbol. The platform maps internal `MNQ` to MT5 `USTECH100M` as an explicit mini Nasdaq proxy.

```powershell
python -m ict.cli symbols sync
python -m ict.cli ingest --symbol MNQ --source mt5 --timeframe M1 --from 2026-07-01T00:00:00 --to 2026-07-02T23:59:00
python -m ict.cli datasets create --symbol MNQ --source mt5 --timeframe M1 --from 2026-07-01T00:00:00 --to 2026-07-02T23:59:00 --name MNQ_MT5_USTECH100M_M1_20260701_20260702
python -m ict.cli backtest --dataset-id <uuid>
```

## V1 Coverage

- PostgreSQL schema and Alembic migration.
- Generic data layer with sources, aliases, transformers, quality reports, and reproducible datasets.
- CSV/Dukascopy-file ingestion and optional MT5 ingestion with idempotent upserts.
- Deterministic M1 to M15/H1 resampling.
- Strategy primitives: CRT signal, confirmed pivots, OTE, FVG, OB, PD selection, mitigation, rejection, and risk validation.
- Dataset-backed backtest engine that persists setup events, orders, fills, trades, equity curve, and reconstructible metrics.
- Grid search over YAML parameter sets.
- Streamlit dashboard backed by SQL marts for runs, datasets, funnel, trades, performance, parameters, and source comparison.

Live trading is intentionally out of scope for V1.
