import { describe, expect, it } from "vitest";
import { isDueForDailyWindow, manualLimit, selectManualAssets } from "../src/index";
import { shouldOpenIncident, shouldResolveIncident } from "../src/policy";
import { LIVE_SOURCES } from "../src/live-sources";
import type { AssetResult } from "../src/types";

describe("incident policy", () => {
  it("opens incidents after three consecutive failures", () => {
    expect(shouldOpenIncident(1)).toBe(false);
    expect(shouldOpenIncident(2)).toBe(false);
    expect(shouldOpenIncident(3)).toBe(true);
  });

  it("resolves open incidents after a healthy asset result", () => {
    const result: AssetResult = {
      asset: LIVE_SOURCES[0],
      status: "ok",
      rowsFetched: 1440,
      rowsWritten: 1440,
      lastCandleTime: "2026-07-05T23:59:00.000Z"
    };

    expect(shouldResolveIncident(result)).toBe(true);
  });
});

describe("asset selection", () => {
  it("defaults manual runs to one asset unless a symbol is requested", () => {
    expect(selectManualAssets(new URLSearchParams()).map((asset) => asset.symbolCode)).toEqual(["BTCUSD"]);
    expect(selectManualAssets(new URLSearchParams("symbol=SOLUSD,ADAUSD")).map((asset) => asset.symbolCode)).toEqual([
      "SOLUSD",
      "ADAUSD"
    ]);
  });

  it("bounds manual due scheduler limits", () => {
    expect(manualLimit(new URLSearchParams())).toBe(1);
    expect(manualLimit(new URLSearchParams("limit=3"))).toBe(3);
    expect(manualLimit(new URLSearchParams("limit=99"))).toBe(9);
  });

  it("keeps assets due when the previous UTC day has gaps", () => {
    const targetLastCandle = new Date("2026-07-06T23:59:00.000Z");

    expect(
      isDueForDailyWindow(
        {
          last_candle_time: "2026-07-06T23:59:00.000Z",
          complete_day_candles: 1434
        },
        targetLastCandle
      )
    ).toBe(true);
    expect(
      isDueForDailyWindow(
        {
          last_candle_time: "2026-07-06T23:59:00.000Z",
          complete_day_candles: 1435
        },
        targetLastCandle
      )
    ).toBe(false);
  });
});
