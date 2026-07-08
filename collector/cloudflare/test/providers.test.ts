import { describe, expect, it } from "vitest";
import { LIVE_SOURCES } from "../src/live-sources";
import { normalizeCoinbaseCandle, normalizeCoinbaseRows, type CoinbaseCandle } from "../src/providers/coinbase";
import { normalizeKrakenCandle, type KrakenCandle } from "../src/providers/kraken";

const asset = LIVE_SOURCES[0];

describe("coinbase normalization", () => {
  it("normalizes Coinbase M1 candles into canonical candles", () => {
    const row: CoinbaseCandle = [1767225600, 41900.5, 42100.25, 42000, 42050.75, 12.5];
    const candle = normalizeCoinbaseCandle(asset, row, new Date("2026-01-01T00:02:00.000Z"));

    expect(candle?.symbolCode).toBe("BTCUSD");
    expect(candle?.sourceName).toBe("binance");
    expect(candle?.sourceSymbol).toBe("BTCUSDT");
    expect(candle?.timeOpen).toBe("2026-01-01T00:00:00.000Z");
    expect(candle?.open).toBe("42000");
    expect(candle?.high).toBe("42100.25");
    expect(candle?.low).toBe("41900.5");
    expect(candle?.tickVolume).toBeNull();
    expect(candle?.realVolume).toBe(12.5);
    expect(candle?.metadata.provider).toBe("coinbase");
  });

  it("sorts Coinbase rows returned newest-first", () => {
    const rows: CoinbaseCandle[] = [
      [1767225660, 2, 3, 2.5, 2.75, 20],
      [1767225600, 1, 2, 1.5, 1.75, 10]
    ];
    const candles = normalizeCoinbaseRows(
      asset,
      rows,
      new Date("2026-01-01T00:00:00.000Z"),
      new Date("2026-01-01T00:02:00.000Z"),
      new Date("2026-01-01T00:03:00.000Z")
    );

    expect(candles.map((candle) => candle.timeOpen)).toEqual([
      "2026-01-01T00:00:00.000Z",
      "2026-01-01T00:01:00.000Z"
    ]);
  });

  it("ignores open Coinbase candles", () => {
    const row: CoinbaseCandle = [1767225600, 1, 2, 1.5, 1.75, 10];

    expect(normalizeCoinbaseCandle(asset, row, new Date("2026-01-01T00:00:30.000Z"))).toBeNull();
  });
});

describe("kraken normalization", () => {
  it("normalizes Kraken OHLC rows into canonical candles", () => {
    const row: KrakenCandle = [1767225600, "42000", "42100", "41900", "42050", "42025", "12.5", 120];
    const candle = normalizeKrakenCandle(asset, row, "XBTUSD", new Date("2026-01-01T00:02:00.000Z"));

    expect(candle?.timeOpen).toBe("2026-01-01T00:00:00.000Z");
    expect(candle?.open).toBe("42000");
    expect(candle?.high).toBe("42100");
    expect(candle?.low).toBe("41900");
    expect(candle?.close).toBe("42050");
    expect(candle?.tickVolume).toBe(120);
    expect(candle?.realVolume).toBe(12.5);
    expect(candle?.metadata.provider).toBe("kraken");
  });
});
