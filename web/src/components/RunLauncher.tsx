import { AlertTriangle, Check, Play, RefreshCcw, Square, SquareCheckBig } from "lucide-react";
import type { BacktestAssetOption, BacktestJob, BacktestLaunchPayload, BacktestOptions } from "../types";

type RunLauncherProps = {
  options: BacktestOptions | null;
  loading: boolean;
  selectedAssetKeys: string[];
  strategyConfig: string;
  fromDate: string;
  toDate: string;
  label: string;
  job: BacktestJob | null;
  error: string | null;
  onRefreshOptions: () => void;
  onStrategyChange: (value: string) => void;
  onFromDateChange: (value: string) => void;
  onToDateChange: (value: string) => void;
  onLabelChange: (value: string) => void;
  onToggleAsset: (key: string) => void;
  onSelectAll: () => void;
  onClearAssets: () => void;
  onLaunch: (payload: BacktestLaunchPayload) => void;
};

function assetKey(asset: BacktestAssetOption): string {
  return `${asset.symbol_code}::${asset.source_name}`;
}

function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("fr-FR", { year: "2-digit", month: "short", day: "2-digit" }).format(new Date(value));
}

function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return value.toLocaleString("fr-FR", { maximumFractionDigits: 0 });
}

function jobTone(status: BacktestJob["status"] | undefined): string {
  if (!status) {
    return "idle";
  }
  if (status === "completed") {
    return "good";
  }
  if (status === "failed" || status === "partial") {
    return "bad";
  }
  return "running";
}

function strategyPayload(strategyConfig: string): Pick<BacktestLaunchPayload, "strategy_config" | "strategy_definition_id"> {
  if (strategyConfig.startsWith("builder:")) {
    return { strategy_definition_id: strategyConfig.replace("builder:", "") };
  }
  return { strategy_config: strategyConfig };
}

export function RunLauncher({
  options,
  loading,
  selectedAssetKeys,
  strategyConfig,
  fromDate,
  toDate,
  label,
  job,
  error,
  onRefreshOptions,
  onStrategyChange,
  onFromDateChange,
  onToDateChange,
  onLabelChange,
  onToggleAsset,
  onSelectAll,
  onClearAssets,
  onLaunch
}: RunLauncherProps) {
  const selectedAssets =
    options?.assets.filter((asset) => selectedAssetKeys.includes(assetKey(asset))).map((asset) => ({
      symbol_code: asset.symbol_code,
      source_name: asset.source_name
    })) ?? [];
  const canLaunch = selectedAssets.length > 0 && strategyConfig && fromDate && toDate && job?.status !== "running";

  return (
    <section className="run-launcher">
      <header className="launcher-header">
        <div>
          <span className="eyebrow">Run Lab</span>
          <h1>Lancer un backtest</h1>
        </div>
        <button className="icon-button" disabled={loading} onClick={onRefreshOptions} title="Rafraichir les donnees" type="button">
          <RefreshCcw size={17} />
          <span>Refresh</span>
        </button>
      </header>

      {error && <div className="alert error">{error}</div>}

      <div className="launcher-layout">
        <section className="launcher-panel">
          <div className="launcher-fields">
            <label className="field">
              <span>Strategie</span>
              <select value={strategyConfig} onChange={(event) => onStrategyChange(event.target.value)}>
                {(options?.strategies ?? []).map((strategy) => (
                  <option value={strategy.path} key={strategy.path}>
                    {strategy.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Label</span>
              <input value={label} onChange={(event) => onLabelChange(event.target.value)} placeholder="Auto" />
            </label>
            <label className="field">
              <span>From</span>
              <input type="date" value={fromDate} onChange={(event) => onFromDateChange(event.target.value)} />
            </label>
            <label className="field">
              <span>To</span>
              <input type="date" value={toDate} onChange={(event) => onToDateChange(event.target.value)} />
            </label>
          </div>

          <div className="asset-toolbar">
            <span>{selectedAssets.length} actifs selectionnes</span>
            <div>
              <button className="small-button" onClick={onSelectAll} type="button">
                <SquareCheckBig size={15} />
                All
              </button>
              <button className="small-button" onClick={onClearAssets} type="button">
                <Square size={15} />
                Clear
              </button>
            </div>
          </div>

          <div className="asset-select-grid">
            {(options?.assets ?? []).map((asset) => {
              const key = assetKey(asset);
              const selected = selectedAssetKeys.includes(key);
              return (
                <button className={selected ? "asset-option is-selected" : "asset-option"} key={key} onClick={() => onToggleAsset(key)} type="button">
                  {selected ? <SquareCheckBig size={17} /> : <Square size={17} />}
                  <div>
                    <strong>{asset.symbol_code}</strong>
                    <small>
                      {asset.source_name} | {formatDate(asset.start_time)} {"->"} {formatDate(asset.end_time)}
                    </small>
                  </div>
                  <span>{formatNumber(asset.candles)}</span>
                </button>
              );
            })}
            {!options?.assets.length && <div className="empty-state">Aucun actif M1 disponible en base locale.</div>}
          </div>

          <button
            className="primary-action"
            disabled={!canLaunch}
            onClick={() =>
              onLaunch({
                ...strategyPayload(strategyConfig),
                assets: selectedAssets,
                from: fromDate,
                to: toDate,
                timeframe: "M1",
                label: label || undefined
              })
            }
            type="button"
          >
            <Play size={18} />
            Lancer le run
          </button>
        </section>

        <aside className={`job-panel ${jobTone(job?.status)}`}>
          <header>
            <span>Dernier job</span>
            <strong>{job?.status ?? "idle"}</strong>
          </header>
          {job ? (
            <>
              <div className="job-progress">
                <div>
                  <span>Assets</span>
                  <strong>
                    {job.completed_assets}/{job.total_assets}
                  </strong>
                </div>
                <div>
                  <span>Fails</span>
                  <strong>{job.failed_assets}</strong>
                </div>
              </div>
              <div className="job-results">
                {job.results.map((result) => (
                  <div className="job-result-row" key={`${result.run_id}`}>
                    <Check size={16} />
                    <span>
                      {String(result.symbol_code)} / {String(result.source_name)}
                    </span>
                    <small>{String(result.trades ?? 0)} trades</small>
                  </div>
                ))}
                {job.errors.map((item) => (
                  <div className="job-result-row failed" key={`${item.symbol_code}-${item.source_name}`}>
                    <AlertTriangle size={16} />
                    <span>
                      {String(item.symbol_code)} / {String(item.source_name)}
                    </span>
                    <small>{String(item.error ?? "failed")}</small>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="empty-state">Aucun lancement depuis cette session.</div>
          )}
        </aside>
      </div>
    </section>
  );
}
