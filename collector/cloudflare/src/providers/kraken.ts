import type { Candle, LiveSource } from "../types";
import { dedupeCandles, M1_MS } from "./common";

const KRAKEN_BASE_URL = "https://api.kraken.com";
const KRAKEN_INTERVAL_MINUTES = 1;
const MAX_PAGES = 6;

export type KrakenCandle = [
  number,
  string,
  string,
  string,
  string,
  string,
  string,
  number
];

type KrakenPayload = {
  error?: string[];
  result?: Record<string, KrakenCandle[] | number | string>;
};

export function normalizeKrakenCandle(
  asset: LiveSource,
  row: KrakenCandle,
  providerPair: string,
  now = new Date()
): Candle | null {
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
    open: row[1],
    high: row[2],
    low: row[3],
    close: row[4],
    tickVolume: Number(row[7]),
    realVolume: Number(row[6]),
    spread: null,
    qualityFlags: {},
    metadata: {
      provider: "kraken",
      provider_symbol: providerPair,
      vwap: row[5]
    }
  };
}

export async function fetchKrakenCandles(
  asset: LiveSource,
  from: Date,
  to: Date,
  now = new Date(),
  providerPair = asset.fallbackProviderSymbol ?? asset.providerSymbol ?? asset.sourceSymbol
): Promise<Candle[]> {
  const rows: KrakenCandle[] = [];
  let cursor = from.getTime();
  const endMs = to.getTime();

  for (let pageIndex = 0; pageIndex < MAX_PAGES && cursor < endMs; pageIndex += 1) {
    const url = new URL("/0/public/OHLC", KRAKEN_BASE_URL);
    url.searchParams.set("pair", providerPair);
    url.searchParams.set("interval", String(KRAKEN_INTERVAL_MINUTES));
    url.searchParams.set("since", String(Math.floor(cursor / 1000)));
    const response = await fetch(url.toString(), {
      headers: {
        Accept: "application/json",
        "User-Agent": "ict-live-collector/0.1"
      }
    });
    if (!response.ok) {
      throw new Error(`Kraken ${providerPair} failed: ${response.status} ${await response.text()}`);
    }
    const payload = (await response.json()) as KrakenPayload;
    if (payload.error && payload.error.length > 0) {
      throw new Error(`Kraken ${providerPair} failed: ${payload.error.join(", ")}`);
    }
    const result = payload.result ?? {};
    const resultKey = Object.keys(result).find((key) => key !== "last");
    const page = resultKey ? result[resultKey] : [];
    if (!Array.isArray(page) || page.length === 0) {
      break;
    }

    const typedPage = page as KrakenCandle[];
    rows.push(...typedPage);
    const lastOpen = Math.max(...typedPage.map((row) => Number(row[0]) * 1000).filter(Number.isFinite));
    const nextCursor = lastOpen + M1_MS;
    if (!Number.isFinite(nextCursor) || nextCursor <= cursor) {
      break;
    }
    cursor = nextCursor;
  }

  return normalizeKrakenRows(asset, rows, from, to, now, providerPair);
}

export function normalizeKrakenRows(
  asset: LiveSource,
  rows: KrakenCandle[],
  from: Date,
  to: Date,
  now = new Date(),
  providerPair = asset.fallbackProviderSymbol ?? asset.providerSymbol ?? asset.sourceSymbol
): Candle[] {
  return dedupeCandles(
    rows
      .map((row) => normalizeKrakenCandle(asset, row, providerPair, now))
      .filter((candle): candle is Candle => {
        return candle !== null && Date.parse(candle.timeOpen) >= from.getTime() && Date.parse(candle.timeOpen) < to.getTime();
      })
  );
}
