import type { Candle, LiveSource } from "../types";
import { dedupeCandles, M1_MS } from "./common";

const BINANCE_BASE_URL = "https://api.binance.com";
const MAX_KLINES = 1000;

export { dailyWindow, dedupeCandles } from "./common";

export type BinanceKline = [
  number,
  string,
  string,
  string,
  string,
  string,
  number,
  string,
  number,
  string,
  string,
  string
];

export function normalizeBinanceKline(asset: LiveSource, row: BinanceKline, now = new Date()): Candle | null {
  const openTime = Number(row[0]);
  const closeTime = Number(row[6]);
  if (!Number.isFinite(openTime) || closeTime + 1 > now.getTime()) {
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
    tickVolume: Number(row[8]),
    realVolume: Number(row[5]),
    spread: null,
    qualityFlags: {},
    metadata: {
      provider: "binance",
      quote_volume: row[7],
      taker_buy_base_volume: row[9],
      taker_buy_quote_volume: row[10]
    }
  };
}

export async function fetchBinanceCandles(asset: LiveSource, from: Date, to: Date, now = new Date()): Promise<Candle[]> {
  const rows: BinanceKline[] = [];
  let cursor = from.getTime();
  const endMs = to.getTime();

  while (cursor < endMs) {
    const pageEnd = Math.min(endMs - 1, cursor + MAX_KLINES * M1_MS - 1);
    const url = new URL("/api/v3/klines", BINANCE_BASE_URL);
    url.searchParams.set("symbol", asset.sourceSymbol);
    url.searchParams.set("interval", "1m");
    url.searchParams.set("startTime", String(cursor));
    url.searchParams.set("endTime", String(pageEnd));
    url.searchParams.set("limit", String(MAX_KLINES));
    const response = await fetch(url.toString(), {
      headers: { "User-Agent": "ict-live-collector/0.1" }
    });
    if (!response.ok) {
      throw new Error(`Binance ${asset.sourceSymbol} failed: ${response.status} ${await response.text()}`);
    }
    const page = (await response.json()) as BinanceKline[];
    if (page.length === 0) {
      break;
    }
    rows.push(...page);
    const lastOpen = Number(page[page.length - 1][0]);
    const nextCursor = lastOpen + M1_MS;
    if (nextCursor <= cursor) {
      break;
    }
    cursor = nextCursor;
  }

  return normalizeBinanceRows(asset, rows, from, to, now);
}

export function normalizeBinanceRows(asset: LiveSource, rows: BinanceKline[], from: Date, to: Date, now = new Date()): Candle[] {
  return dedupeCandles(
    rows
      .map((row) => normalizeBinanceKline(asset, row, now))
      .filter((candle): candle is Candle => {
        return candle !== null && Date.parse(candle.timeOpen) >= from.getTime() && Date.parse(candle.timeOpen) < to.getTime();
      })
  );
}
