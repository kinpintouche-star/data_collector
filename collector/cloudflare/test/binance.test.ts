import { describe, expect, it } from "vitest";
import { dailyWindow, dedupeCandles, normalizeBinanceKline, normalizeBinanceRows, type BinanceKline } from "../src/providers/binance";
import { LIVE_SOURCES } from "../src/live-sources";
import type { Candle } from "../src/types";

const asset = LIVE_SOURCES[0];

function kline(openTime: number, closeTime: number, close = "42050.00"): BinanceKline {
  return [
    openTime,
    "42000.00",
    "42100.00",
    "41900.00",
    close,
    "12.5",
    closeTime,
    "525000.00",
    120,
    "6.0",
    "252000.00",
    "0"
  ];
}

describe("binance normalization", () => {
  it("normalizes Binance M1 klines into canonical candles", () => {
    const row = kline(Date.UTC(2026, 0, 1, 0, 0), Date.UTC(2026, 0, 1, 0, 0, 59, 999));
    const candle = normalizeBinanceKline(asset, row, new Date(Date.UTC(2026, 0, 1, 0, 1)));

    expect(candle?.symbolCode).toBe("BTCUSD");
    expect(candle?.sourceSymbol).toBe("BTCUSDT");
    expect(candle?.timeOpen).toBe("2026-01-01T00:00:00.000Z");
    expect(candle?.open).toBe("42000.00");
    expect(candle?.tickVolume).toBe(120);
    expect(candle?.realVolume).toBe(12.5);
  });

  it("ignores candles that are not closed yet", () => {
    const row = kline(Date.UTC(2026, 0, 1, 0, 0), Date.UTC(2026, 0, 1, 0, 0, 59, 999));
    const candle = normalizeBinanceKline(asset, row, new Date(Date.UTC(2026, 0, 1, 0, 0, 30)));

    expect(candle).toBeNull();
  });

  it("deduplicates by open time using the latest row in the batch", () => {
    const from = new Date(Date.UTC(2026, 0, 1, 0, 0));
    const to = new Date(Date.UTC(2026, 0, 1, 0, 2));
    const rows = [
      kline(Date.UTC(2026, 0, 1, 0, 0), Date.UTC(2026, 0, 1, 0, 0, 59, 999), "42050.00"),
      kline(Date.UTC(2026, 0, 1, 0, 0), Date.UTC(2026, 0, 1, 0, 0, 59, 999), "42060.00"),
      kline(Date.UTC(2026, 0, 1, 0, 1), Date.UTC(2026, 0, 1, 0, 1, 59, 999), "42080.00")
    ];

    const candles = normalizeBinanceRows(asset, rows, from, to, new Date(Date.UTC(2026, 0, 1, 0, 3)));

    expect(candles).toHaveLength(2);
    expect(candles[0].close).toBe("42060.00");
  });

  it("builds a previous-day UTC daily window with overlap", () => {
    const window = dailyWindow(new Date(Date.UTC(2026, 6, 6, 12)), 15);

    expect(window.from.toISOString()).toBe("2026-07-04T23:45:00.000Z");
    expect(window.to.toISOString()).toBe("2026-07-06T00:00:00.000Z");
  });
});

describe("batch idempotence key", () => {
  it("keeps one candle per time_open", () => {
    const first: Candle = { ...normalizeBinanceKline(asset, kline(0, 59_999), new Date(60_000))!, close: "1" };
    const second: Candle = { ...first, close: "2" };

    expect(dedupeCandles([first, second])).toEqual([second]);
  });
});
