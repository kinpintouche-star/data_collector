import {
  createCollectorRun,
  finishCollectorRun,
  pruneRetention,
  recordAssetResult,
  sqlClient,
  upsertCandles
} from "./db";
import { LIVE_SOURCES } from "./live-sources";
import { fetchBinanceCandles } from "./providers/binance";
import { fetchCoinbaseCandles } from "./providers/coinbase";
import { compactError, dailyWindow } from "./providers/common";
import { fetchKrakenCandles } from "./providers/kraken";
import type { AssetResult, CollectorRunSummary, Env, LiveSource, TriggerType } from "./types";

const SCHEDULED_BATCH_SIZE = 1;
// Free provider APIs can miss an isolated minute; large gaps should still keep an asset due.
const MIN_COMPLETE_DAILY_M1_CANDLES = 1435;

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      if (!isAuthorized(request, env)) {
        return jsonResponse({ error: "unauthorized" }, 401);
      }
      return health(env);
    }
    if (url.pathname === "/run-now") {
      if (!isAuthorized(request, env)) {
        return jsonResponse({ error: "unauthorized" }, 401);
      }
      const now = new Date();
      const assets = url.searchParams.has("due")
        ? await selectDueAssets(sqlClient(env), now, manualLimit(url.searchParams))
        : selectManualAssets(url.searchParams);
      const summary = await runCollector(env, "manual", now, assets);
      return jsonResponse(summary);
    }
    return jsonResponse({ error: "not_found" }, 404);
  },

  async scheduled(_event: ScheduledEvent, env: Env, ctx: ExecutionContext): Promise<void> {
    ctx.waitUntil(runScheduledCollector(env));
  }
};

export async function runScheduledCollector(env: Env, now = new Date()): Promise<CollectorRunSummary> {
  const assets = await selectDueAssets(sqlClient(env), now, SCHEDULED_BATCH_SIZE);
  return runCollector(env, "scheduled", now, assets);
}

export async function runCollector(
  env: Env,
  triggerType: TriggerType,
  now = new Date(),
  selectedAssets: LiveSource[] = LIVE_SOURCES
): Promise<CollectorRunSummary> {
  const sql = sqlClient(env);
  const runId = await createCollectorRun(sql, triggerType);
  const assets = selectedAssets.filter((asset) => asset.enabled).sort((a, b) => a.priority - b.priority);
  const results: AssetResult[] = [];
  let runError: string | undefined;

  try {
    for (const asset of assets) {
      const result = await collectAsset(sql, asset, now);
      results.push(result);
    }
  } catch (error) {
    runError = error instanceof Error ? error.message : String(error);
  }

  const summary = summarizeRun(runId, assets.length, results, runError);
  await finishCollectorRun(sql, runId, summary.status, {
    assetsRequested: summary.assetsRequested,
    assetsSucceeded: summary.assetsSucceeded,
    assetsFailed: summary.assetsFailed,
    rowsFetched: summary.rowsFetched,
    rowsWritten: summary.rowsWritten,
    errorMessage: summary.errorMessage
  });
  return summary;
}

export function selectManualAssets(params: URLSearchParams): LiveSource[] {
  const all = params.get("all");
  if (all === "1" || all === "true") {
    return LIVE_SOURCES;
  }

  const symbols = params
    .getAll("symbol")
    .flatMap((value) => value.split(","))
    .map((value) => value.trim().toUpperCase())
    .filter(Boolean);
  if (symbols.length > 0) {
    return LIVE_SOURCES.filter((asset) => symbols.includes(asset.symbolCode));
  }

  const shards = Number(params.get("shards"));
  const shard = Number(params.get("shard"));
  if (Number.isInteger(shards) && Number.isInteger(shard) && shards > 0 && shard >= 0 && shard < shards) {
    return LIVE_SOURCES.filter((_, index) => index % shards === shard);
  }

  const limit = manualLimit(params);
  return LIVE_SOURCES.filter((asset) => asset.enabled).sort((a, b) => a.priority - b.priority).slice(0, limit);
}

export function manualLimit(params: URLSearchParams): number {
  return Math.max(1, Math.min(9, Number(params.get("limit") ?? "1") || 1));
}

export async function selectDueAssets(
  sql: ReturnType<typeof sqlClient>,
  now = new Date(),
  limit = SCHEDULED_BATCH_SIZE
): Promise<LiveSource[]> {
  const window = dailyWindow(now);
  const completeDayFrom = new Date(window.to.getTime() - 24 * 60 * 60_000);
  const targetLastCandle = new Date(window.to.getTime() - 60_000);
  const rows = (await sql`
    SELECT
      s.symbol_code,
      css.last_candle_time,
      css.consecutive_failures,
      COUNT(lmc.time_open)::integer AS complete_day_candles
    FROM collector_source_state css
    JOIN symbols s ON s.id = css.symbol_id
    LEFT JOIN live_market_candles lmc
      ON lmc.source_state_id = css.id
      AND lmc.time_open >= ${completeDayFrom.toISOString()}::timestamptz
      AND lmc.time_open < ${window.to.toISOString()}::timestamptz
    WHERE css.timeframe = 'M1'
      AND css.enabled = true
    GROUP BY s.symbol_code, css.last_candle_time, css.consecutive_failures
  `) as Array<Record<string, unknown>>;
  const stateBySymbol = new Map(rows.map((row) => [String(row.symbol_code), row]));

  return LIVE_SOURCES.filter((asset) => {
    if (!asset.enabled) {
      return false;
    }
    const state = stateBySymbol.get(asset.symbolCode);
    return isDueForDailyWindow(state, targetLastCandle);
  })
    .sort((a, b) => {
      const aState = stateBySymbol.get(a.symbolCode);
      const bState = stateBySymbol.get(b.symbolCode);
      const aFailures = Number(aState?.consecutive_failures ?? 0);
      const bFailures = Number(bState?.consecutive_failures ?? 0);
      return aFailures - bFailures || a.priority - b.priority || a.symbolCode.localeCompare(b.symbolCode);
    })
    .slice(0, limit);
}

