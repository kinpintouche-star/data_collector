import type { Candle, LiveSource } from "../types";
import { dedupeCandles, M1_MS, providerSymbol } from "./common";

const COINBASE_BASE_URL = "https://api.exchange.coinbase.com";
const COINBASE_GRANULARITY_SECONDS = 60;
const MAX_CANDLES_PER_REQUEST = 300;

export type CoinbaseCandle = [
  number,
  number | string,
  number | string,
  number | string,
  number | string,
  number | string
];

export function normalizeCoinbaseCandle(asset: LiveSource, row: CoinbaseCandle, now = new Date()): Candle | null {
  const openTime = Number(row[0]) * 1000;
  if (!Number.isFinite(openTime) || openTime + M1_MS > now.getTime()) {
    return null;
  }
  return {
    symbolCode: asset.symbolCode,
    sourceName: asset.sourceName,
    sourceSymbol: asset.sourceSymbol,
    timeframe: asset.timeframe,
    timeOpen: new Date(openTime).toISOString(),
    open: String(row[3]),
    high: String(row[2]),
    low: String(row[1]),
    close: String(row[4]),
    tickVolume: null,
    realVolume: Number(row[5]),
    spread: null,
    qualityFlags: {},
    metadata: {
      provider: "coinbase",
      provider_symbol: providerSymbol(asset)
    }
  };
}

export async function fetchCoinbaseCandles(
  asset: LiveSource,
  from: Date,
  to: Date,
  now = new Date()
): Promise<Candle[]> {
  const rows: CoinbaseCandle[] = [];
  let cursor = from.getTime();
  const endMs = to.getTime();

  while (cursor < endMs) {
    const pageEnd = Math.min(endMs, cursor + MAX_CANDLES_PER_REQUEST * M1_MS);
    const url = new URL(`/products/${providerSymbol(asset)}/candles`, COINBASE_BASE_URL);
    url.searchParams.set("granularity", String(COINBASE_GRANULARITY_SECONDS));
    url.searchParams.set("start", new Date(cursor).toISOString());
    url.searchParams.set("end", new Date(pageEnd).toISOString());
    const response = await fetch(url.toString(), {
      headers: {
        Accept: "application/json",
        "User-Agent": "ict-live-collector/0.1"
      }
    });
    if (!response.ok) {
      throw new Error(`Coinbase ${providerSymbol(asset)} failed: ${response.status} ${await response.text()}`);
    }
    const page = (await response.json()) as unknown;
    if (!Array.isArray(page)) {
      throw new Error(`Coinbase ${providerSymbol(asset)} returned a non-array payload.`);
    }
    rows.push(...(page as CoinbaseCandle[]));
    cursor = pageEnd;
  }

  return normalizeCoinbaseRows(asset, rows, from, to, now);
}

export function normalizeCoinbaseRows(
  asset: LiveSource,
  rows: CoinbaseCandle[],
  from: Date,
  to: Date,
  now = new Date()
): Candle[] {
  return dedupeCandles(
    rows
      .map((row) => normalizeCoinbaseCandle(asset, row, now))
      .filter((candle): candle is Candle => {
        return candle !== null && Date.parse(candle.timeOpen) >= from.getTime() && Date.parse(candle.timeOpen) < to.getTime();
      })
  );
}
