import type { Candle } from "../types";

export const M1_MS = 60_000;

export function dailyWindow(now = new Date(), overlapMinutes = 15): { from: Date; to: Date } {
  const todayUtc = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const to = new Date(todayUtc);
  const from = new Date(todayUtc - 24 * 60 * M1_MS - overlapMinutes * M1_MS);
  return { from, to };
}

export function dedupeCandles(candles: Candle[]): Candle[] {
  const unique = new Map<string, Candle>();
  for (const candle of candles) {
    unique.set(candle.timeOpen, candle);
  }
  return [...unique.values()].sort((a, b) => Date.parse(a.timeOpen) - Date.parse(b.timeOpen));
}

export function providerSymbol(asset: { providerSymbol?: string; sourceSymbol: string }): string {
  return asset.providerSymbol ?? asset.sourceSymbol;
}

export function compactError(error: unknown, limit = 420): string {
  const message = error instanceof Error ? error.message : String(error);
  return message.length <= limit ? message : `${message.slice(0, limit)}...`;
}
