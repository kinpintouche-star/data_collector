import { neon } from "@neondatabase/serverless";
import { shouldOpenIncident } from "./policy";
import type { AssetResult, Candle, Env, LiveSource, TriggerType } from "./types";

type SqlClient = ReturnType<typeof neon>;

export function sqlClient(env: Env): SqlClient {
  return neon(env.DATABASE_URL);
}

export async function createCollectorRun(sql: SqlClient, triggerType: TriggerType): Promise<string> {
  const rows = (await sql`
    INSERT INTO collector_runs (trigger_type, status, started_at, metadata)
    VALUES (${triggerType}, 'running', now(), ${JSON.stringify({ runtime: "cloudflare-worker" })}::jsonb)
    RETURNING id
  `) as Array<Record<string, unknown>>;
  return String(rows[0].id);
}

export async function finishCollectorRun(
  sql: SqlClient,
  runId: string,
  status: "completed" | "failed",
  summary: {
    assetsRequested: number;
    assetsSucceeded: number;
    assetsFailed: number;
    rowsFetched: number;
    rowsWritten: number;
    errorMessage?: string;
  }
): Promise<void> {
  await sql`
    UPDATE collector_runs
    SET
      status = ${status},
      finished_at = now(),
      duration_ms = EXTRACT(EPOCH FROM (now() - started_at))::int * 1000,
      assets_requested = ${summary.assetsRequested},
      assets_succeeded = ${summary.assetsSucceeded},
      assets_failed = ${summary.assetsFailed},
      rows_fetched = ${summary.rowsFetched},
      rows_written = ${summary.rowsWritten},
      error_message = ${summary.errorMessage ?? null}
    WHERE id = ${runId}::uuid
  `;
}

export async function upsertCandles(sql: SqlClient, asset: LiveSource, candles: Candle[]): Promise<number> {
  if (candles.length === 0) {
    return 0;
  }
  const sourceStateId = await resolveSourceStateId(sql, asset);
  const payload = JSON.stringify(
    candles.map((candle) => ({
      time_open: candle.timeOpen,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
      tick_volume: candle.tickVolume,
      real_volume: candle.realVolume,
      spread: candle.spread
    }))
  );
  const rows = (await sql`
    WITH payload AS (
      SELECT *
      FROM jsonb_to_recordset(${payload}::jsonb) AS x(
        time_open timestamptz,
        open double precision,
        high double precision,
        low double precision,
        close double precision,
        tick_volume bigint,
        real_volume double precision,
        spread double precision
      )
    )
    INSERT INTO live_market_candles (
      source_state_id,
      time_open,
      open,
      high,
      low,
      close,
      tick_volume,
      real_volume,
      spread,
      ingested_at
    )
    SELECT
      ${sourceStateId}::uuid,
      time_open,
      open,
      high,
      low,
      close,
      tick_volume,
      real_volume,
      spread,
      now()
    FROM payload
    ON CONFLICT (source_state_id, time_open)
    DO UPDATE SET
      open = EXCLUDED.open,
      high = EXCLUDED.high,
      low = EXCLUDED.low,
      close = EXCLUDED.close,
      tick_volume = EXCLUDED.tick_volume,
      real_volume = EXCLUDED.real_volume,
      spread = EXCLUDED.spread,
      ingested_at = now()
    RETURNING source_state_id
  `) as Array<Record<string, unknown>>;
  return rows.length;
}

export async function pruneRetention(sql: SqlClient, asset: LiveSource): Promise<number> {
  const sourceStateId = await resolveSourceStateId(sql, asset);
  const rows = (await sql`
    DELETE FROM live_market_candles
    WHERE source_state_id = ${sourceStateId}::uuid
      AND time_open < now() - (${asset.retentionDays}::text || ' days')::interval
    RETURNING source_state_id
  `) as Array<Record<string, unknown>>;
  return rows.length;
}

