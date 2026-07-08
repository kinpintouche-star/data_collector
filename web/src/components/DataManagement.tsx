import { useEffect, useMemo, useState } from "react";
import { CloudDownload, Database, RefreshCcw, Search, Square, SquareCheckBig } from "lucide-react";
import { getDataApiUsage, getDataCoverage, getDataFetchJob, launchDataFetch } from "../api";
import type { DataApiUsagePayload, DataCoveragePayload, DataCoverageRow, DataFetchChannel, DataFetchJob } from "../types";

const channelLabels: Record<DataFetchChannel, string> = {
  auto: "Auto",
  neon: "Neon",
  databento: "Databento"
};

function rowKey(row: DataCoverageRow): string {
  return `${row.symbol_code}::${row.source_name}`;
}

function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return value.toLocaleString("fr-FR", { maximumFractionDigits: digits });
}

function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("fr-FR", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function statusLabel(row: DataCoverageRow): string {
  if (row.freshness_status === "empty") {
    return "Vide";
  }
  if (row.complete_day_ok) {
    return row.today_present ? "Aujourd'hui" : "Jour complet OK";
  }
  return "Retard";
}

function channelIsApplicable(row: DataCoverageRow, channel: DataFetchChannel): boolean {
  if (channel === "auto") {
    return true;
  }
  if (channel === "neon") {
    return row.source_type !== "databento";
  }
  if (channel === "databento") {
    return row.source_type === "databento";
  }
  return false;
}

function configMissing(rows: DataCoverageRow[], channel: DataFetchChannel, settings: DataCoveragePayload["settings"] | null): boolean {
  if (!settings) {
    return true;
  }
  const channels =
    channel === "auto"
      ? new Set(rows.map((row) => row.recommended_channel.toLowerCase()))
      : new Set([channel]);
  if (channels.has("neon") && !settings.neon_configured) {
    return true;
  }
  if (channels.has("databento") && !settings.databento_configured) {
    return true;
  }
  return false;
}

function resultText(value: unknown): string {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

export function DataManagement() {
  const [coverage, setCoverage] = useState<DataCoveragePayload | null>(null);
  const [usage, setUsage] = useState<DataApiUsagePayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [channelFilter, setChannelFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [groupFilter, setGroupFilter] = useState("all");
  const [job, setJob] = useState<DataFetchJob | null>(null);
  const [fallbackDays, setFallbackDays] = useState(180);
  const [overlapMinutes, setOverlapMinutes] = useState(5);
  const [neonLimit, setNeonLimit] = useState(250000);
  const [maxDatabentoUsd, setMaxDatabentoUsd] = useState(5);

  const rows = coverage?.rows ?? [];
  const groups = useMemo(() => Array.from(new Set(rows.map((row) => row.group).filter(Boolean))).sort() as string[], [rows]);
  const filteredRows = useMemo(() => {
    const query = search.trim().toLowerCase();
    return rows.filter((row) => {
      if (channelFilter !== "all" && row.recommended_channel !== channelFilter) {
        return false;
      }
      if (groupFilter !== "all" && row.group !== groupFilter) {
        return false;
      }
      if (statusFilter !== "all") {
        if (statusFilter === "complete" && !row.complete_day_ok) {
          return false;
        }
        if (statusFilter === "today" && !row.today_present) {
          return false;
        }
        if (statusFilter === "stale" && row.freshness_status !== "stale") {
          return false;
        }
        if (statusFilter === "empty" && row.freshness_status !== "empty") {
          return false;
        }
      }
      if (!query) {
        return true;
      }
      return `${row.symbol_code} ${row.source_name} ${row.group ?? ""} ${row.source_type ?? ""}`.toLowerCase().includes(query);
    });
  }, [channelFilter, groupFilter, rows, search, statusFilter]);

  const selectedRows = useMemo(() => {
    const keys = new Set(selectedKeys);
    return rows.filter((row) => keys.has(rowKey(row)));
  }, [rows, selectedKeys]);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [coveragePayload, usagePayload] = await Promise.all([getDataCoverage(), getDataApiUsage()]);
      setCoverage(coveragePayload);
      setUsage(usagePayload);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, []);

  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.status)) {
      return;
    }
    const handle = window.setInterval(async () => {
      try {
        const next = await getDataFetchJob(job.id);
        setJob(next);
        if (!["queued", "running"].includes(next.status)) {
          window.clearInterval(handle);
          void loadData();
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        window.clearInterval(handle);
      }
    }, 1500);
    return () => window.clearInterval(handle);
  }, [job]);

  const toggleRow = (key: string) => {
    setSelectedKeys((current) => (current.includes(key) ? current.filter((item) => item !== key) : [...current, key]));
  };

  const selectFiltered = () => setSelectedKeys(filteredRows.map(rowKey));
  const clearSelection = () => setSelectedKeys([]);
  const selectChannel = (channel: string) => setSelectedKeys(rows.filter((row) => row.recommended_channel === channel).map(rowKey));

  const runFetch = async (channel: DataFetchChannel, explicitRows: DataCoverageRow[] = selectedRows) => {
    const applicableRows = explicitRows.filter((row) => channelIsApplicable(row, channel));
    if (!applicableRows.length) {
      setError("Aucun actif compatible avec ce canal.");
      return;
    }
    setError(null);
    try {
      const next = await launchDataFetch({
        channel,
        assets: applicableRows.map((row) => ({ symbol_code: row.symbol_code, source_name: row.source_name })),
        fallback_days: fallbackDays,
        overlap_minutes: overlapMinutes,
        neon_limit: neonLimit,
        max_databento_usd: maxDatabentoUsd
      });
      setJob(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const actionDisabled = (channel: DataFetchChannel) => {
    const applicableRows = selectedRows.filter((row) => channelIsApplicable(row, channel));
    return !selectedRows.length || !applicableRows.length || configMissing(applicableRows, channel, coverage?.settings ?? null);
  };

  return (
    <section className="data-page">
      <header className="section-header">
        <div>
          <span className="eyebrow">Data Management</span>
          <h1>Données marché</h1>
        </div>
        <div className="toolbar">
          <button className="icon-button" onClick={() => void loadData()} type="button">
            <RefreshCcw size={17} />
            <span>Refresh coverage</span>
          </button>
        </div>
      </header>

      {error && <div className="alert error">{error}</div>}
      {loading && <div className="alert">Chargement données...</div>}

      <div className="data-kpis">
        <div>
          <span>Actifs</span>
          <strong>{formatNumber(coverage?.summary.assets)}</strong>
        </div>
        <div>
          <span>Jour complet OK</span>
          <strong>{formatNumber(coverage?.summary.complete_day_ok)}</strong>
        </div>
        <div>
          <span>Aujourd'hui présent</span>
          <strong>{formatNumber(coverage?.summary.today_present)}</strong>
        </div>
        <div>
          <span>Vides</span>
          <strong>{formatNumber(coverage?.summary.empty)}</strong>
        </div>
        <div>
          <span>Candles</span>
          <strong>{formatNumber(coverage?.summary.total_candles)}</strong>
        </div>
        <div>
          <span>Flags</span>
          <strong>{formatNumber(coverage?.summary.flagged_candles)}</strong>
        </div>
      </div>

      <section className="data-controls">
        <label className="field search-field">
          <span>Recherche</span>
          <div>
            <Search size={16} />
            <input placeholder="EURUSD, MNQ, crypto..." value={search} onChange={(event) => setSearch(event.target.value)} />
          </div>
        </label>
        <label className="field">
          <span>Canal</span>
          <select value={channelFilter} onChange={(event) => setChannelFilter(event.target.value)}>
            <option value="all">Tous</option>
            <option value="Neon">Neon</option>
            <option value="Databento">Databento</option>
          </select>
        </label>
        <label className="field">
          <span>Statut</span>
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="all">Tous</option>
            <option value="complete">Jour complet OK</option>
            <option value="today">Aujourd'hui présent</option>
            <option value="stale">Retard</option>
            <option value="empty">Vide</option>
          </select>
        </label>
        <label className="field">
          <span>Groupe</span>
          <select value={groupFilter} onChange={(event) => setGroupFilter(event.target.value)}>
            <option value="all">Tous</option>
            {groups.map((group) => (
              <option key={group} value={group}>
                {group}
              </option>
            ))}
          </select>
        </label>
      </section>

      <section className="data-actions">
        <button className="icon-button" onClick={selectFiltered} type="button">
          <SquareCheckBig size={16} />
          <span>All filtrés</span>
        </button>
        <button className="icon-button" onClick={clearSelection} type="button">
          <Square size={16} />
          <span>Clear</span>
        </button>
        <button className="icon-button" onClick={() => selectChannel("Neon")} type="button">Neon only</button>
        <button className="icon-button" onClick={() => selectChannel("Databento")} type="button">Databento only</button>
        <button className="primary-inline" disabled={actionDisabled("neon")} onClick={() => void runFetch("neon")} type="button">
          <CloudDownload size={17} />
          <span>Fetch Neon</span>
        </button>
        <button className="icon-button" disabled={actionDisabled("databento")} onClick={() => void runFetch("databento")} type="button">Fetch Databento</button>
      </section>

      <section className="data-settings">
        <label className="field">
          <span>Fallback days</span>
          <input type="number" min={1} max={3650} value={fallbackDays} onChange={(event) => setFallbackDays(Number(event.target.value))} />
        </label>
        <label className="field">
          <span>Overlap minutes</span>
          <input type="number" min={0} max={240} value={overlapMinutes} onChange={(event) => setOverlapMinutes(Number(event.target.value))} />
        </label>
        <label className="field">
          <span>Neon row limit</span>
          <input type="number" min={1000} max={2000000} step={10000} value={neonLimit} onChange={(event) => setNeonLimit(Number(event.target.value))} />
        </label>
        <label className="field">
          <span>Max Databento USD</span>
          <input type="number" min={0.01} max={125} step={0.25} value={maxDatabentoUsd} onChange={(event) => setMaxDatabentoUsd(Number(event.target.value))} />
        </label>
      </section>

      <div className="data-config-line">
        <Database size={16} />
        <span>Neon: {coverage?.settings.neon_configured ? "configuré" : "non configuré"}</span>
        <span>Databento: {coverage?.settings.databento_configured ? "configuré" : "non configuré"}</span>
        <span>Sélection: {selectedRows.length}</span>
      </div>

      {job && (
        <section className="data-job-panel">
          <header>
            <strong>Job {channelLabels[job.channel]}</strong>
            <span className={`status-pill ${job.status}`}>{job.status}</span>
            <small>
              {job.completed_assets} done | {job.skipped_assets} skipped | {job.failed_assets} failed / {job.total_assets}
            </small>
          </header>
          {[...job.results, ...job.errors].length > 0 && (
            <div className="job-result-table">
              {[...job.results, ...job.errors].map((result, index) => (
                <div key={index}>
                  <strong>{resultText(result.symbol_code)} / {resultText(result.source_name)}</strong>
                  <span>{resultText(result.channel)}</span>
                  <span>{resultText(result.status)}</span>
                  <small>{resultText(result.reason ?? result.error ?? result.rows_written ?? result.rows_inserted ?? "-")}</small>
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      <section className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th></th>
              <th>Actif</th>
              <th>Source</th>
              <th>Canal</th>
              <th>Statut</th>
              <th>Local last</th>
              <th>Neon last</th>
              <th>Rows</th>
              <th>Flags</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filteredRows.map((row) => {
              const key = rowKey(row);
              const checked = selectedKeys.includes(key);
              return (
                <tr key={key} className={row.needs_attention ? "needs-attention" : ""}>
                  <td>
                    <input checked={checked} onChange={() => toggleRow(key)} type="checkbox" />
                  </td>
                  <td>
                    <strong>{row.symbol_code}</strong>
                    <small>{row.group ?? row.asset_type ?? "-"}</small>
                  </td>
                  <td>
                    <span>{row.source_name}</span>
                    <small>{row.source_type ?? "-"}</small>
                  </td>
                  <td>{row.recommended_channel}</td>
                  <td><span className={`freshness ${row.freshness_status}`}>{statusLabel(row)}</span></td>
                  <td>{formatDate(row.local_last)}</td>
                  <td>{formatDate(row.neon_last)}</td>
                  <td>{formatNumber(row.candle_rows)}</td>
                  <td>{formatNumber(row.flagged_candles)}</td>
                  <td>
                    <div className="row-actions">
                      {(["neon", "databento"] as DataFetchChannel[]).map((channel) => (
                        <button
                          disabled={!channelIsApplicable(row, channel) || configMissing([row], channel, coverage?.settings ?? null)}
                          key={channel}
                          onClick={() => void runFetch(channel, [row])}
                          type="button"
                        >
                          {channelLabels[channel]}
                        </button>
                      ))}
                    </div>
                  </td>
                </tr>
              );
            })}
            {!filteredRows.length && (
              <tr>
                <td colSpan={10}>
                  <div className="empty-state">Aucun actif ne correspond aux filtres.</div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <section className="api-usage-panel">
        <header>
          <span>API Usage</span>
          <small>Canaux disponibles et actifs concernés</small>
        </header>
        {(usage?.rows ?? []).map((row) => (
          <div key={row.fetch_channel}>
            <strong>{row.fetch_channel}</strong>
            <span>{row.asset_count} actifs</span>
            <p>{row.usage}</p>
            <small>{row.limits}</small>
            <small>{row.current_split}</small>
            <small>{row.cost}</small>
          </div>
        ))}
      </section>
    </section>
  );
}
