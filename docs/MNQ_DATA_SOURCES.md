# MNQ Data Source Notes

`MNQ` should be treated as a native CME futures instrument, not as a generic Nasdaq CFD. The local MT5 terminal does not currently expose `MNQ`; it exposes `USTEC` and `USTECH100M`, which are usable Nasdaq proxies but not native Micro E-mini Nasdaq-100 futures.

## Current Recommendation

Use this priority order:

1. `databento_glbx` for native CME Globex MNQ historical data when an API key is available.
2. Broker/API extraction through Interactive Brokers, Tradovate/CQG, NinjaTrader, or a similar futures data entitlement if the user already has an account/subscription.
3. `NAS100` via Dukascopy (`usatechidxusd`) as a clearly labelled proxy for strategy research.
4. Yahoo-style `MNQ=F` only for quick manual checks, not for canonical backtest storage.

MNQ cannot be calculated cleanly from NAS100/OANDA/Dukascopy CFD candles. A proxy can help research signals and market regimes, but fills, stops, roll behaviour, gaps, session details and basis can differ from the native CME futures contract.

## Candidate Sources

### Databento

Best technical fit for native MNQ ingestion.

- Venue/dataset: CME Globex MDP 3.0, `GLBX.MDP3`.
- Supported data includes futures and OHLCV minute bars.
- Selected canonical backtest symbol: `MNQ.c.0` continuous front contract through Databento continuous symbology.
- Other symbol styles to test when needed: `MNQ.FUT` parent and explicit quarterly contract symbols.
- Requires API key and metered pricing, but Databento advertises starter credits.
- Native provider status: implemented as source `databento_glbx` with `dataset="GLBX.MDP3"`, `schema="ohlcv-1m"`, cost preflight through `metadata.get_cost`, then storage into `market_candles`.

References:

- https://databento.com/
- https://databento.com/docs/venues-and-datasets/glbx-mdp3
- https://databento.com/docs/schemas-and-data-formats/ohlcv
- https://databento.com/docs/examples/symbology/continuous

### CME DataMine

Most authoritative source, because it is CME's own historical data platform.

- Strongest provenance for native futures history.
- More operational friction: account, licensing, ordering/export workflow.
- Better for institutional-quality archive purchases than for lightweight iterative ingestion.

References:

- https://www.cmegroup.com/datamine.html
- https://www.cmegroup.com/markets/equities/nasdaq/micro-e-mini-nasdaq-100.html

### Interactive Brokers TWS API

Good if an IBKR account and futures market data entitlement are already available.

- TWS API supports historical bar requests, including `1 min` bars and futures products.
- Less ideal as a bulk archive source because requests are broker/session-bound and subject to pacing/entitlements.
- Good integration target only if TWS/Gateway is running locally.

Reference:

- https://interactivebrokers.github.io/tws-api/historical_bars.html

### Trading Platform/Broker Data

Tradovate, CQG, NinjaTrader/Kinetick, Rithmic, Sierra Chart/Denali, and similar futures platforms may provide MNQ history if the account has CME data access.

- Usually not free for six months of minute data.
- Good user-owned source if export or API is available.
- Needs explicit provenance and entitlement metadata in source config.

### Proxy Sources

Use only with clear labels:

- `NAS100` through Dukascopy: `usatechidxusd`.
- MT5 broker symbols: `USTEC`, `USTECH100M`.

These are useful for strategy exploration but should not be mixed with native `MNQ` results in reports.

## Implementation Plan

1. Keep `MNQ` native source separate from `NAS100` and MT5 proxy aliases.
2. Use `databento_glbx` only when an API key is available.
3. Store native MNQ candles as `(symbol=MNQ, source=databento_glbx, timeframe=M1)`.
4. Store proxy data as `NAS100` or as `MNQ_PROXY_*`, never as canonical `MNQ`.
5. Record contract mapping/roll policy in dataset metadata for continuous futures backtests.