export async function recordAssetResult(sql: SqlClient, result: AssetResult): Promise<void> {
  const ids = await resolveIds(sql, result.asset);
  const isOk = result.status === "ok";
  const lagSeconds = result.lastCandleTime
    ? Math.max(0, Math.floor((Date.now() - Date.parse(result.lastCandleTime)) / 1000))
    : null;
  const stateRows = (await sql`
    INSERT INTO collector_source_state (
      symbol_id,
      source_id,
      source_symbol,
      provider,
      timeframe,
      priority,
      enabled,
      poll_interval_minutes,
      retention_days,
      collection_mode,
      status,
      last_candle_time,
      last_success_at,
      last_error_at,
      last_error_message,
      consecutive_failures,
      lag_seconds,
      metadata,
      updated_at
    )
    VALUES (
      ${ids.symbolId}::uuid,
      ${ids.sourceId}::uuid,
      ${result.asset.sourceSymbol},
      ${result.asset.provider},
      ${result.asset.timeframe},
      ${result.asset.priority},
      ${result.asset.enabled},
      ${result.asset.pollIntervalMinutes},
      ${result.asset.retentionDays},
      ${result.asset.collectionMode},
      ${result.status},
      ${result.lastCandleTime},
      ${isOk ? new Date().toISOString() : null},
      ${isOk ? null : new Date().toISOString()},
      ${result.errorMessage ?? null},
      ${isOk ? 0 : 1},
      ${lagSeconds},
      ${JSON.stringify({ rows_fetched: result.rowsFetched, rows_written: result.rowsWritten })}::jsonb,
      now()
    )
    ON CONFLICT (symbol_id, source_id, timeframe)
    DO UPDATE SET
      source_symbol = EXCLUDED.source_symbol,
      provider = EXCLUDED.provider,
      priority = EXCLUDED.priority,
      enabled = EXCLUDED.enabled,
      poll_interval_minutes = EXCLUDED.poll_interval_minutes,
      retention_days = EXCLUDED.retention_days,
      collection_mode = EXCLUDED.collection_mode,
      status = EXCLUDED.status,
      last_candle_time = COALESCE(EXCLUDED.last_candle_time, collector_source_state.last_candle_time),
      last_success_at = COALESCE(EXCLUDED.last_success_at, collector_source_state.last_success_at),
      last_error_at = COALESCE(EXCLUDED.last_error_at, collector_source_state.last_error_at),
      last_error_message = EXCLUDED.last_error_message,
      consecutive_failures = CASE
        WHEN EXCLUDED.status = 'ok' THEN 0
        ELSE collector_source_state.consecutive_failures + 1
      END,
      lag_seconds = EXCLUDED.lag_seconds,
      metadata = EXCLUDED.metadata,
      updated_at = now()
    RETURNING consecutive_failures
  `) as Array<Record<string, unknown>>;
  const failures = Number(stateRows[0]?.consecutive_failures ?? 0);
  if (isOk) {
    await resolveOpenIncident(sql, result.asset, ids.symbolId, ids.sourceId);
  } else if (shouldOpenIncident(failures)) {
    await openIncident(sql, result, ids.symbolId, ids.sourceId, failures);
  }
}

async function openIncident(
  sql: SqlClient,
  result: AssetResult,
  symbolId: string,
  sourceId: string,
  failureCount: number
): Promise<void> {
  const incidentKey = incidentKeyFor(result.asset);
  await sql`
    INSERT INTO collector_incidents (
      incident_key,
      symbol_id,
      source_id,
      timeframe,
      severity,
      status,
      title,
      message,
      failure_count,
      first_seen_at,
      last_seen_at,
      metadata
    )
    VALUES (
      ${incidentKey},
      ${symbolId}::uuid,
      ${sourceId}::uuid,
      ${result.asset.timeframe},
      'warning',
      'open',
      ${`${result.asset.symbolCode} live collection failing`},
      ${result.errorMessage ?? "Collector failed without a provider error."},
      ${failureCount},
      now(),
      now(),
      ${JSON.stringify({ provider: result.asset.provider })}::jsonb
    )
    ON CONFLICT (incident_key)
    WHERE resolved_at IS NULL
    DO UPDATE SET
      status = 'open',
      message = EXCLUDED.message,
      failure_count = EXCLUDED.failure_count,
      last_seen_at = now()
  `;
}

async function resolveOpenIncident(sql: SqlClient, asset: LiveSource, symbolId: string, sourceId: string): Promise<void> {
  await sql`
    UPDATE collector_incidents
    SET status = 'resolved', resolved_at = now(), last_seen_at = now()
    WHERE incident_key = ${incidentKeyFor(asset)}
      AND symbol_id = ${symbolId}::uuid
      AND source_id = ${sourceId}::uuid
      AND resolved_at IS NULL
  `;
}

async function resolveIds(sql: SqlClient, asset: LiveSource): Promise<{ symbolId: string; sourceId: string }> {
  const rows = (await sql`
    SELECT s.id AS symbol_id, ds.id AS source_id
    FROM symbols s
    JOIN data_sources ds ON ds.name = ${asset.sourceName}
    WHERE s.symbol_code = ${asset.symbolCode}
    LIMIT 1
  `) as Array<Record<string, unknown>>;
  if (rows.length === 0) {
    throw new Error(`Missing symbol/source in DB: ${asset.symbolCode}/${asset.sourceName}`);
  }
  return {
    symbolId: String(rows[0].symbol_id),
    sourceId: String(rows[0].source_id)
  };
}

async function resolveSourceStateId(sql: SqlClient, asset: LiveSource): Promise<string> {
  const rows = (await sql`
    SELECT css.id
    FROM collector_source_state css
    JOIN symbols s ON s.id = css.symbol_id
    JOIN data_sources ds ON ds.id = css.source_id
    WHERE s.symbol_code = ${asset.symbolCode}
      AND ds.name = ${asset.sourceName}
      AND css.source_symbol = ${asset.sourceSymbol}
      AND css.timeframe = ${asset.timeframe}
    LIMIT 1
  `) as Array<Record<string, unknown>>;
  if (rows.length === 0) {
    throw new Error(`Missing live source state in DB: ${asset.symbolCode}/${asset.sourceName}/${asset.timeframe}`);
  }
  return String(rows[0].id);
}

function incidentKeyFor(asset: LiveSource): string {
  return `${asset.symbolCode}:${asset.sourceName}:${asset.timeframe}:collector_failure`;
}
