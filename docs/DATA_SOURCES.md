# Market Data Source Plan

The platform should keep one canonical PostgreSQL market database. External tools are acquisition adapters only: download raw candles, normalize through the transformer layer, and store canonical candles in `market_candles`.

## Priority Free Sources

### Dukascopy / dukascopy-node

Good fit for forex and some CFD/index instruments exposed by Dukascopy.

Example for GER40:

```powershell
npx dukascopy-node -i deuidxeur -from 2026-04-01 -to 2026-07-01 -t m1 -f csv > data/raw/dukascopy_deuidxeur_m1_20260401_20260701.csv
python -m ict.cli ingest-csv --symbol GER40 --source csv --file data/raw/dukascopy_deuidxeur_m1_20260401_20260701.csv --timeframe M1 --from 2026-04-01 --to 2026-07-01 --mapping configs/csv_dukascopy_node_mapping.yaml
python -m ict.cli datasets create --symbol GER40 --source csv --timeframe M1 --from 2026-04-01 --to 2026-07-01 --name GER40_DUKASCOPY_NODE_M1_20260401_20260701
```

If the exported CSV header differs, update `configs/csv_dukascopy_node_mapping.yaml`; the database model does not need to change.

Operational limits used by the app:

- no hard public daily quota is documented for our use case;
- `dukascopy-node` downloads many daily artifacts under the hood, defaults to small batches, and exposes `batchSize`, `pauseBetweenBatchesMs`, retries and retry-on-empty controls;
- for the dashboard, Dukascopy fetches are run per selected asset and target the latest complete UTC day to avoid unstable partial files;
- for large backfills, keep monthly/day-file batches and avoid launching many assets in parallel.

### Binance Public Data

Best fit for crypto spot/futures. Binance publishes daily and monthly ZIP archives for public klines, including `1m` candles. The project has a native `binance_public` provider for those archives.

Suggested internal symbols: `BTCUSD`, `ETHUSD`, with source aliases such as `BTCUSDT` and `ETHUSDT` when a Binance provider is added.

Native provider status: implemented as source `binance` with `source_type: binance_public`.

Example:

```powershell
python -m ict.cli sources sync
python -m ict.cli symbols sync
python -m ict.cli collect --symbols BTCUSD,ETHUSD --sources binance --from 2026-01-01 --to 2026-07-01 --chunk monthly
```

### Coinbase Exchange Candles

Useful secondary crypto source for cross-checking BTC/ETH candles. It exposes product candle endpoints with `start`, `end`, and `granularity`.

### yfinance

Useful for equities, ETFs, and some index proxies at higher timeframes. It is less suitable as the main M1 source for systematic intraday backtests because availability and terms are Yahoo-dependent.

### Alpha Vantage

Useful as an API-key source for daily data, FX, metals, and some intraday endpoints. Treat as a supplemental source because free tiers and premium endpoint boundaries affect scale.

### OANDA

OANDA is no longer part of the operational pipeline because the account flow requested a deposit. The source is kept inactive in `configs/sources.yaml` only so `ict sources sync` can explicitly mark any previous local DB source as inactive.

### MNQ / CME Futures

Native `MNQ` is not available in the current MT5 terminal. The terminal exposes Nasdaq CFD/proxy symbols such as `USTEC` and `USTECH100M`, but those should not be mixed with native CME futures data.

Recommended path: use Databento `GLBX.MDP3` source `databento_glbx` for native `MNQ`; use Dukascopy `NAS100` (`usatechidxusd`) only as a labelled proxy. The Databento provider performs `metadata.get_cost` before downloading metered data and refuses requests above `max_cost_usd`. See `docs/MNQ_DATA_SOURCES.md`.

Example cost-bounded MNQ collection:

```powershell
python -m ict.cli sources sync
python -m ict.cli symbols sync
python -m ict.cli collect --symbols MNQ --sources databento_glbx --from 2026-01-01 --to 2026-07-01 --chunk full
```

Databento downloaded OHLCV CSV ZIPs can also be imported directly through `--file`; MBO exports are raw order book events and are intentionally not candle-ingested by the OHLCV provider.

## Storage Direction

- Keep raw external files under `data/raw/` and keep them out of Git.
- Store canonical candles in PostgreSQL only once per `(symbol, source, timeframe, time_open)`.
- Use datasets to freeze reproducible backtest windows.
- Use `mart_market_coverage` to monitor what is actually available for each asset/source/timeframe.
- Start with normal PostgreSQL tables. Move to monthly partitioning or TimescaleDB only when row counts and query latency justify it.

## Default 40 Asset Universe

`configs/universe_default_40.yaml` defines the first collection target:

- 20 forex pairs through `dukascopy`.
- 20 mainstream assets across metals, crypto, and indices, including native `MNQ` through Databento.
- Crypto assets use `binance`; unsupported free-live assets remain `pending_cloud_source` until a validated free R2-compatible provider exists.

Collect six months in monthly packets:

```powershell
python -m ict.cli sources sync
python -m ict.cli symbols sync
python -m ict.cli universe audit --universe configs/universe_default_40.yaml --target-days 180 --check-provider
python -m ict.cli collect --universe configs/universe_default_40.yaml --from 2026-01-01 --to 2026-07-01 --chunk monthly
python -m ict.cli db refresh-views
```

For a dry first pass, use `--limit 2` or `--group crypto`.

For complete monthly Binance archives, prefer whole completed months:

```powershell
python -m ict.cli collect --universe configs/universe_default_40.yaml --group crypto --from 2026-01-01T00:00:00 --to 2026-06-30T23:59:00 --chunk monthly
```

If a broker-specific MT5 alias is missing, search the terminal catalog:

```powershell
python -m ict.cli sources search --source mt5 --query "Germany"
python -m ict.cli sources search --source mt5 --query "US 500"
```
