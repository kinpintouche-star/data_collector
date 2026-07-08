import type { LiveSource } from "./types";

export const LIVE_SOURCES: LiveSource[] = [
  liveCrypto("BTCUSD", "BTCUSDT", "BTC-USD", "XBTUSD", 10),
  liveCrypto("ETHUSD", "ETHUSDT", "ETH-USD", "ETHUSD", 10),
  liveCrypto("BNBUSD", "BNBUSDT", "BNB-USD", "BNBUSD", 20),
  liveCrypto("SOLUSD", "SOLUSDT", "SOL-USD", "SOLUSD", 20),
  liveCrypto("XRPUSD", "XRPUSDT", "XRP-USD", "XRPUSD", 30),
  liveCrypto("ADAUSD", "ADAUSDT", "ADA-USD", "ADAUSD", 30),
  liveCrypto("DOGEUSD", "DOGEUSDT", "DOGE-USD", "DOGEUSD", 30),
  liveCrypto("AVAXUSD", "AVAXUSDT", "AVAX-USD", "AVAXUSD", 30),
  liveCrypto("LINKUSD", "LINKUSDT", "LINK-USD", "LINKUSD", 30)
];

function liveCrypto(
  symbolCode: string,
  sourceSymbol: string,
  providerSymbol: string,
  fallbackProviderSymbol: string,
  priority: number
): LiveSource {
  return {
    symbolCode,
    sourceName: "binance",
    provider: "coinbase",
    sourceSymbol,
    providerSymbol,
    fallbackProvider: "kraken",
    fallbackProviderSymbol,
    timeframe: "M1",
    pollIntervalMinutes: 1440,
    retentionDays: 180,
    enabled: true,
    priority,
    collectionMode: "daily",
    overlapMinutes: 15
  };
}
