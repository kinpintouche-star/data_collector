# ICT CRT M1 Backtesting & Analytics Platform

Python/PostgreSQL platform for ingesting generic M1 market data, replaying the ICT CRT M1 strategy offline, and storing detailed analytics.

This repository follows `SPEC_Codex_ICT_Backtesting_Platform_v2_Generic_Data_Layer.md`. MT5 is only one optional provider; CSV and Dukascopy-style file imports use the same provider -> transformer -> loader path. Backtests read PostgreSQL datasets only.

## Quick Start

Recommended full local stack:

```powershell
.\scripts\dev.ps1 up
```

Or with Make:

```powershell
make up
```

This starts:

- React Trading Lab: `http://127.0.0.1:5173`
- FastAPI: `http://127.0.0.1:8000/api/health`
- PostgreSQL: `localhost:5432`
- Adminer: `http://127.0.0.1:8080`

Current architecture diagram:

- [docs/ARCHITECTURE_CURRENT.drawio](docs/ARCHITECTURE_CURRENT.drawio)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

Strategy research playbook:

- [docs/STRATEGY_ANALYSIS_PLAYBOOK.md](docs/STRATEGY_ANALYSIS_PLAYBOOK.md)

Useful service commands:

```powershell
.\scripts\dev.ps1 ps
.\scripts\dev.ps1 logs-api
.\scripts\dev.ps1 logs-web
.\scripts\dev.ps1 down
```

Manual setup still works:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
docker-compose up -d postgres adminer
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
- inspect linked runs, datasets, coverage, funnel, trades, equity, parameters, and source comparison;
- use the `Chart` page to review M1 candles with trade entries, exits, SL/TP levels, and setup trigger events.

The current baseline bot is `ICT_CRT_M1 / python-v1.6`, sourced from the Pine v1.6 strategy port.

## React Trading Lab

The React/FastAPI trading lab is the new trade-review cockpit. It runs alongside Streamlit and reads the same local
PostgreSQL database.

Preferred launcher:

```powershell
.\scripts\dev.ps1 up
```

Manual launcher:

```powershell
python -m ict.cli api
cd web
npm install
npm run dev
```

Default local services:

- API: `http://127.0.0.1:8000`
- React app: `http://127.0.0.1:5173`

The React app reviews each persisted trade with H4, H1, M30, M15, M5, and M1 charts, setup events, entry/exit markers,
SL/TP, fib levels, risk/reward zones, and gap warnings.

The `Strategy Builder` page creates ICT/SMC strategies from ordered blocks, stores drafts/validated versions in the
local database, exports validated definitions to YAML, and lets Run Lab backtest them through `strategy_definition_id`.

## Market Data Store

The platform uses PostgreSQL as the canonical market-data store. External providers and tools are acquisition paths only; candles are normalized into one schema and backtests read reproducible DB datasets.

```powershell
python -m ict.cli db refresh-views
```

Useful views:

- `mart_market_coverage`: stored candle rows, date range, source symbol, and quality flags by asset/source/timeframe.
- `mart_dataset_quality`: reproducible dataset windows and quality scores.
- `mart_run_summary`: bot/run metrics linked to the dataset and parameter set.

See [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) for free data-source recipes and the 6-12 month collection strategy.

The default research universe targets 40 assets with 20 forex pairs and 20 mainstream metals/crypto/indices:

```powershell
python -m ict.cli sources sync
python -m ict.cli symbols sync
python -m ict.cli collect --universe configs/universe_default_40.yaml --from 2026-01-01 --to 2026-07-01 --chunk monthly
python -m ict.cli db refresh-views
```

Use `--limit 2` or `--group crypto` for a first controlled run.

Before a long run, audit the universe against the configured DB aliases and the current provider catalog:

```powershell
python -m ict.cli universe audit --universe configs/universe_default_40.yaml --target-days 180 --check-provider
```

On the current MT5 terminal this returns 40/40 provider-ready assets.

## Dukascopy Node Example

GER40 can be downloaded with `dukascopy-node` and then ingested through the CSV transformer:

```powershell
npx dukascopy-node -i deuidxeur -from 2026-04-01 -to 2026-07-01 -t m1 -f csv > data/raw/dukascopy_deuidxeur_m1_20260401_20260701.csv
python -m ict.cli ingest-csv --symbol GER40 --source csv --file data/raw/dukascopy_deuidxeur_m1_20260401_20260701.csv --timeframe M1 --from 2026-04-01 --to 2026-07-01 --mapping configs/csv_dukascopy_node_mapping.yaml
python -m ict.cli datasets create --symbol GER40 --source csv --timeframe M1 --from 2026-04-01 --to 2026-07-01 --name GER40_DUKASCOPY_NODE_M1_20260401_20260701
```

## MNQ / MT5 First Test

This broker terminal did not expose a confirmed native `MNQ` symbol. The current `MNQ` config maps to MT5 `USTECH100M` only as an exploratory Nasdaq mini proxy, not as a validated CME MNQ feed.

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