export function isDueForDailyWindow(
  state: Record<string, unknown> | undefined,
  targetLastCandle: Date,
  expectedCandles = MIN_COMPLETE_DAILY_M1_CANDLES
): boolean {
  if (!state) {
    return true;
  }
  const lastCandle = timestampMs(state.last_candle_time);
  const completeDayCandles = Number(state.complete_day_candles ?? 0);
  return (
    !Number.isFinite(lastCandle) ||
    lastCandle < targetLastCandle.getTime() ||
    !Number.isFinite(completeDayCandles) ||
    completeDayCandles < expectedCandles
  );
}

function timestampMs(value: unknown): number {
  if (value instanceof Date) {
    return value.getTime();
  }
  if (typeof value === "string" || typeof value === "number") {
    return Date.parse(String(value));
  }
  return 0;
}

async function collectAsset(sql: ReturnType<typeof sqlClient>, asset: LiveSource, now: Date): Promise<AssetResult> {
  try {
    const window = dailyWindow(now, asset.overlapMinutes);
    const candles = await fetchCandles(asset, window.from, window.to, now);
    const rowsWritten = await upsertCandles(sql, asset, candles);
    await pruneRetention(sql, asset);
    const lastCandleTime = candles.length > 0 ? candles[candles.length - 1].timeOpen : null;
    const result: AssetResult = {
      asset,
      status: lastCandleTime ? "ok" : "stale",
      rowsFetched: candles.length,
      rowsWritten,
      lastCandleTime,
      errorMessage: lastCandleTime ? undefined : "No closed candles returned for the daily window."
    };
    await recordAssetResult(sql, result);
    return result;
  } catch (error) {
    const result: AssetResult = {
      asset,
      status: "error",
      rowsFetched: 0,
      rowsWritten: 0,
      lastCandleTime: null,
      errorMessage: error instanceof Error ? error.message : String(error)
    };
    await recordAssetResult(sql, result);
    return result;
  }
}

async function fetchCandles(asset: LiveSource, from: Date, to: Date, now: Date) {
  if (asset.provider === "binance") {
    return fetchBinanceCandles(asset, from, to, now);
  }
  if (asset.provider === "coinbase") {
    let coinbaseError: unknown;
    try {
      const candles = await fetchCoinbaseCandles(asset, from, to, now);
      if (candles.length > 0 || asset.fallbackProvider !== "kraken") {
        return candles;
      }
    } catch (error) {
      coinbaseError = error;
      if (asset.fallbackProvider !== "kraken") {
        throw error;
      }
    }
    try {
      const candles = await fetchKrakenCandles(asset, from, to, now);
      if (candles.length > 0) {
        return candles;
      }
      throw new Error("Kraken returned no closed candles for the requested window.");
    } catch (krakenError) {
      throw new Error(`Coinbase failed: ${compactError(coinbaseError ?? "empty response")}; Kraken fallback failed: ${compactError(krakenError)}`);
    }
  }
  if (asset.provider === "kraken") {
    return fetchKrakenCandles(asset, from, to, now);
  }
  throw new Error(`Unsupported live provider: ${asset.provider}`);
}

function summarizeRun(
  runId: string,
  assetsRequested: number,
  results: AssetResult[],
  runError?: string
): CollectorRunSummary {
  const assetsFailed = results.filter((result) => result.status === "error").length + (runError ? 1 : 0);
  return {
    runId,
    status: runError ? "failed" : "completed",
    assetsRequested,
    assetsSucceeded: results.filter((result) => result.status === "ok").length,
    assetsFailed,
    rowsFetched: results.reduce((total, result) => total + result.rowsFetched, 0),
    rowsWritten: results.reduce((total, result) => total + result.rowsWritten, 0),
    errorMessage: runError,
    results
  };
}

async function health(env: Env): Promise<Response> {
  const sql = sqlClient(env);
  const rows = (await sql`
    SELECT status, started_at, finished_at, rows_written, error_message
    FROM collector_runs
    ORDER BY started_at DESC
    LIMIT 1
  `) as Array<Record<string, unknown>>;
  return jsonResponse({
    ok: true,
    service: "ict-live-collector",
    lastRun: rows[0] ?? null,
    enabledSources: LIVE_SOURCES.filter((asset) => asset.enabled).length,
    scheduledBatchSize: SCHEDULED_BATCH_SIZE,
    scheduleMode: "due_asset_every_30_minutes"
  });
}

function isAuthorized(request: Request, env: Env): boolean {
  const header = request.headers.get("authorization") ?? "";
  return header === `Bearer ${env.COLLECTOR_TOKEN}`;
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload, null, 2), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" }
  });
}
