import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  BarChart3,
  Bot,
  CandlestickChart,
  ChevronLeft,
  ChevronRight,
  Database,
  Eye,
  EyeOff,
  Grid2X2,
  Maximize2,
  RefreshCcw,
  Workflow
} from "lucide-react";
import {
  createStrategyDefinition,
  deleteStrategyDefinition,
  exportStrategyDefinition,
  getBacktestJob,
  getBacktestOptions,
  getRunGroupAnalytics,
  getRunGroups,
  getRunTrades,
  getRuns,
  getStrategyBuilderCatalog,
  getStrategyDefinitions,
  getTradeReview,
  launchBacktest,
  updateStrategyDefinition,
  validateStrategyDefinition
} from "./api";
import { AnalyticsDashboard } from "./components/AnalyticsDashboard";
import { DataManagement } from "./components/DataManagement";
import { RunLauncher } from "./components/RunLauncher";
import { StrategyBuilder } from "./components/StrategyBuilder";
import { TradingChart } from "./components/TradingChart";
import type {
  BacktestJob,
  BacktestLaunchPayload,
  BacktestOptions,
  RunAnalytics,
  RunGroupSummary,
  RunSummary,
  StrategyBuilderCatalog,
  StrategyDefinition,
  StrategyDefinitionPayload,
  StrategyValidationResult,
  TimeframeCode,
  TradeReview,
  TradeSummary
} from "./types";
import "./styles.css";

const timeframes: TimeframeCode[] = ["H4", "H1", "M30", "M15", "M5", "M1"];
type AppView = "review" | "analytics" | "runlab" | "builder" | "data";
type ChartMode = "single" | "grid";

function formatNumber(value: number | null | undefined, digits = 2): string {
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

function runLabel(run: RunSummary): string {
  return `${run.symbol_code} / ${run.source_name} | ${run.strategy_name} ${run.strategy_version} | ${formatDate(
    run.created_at
  )}`;
}

function tradeLabel(trade: TradeSummary, index: number): string {
  const side = trade.direction === "bullish" ? "LONG" : "SHORT";
  return `#${index + 1} ${side} ${formatDate(trade.entry_time)} | PnL ${formatNumber(trade.pnl)}`;
}

function draftStamp(): string {
  return new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
}

function ToggleButton({
  active,
  label,
  onClick
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button className={active ? "icon-button is-active" : "icon-button"} onClick={onClick} title={label} type="button">
      {active ? <Eye size={17} /> : <EyeOff size={17} />}
      <span>{label}</span>
    </button>
  );
}

function App() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runGroups, setRunGroups] = useState<RunGroupSummary[]>([]);
  const [trades, setTrades] = useState<TradeSummary[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string>("");
  const [selectedGroupId, setSelectedGroupId] = useState<string>("");
  const [selectedAnalyticsSymbols, setSelectedAnalyticsSymbols] = useState<string[]>([]);
  const [selectedTradeId, setSelectedTradeId] = useState<string>("");
  const [review, setReview] = useState<TradeReview | null>(null);
  const [analytics, setAnalytics] = useState<RunAnalytics | null>(null);
  const [backtestOptions, setBacktestOptions] = useState<BacktestOptions | null>(null);
  const [backtestOptionsLoading, setBacktestOptionsLoading] = useState(false);
  const [selectedLauncherAssets, setSelectedLauncherAssets] = useState<string[]>([]);
  const [launcherStrategy, setLauncherStrategy] = useState("configs/strategy_default.yaml");
  const [launcherFrom, setLauncherFrom] = useState("");
  const [launcherTo, setLauncherTo] = useState("");
  const [launcherLabel, setLauncherLabel] = useState("");
  const [backtestJob, setBacktestJob] = useState<BacktestJob | null>(null);
  const [launcherError, setLauncherError] = useState<string | null>(null);
  const [strategyCatalog, setStrategyCatalog] = useState<StrategyBuilderCatalog | null>(null);
  const [strategyDefinitions, setStrategyDefinitions] = useState<StrategyDefinition[]>([]);
  const [selectedStrategyDefinitionId, setSelectedStrategyDefinitionId] = useState("");
  const [selectedBuilderBlockId, setSelectedBuilderBlockId] = useState("");
  const [builderValidation, setBuilderValidation] = useState<StrategyValidationResult | null>(null);
  const [builderLoading, setBuilderLoading] = useState(false);
  const [builderError, setBuilderError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<AppView>("review");
  const [chartMode, setChartMode] = useState<ChartMode>("single");
  const [activeTimeframe, setActiveTimeframe] = useState<TimeframeCode>("M1");
  const [showFib, setShowFib] = useState(true);
  const [showRisk, setShowRisk] = useState(true);
  const [showEvents, setShowEvents] = useState(true);

  const selectedRun = useMemo(() => runs.find((run) => run.id === selectedRunId) ?? null, [runs, selectedRunId]);
  const selectedGroup = useMemo(
    () => runGroups.find((group) => group.group_id === selectedGroupId) ?? null,
    [runGroups, selectedGroupId]
  );
  const selectedStrategyDefinition = useMemo(
    () => strategyDefinitions.find((strategy) => strategy.id === selectedStrategyDefinitionId) ?? null,
    [strategyDefinitions, selectedStrategyDefinitionId]
  );
  const selectedTradeIndex = useMemo(
    () => trades.findIndex((trade) => trade.id === selectedTradeId),
    [trades, selectedTradeId]
  );

  const loadRuns = async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextRuns, nextGroups] = await Promise.all([getRuns(), getRunGroups()]);
      setRuns(nextRuns);
      setRunGroups(nextGroups);
      setSelectedRunId((current) => current || nextRuns[0]?.id || "");
      setSelectedGroupId((current) => current || nextGroups[0]?.group_id || nextRuns[0]?.id || "");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  };

  const loadBacktestOptions = async () => {
    setBacktestOptionsLoading(true);
    setLauncherError(null);
    try {
      const options = await getBacktestOptions();
      setBacktestOptions(options);
      setLauncherStrategy((current) => current || options.defaults.strategy_config);
      setLauncherFrom((current) => current || options.defaults.from || "");
      setLauncherTo((current) => current || options.defaults.to || "");
      setSelectedLauncherAssets((current) => current.length ? current : options.assets.slice(0, 4).map((asset) => `${asset.symbol_code}::${asset.source_name}`));
    } catch (exc) {
      setLauncherError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBacktestOptionsLoading(false);
    }
  };

  const loadStrategyBuilder = async () => {
    setBuilderLoading(true);
    setBuilderError(null);
    try {
      const [catalog, definitions] = await Promise.all([getStrategyBuilderCatalog(), getStrategyDefinitions()]);
      setStrategyCatalog(catalog);
      setStrategyDefinitions(definitions);
      setSelectedStrategyDefinitionId((current) => current || definitions[0]?.id || "");
      setSelectedBuilderBlockId((current) => {
        if (current) {
          return current;
        }
        return definitions[0]?.definition.blocks[0]?.id || "";
      });
    } catch (exc) {
      setBuilderError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBuilderLoading(false);
    }
  };

  const loadAnalytics = async (groupId: string = selectedGroupId, symbols: string[] = selectedAnalyticsSymbols) => {
    if (!groupId) {
      setAnalytics(null);
      return;
    }
    setAnalyticsLoading(true);
    setError(null);
    try {
      setAnalytics(await getRunGroupAnalytics(groupId, symbols));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setAnalyticsLoading(false);
    }
  };

  useEffect(() => {
    void loadRuns();
    void loadBacktestOptions();
    void loadStrategyBuilder();
  }, []);

  useEffect(() => {
    if (selectedStrategyDefinition && !selectedStrategyDefinition.definition.blocks.some((block) => block.id === selectedBuilderBlockId)) {
      setSelectedBuilderBlockId(selectedStrategyDefinition.definition.blocks[0]?.id || "");
    }
  }, [selectedStrategyDefinition, selectedBuilderBlockId]);

  useEffect(() => {
    if (!selectedRunId) {
      setTrades([]);
      setSelectedTradeId("");
      setAnalytics(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    getRunTrades(selectedRunId)
      .then((nextTrades) => {
        if (cancelled) {
          return;
        }
        setTrades(nextTrades);
        setSelectedTradeId(nextTrades[0]?.id || "");
      })
      .catch((exc) => {
        if (!cancelled) {
          setError(exc instanceof Error ? exc.message : String(exc));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedRunId]);

  useEffect(() => {
    if (!selectedGroupId) {
      setAnalytics(null);
      return;
    }
    void loadAnalytics(selectedGroupId, selectedAnalyticsSymbols);
  }, [selectedGroupId, selectedAnalyticsSymbols]);

  useEffect(() => {
    if (!selectedTradeId) {
      setReview(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    getTradeReview(selectedTradeId)
      .then((nextReview) => {
        if (!cancelled) {
          setReview(nextReview);
        }
      })
      .catch((exc) => {
        if (!cancelled) {
          setError(exc instanceof Error ? exc.message : String(exc));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedTradeId]);

  useEffect(() => {
    if (!backtestJob || !["queued", "running"].includes(backtestJob.status)) {
      return;
    }
    const handle = window.setInterval(() => {
      getBacktestJob(backtestJob.id)
        .then((nextJob) => {
          setBacktestJob(nextJob);
          if (!["queued", "running"].includes(nextJob.status)) {
            void loadRuns();
          }
        })
        .catch((exc) => setLauncherError(exc instanceof Error ? exc.message : String(exc)));
    }, 1800);
    return () => window.clearInterval(handle);
  }, [backtestJob]);

  const moveTrade = (delta: number) => {
    if (selectedTradeIndex < 0) {
      return;
    }
    const next = trades[selectedTradeIndex + delta];
    if (next) {
      setSelectedTradeId(next.id);
    }
  };

  const launchRun = async (payload: BacktestLaunchPayload) => {
    setLauncherError(null);
    try {
      const job = await launchBacktest(payload);
      setBacktestJob(job);
      setActiveView("runlab");
    } catch (exc) {
      setLauncherError(exc instanceof Error ? exc.message : String(exc));
    }
  };

  const createBuilderFromTemplate = async (templateId: string) => {
    setBuilderLoading(true);
    setBuilderError(null);
    setBuilderValidation(null);
    try {
      const template = strategyCatalog?.templates.find((item) => item.id === templateId);
      const stamp = draftStamp();
      const created = await createStrategyDefinition({
        name: `${template?.name ?? "Strategy Builder"} Draft`,
        version: `draft-${stamp}`,
        description: template?.description,
        template_id: templateId
      });
      await loadStrategyBuilder();
      setSelectedStrategyDefinitionId(created.id);
      setSelectedBuilderBlockId(created.definition.blocks[0]?.id || "");
      await loadBacktestOptions();
    } catch (exc) {
      setBuilderError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBuilderLoading(false);
    }
  };

  const duplicateBuilderStrategy = async (strategy: StrategyDefinition) => {
    setBuilderLoading(true);
    setBuilderError(null);
    setBuilderValidation(null);
    try {
      const stamp = draftStamp();
      const created = await createStrategyDefinition({
        name: `${strategy.name} Copy`,
        version: `draft-${stamp}`,
        description: strategy.description ?? undefined,
        definition: strategy.definition
      });
      await loadStrategyBuilder();
      setSelectedStrategyDefinitionId(created.id);
      setSelectedBuilderBlockId(created.definition.blocks[0]?.id || "");
      await loadBacktestOptions();
    } catch (exc) {
      setBuilderError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBuilderLoading(false);
    }
  };

  const saveBuilderStrategy = async (strategyId: string, definition: StrategyDefinitionPayload) => {
    setBuilderLoading(true);
    setBuilderError(null);
    setBuilderValidation(null);
    try {
      const updated = await updateStrategyDefinition(strategyId, { definition });
      setStrategyDefinitions((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      await loadBacktestOptions();
    } catch (exc) {
      setBuilderError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBuilderLoading(false);
    }
  };

  const validateBuilderStrategy = async (strategyId: string) => {
    setBuilderLoading(true);
    setBuilderError(null);
    try {
      setBuilderValidation(await validateStrategyDefinition(strategyId));
      await loadStrategyBuilder();
      await loadBacktestOptions();
    } catch (exc) {
      setBuilderError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBuilderLoading(false);
    }
  };

  const exportBuilderStrategy = async (strategyId: string) => {
    setBuilderLoading(true);
    setBuilderError(null);
    try {
      await exportStrategyDefinition(strategyId);
      await loadStrategyBuilder();
      await loadBacktestOptions();
    } catch (exc) {
      setBuilderError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBuilderLoading(false);
    }
  };

  const deleteBuilderStrategy = async (strategyId: string) => {
    setBuilderLoading(true);
    setBuilderError(null);
    setBuilderValidation(null);
    try {
      await deleteStrategyDefinition(strategyId);
      setStrategyDefinitions((current) => current.filter((item) => item.id !== strategyId));
      if (selectedStrategyDefinitionId === strategyId) {
        const remaining = strategyDefinitions.filter((item) => item.id !== strategyId);
        const next = remaining[0] ?? null;
        setSelectedStrategyDefinitionId(next?.id ?? "");
        setSelectedBuilderBlockId(next?.definition.blocks[0]?.id ?? "");
      }
      await loadStrategyBuilder();
      await loadBacktestOptions();
    } catch (exc) {
      setBuilderError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBuilderLoading(false);
    }
  };

  const useBuilderInRunLab = (strategy: StrategyDefinition) => {
    setLauncherStrategy(`builder:${strategy.id}`);
    setActiveView("runlab");
  };

  const toggleLauncherAsset = (key: string) => {
    setSelectedLauncherAssets((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key]
    );
  };

  const selectAllLauncherAssets = () => {
    setSelectedLauncherAssets((backtestOptions?.assets ?? []).map((asset) => `${asset.symbol_code}::${asset.source_name}`));
  };

  const toggleAnalyticsSymbol = (symbol: string) => {
    const availableSymbols = analytics?.available_symbols ?? selectedGroup?.symbols ?? [];
    setSelectedAnalyticsSymbols((current) => {
      if (current.length === 0 || current.length === availableSymbols.length) {
        return [symbol];
      }
      return current.includes(symbol) ? current.filter((item) => item !== symbol) : [...current, symbol];
    });
  };

  return (
    <main className="app-shell">
      <aside className="left-rail">
        <div className="brand-row">
          <Activity size={22} />
          <div>
            <strong>ICT Trading Lab</strong>
            <span>Backtest Review</span>
          </div>
        </div>

        <nav className="view-tabs" aria-label="Application views">
          <button
            className={activeView === "runlab" ? "view-tab is-active" : "view-tab"}
            onClick={() => setActiveView("runlab")}
            type="button"
          >
            <Bot size={17} />
            <span>Run Lab</span>
          </button>
          <button
            className={activeView === "builder" ? "view-tab is-active" : "view-tab"}
            onClick={() => setActiveView("builder")}
            type="button"
          >
            <Workflow size={17} />
            <span>Builder</span>
          </button>
          <button
            className={activeView === "data" ? "view-tab is-active" : "view-tab"}
            onClick={() => setActiveView("data")}
            type="button"
          >
            <Database size={17} />
            <span>Data</span>
          </button>
          <button
            className={activeView === "review" ? "view-tab is-active" : "view-tab"}
            onClick={() => setActiveView("review")}
            type="button"
          >
            <CandlestickChart size={17} />
            <span>Review</span>
          </button>
          <button
            className={activeView === "analytics" ? "view-tab is-active" : "view-tab"}
            onClick={() => setActiveView("analytics")}
            type="button"
          >
            <BarChart3 size={17} />
            <span>Analytics</span>
          </button>
        </nav>

        <label className="field">
          <span>Run</span>
          <select value={selectedRunId} onChange={(event) => setSelectedRunId(event.target.value)}>
            {runs.map((run) => (
              <option value={run.id} key={run.id}>
                {runLabel(run)}
              </option>
            ))}
          </select>
        </label>

        <div className="run-stats">
          <div>
            <span>Trades</span>
            <strong>{formatNumber(selectedRun?.total_trades, 0)}</strong>
          </div>
          <div>
            <span>Net PnL</span>
            <strong>{formatNumber(selectedRun?.net_profit)}</strong>
          </div>
          <div>
            <span>Winrate</span>
            <strong>{formatNumber(selectedRun?.winrate ? selectedRun.winrate * 100 : null)}%</strong>
          </div>
        </div>

        <div className="trade-list-header">
          <span>Trades</span>
          <button className="small-button" onClick={() => void loadRuns()} title="Rafraichir" type="button">
            <RefreshCcw size={16} />
          </button>
        </div>
        <div className="trade-list">
          {trades.map((trade, index) => (
            <button
              className={trade.id === selectedTradeId ? "trade-row is-selected" : "trade-row"}
              key={trade.id}
              onClick={() => setSelectedTradeId(trade.id)}
              type="button"
            >
              <span>{tradeLabel(trade, index)}</span>
              <small>
                {trade.pd_type ?? "-"} | RR {formatNumber(trade.rr)}
              </small>
            </button>
          ))}
          {trades.length === 0 && <div className="empty-state">Aucun trade sur ce run.</div>}
        </div>
      </aside>

      <section className="workspace">
        {activeView === "review" && (
          <header className="top-bar">
            <div>
              <span className="eyebrow">{review ? `${review.symbol.code} / ${review.source.name}` : "Trading Lab"}</span>
              <h1>
                {review
                  ? `${review.trade.direction === "bullish" ? "LONG" : "SHORT"} ${formatDate(review.trade.entry_time)}`
                  : "Selectionne un trade"}
              </h1>
            </div>
            <div className="toolbar">
              <div className="timeframe-tabs" role="tablist" aria-label="Timeframes">
                {[...timeframes].reverse().map((timeframe) => (
                  <button
                    className={activeTimeframe === timeframe ? "timeframe-tab is-active" : "timeframe-tab"}
                    key={timeframe}
                    onClick={() => setActiveTimeframe(timeframe)}
                    type="button"
                  >
                    {timeframe}
                  </button>
                ))}
              </div>
              <button
                className={chartMode === "single" ? "icon-button is-active" : "icon-button"}
                onClick={() => setChartMode("single")}
                title="Mode un graphe"
                type="button"
              >
                <Maximize2 size={17} />
              </button>
              <button
                className={chartMode === "grid" ? "icon-button is-active" : "icon-button"}
                onClick={() => setChartMode("grid")}
                title="Mode grille"
                type="button"
              >
                <Grid2X2 size={17} />
              </button>
              <button
                className="icon-button"
                disabled={selectedTradeIndex <= 0}
                onClick={() => moveTrade(-1)}
                title="Trade precedent"
                type="button"
              >
                <ChevronLeft size={18} />
              </button>
              <button
                className="icon-button"
                disabled={selectedTradeIndex < 0 || selectedTradeIndex >= trades.length - 1}
                onClick={() => moveTrade(1)}
                title="Trade suivant"
                type="button"
              >
                <ChevronRight size={18} />
              </button>
              <ToggleButton active={showFib} label="Fibo" onClick={() => setShowFib((value) => !value)} />
              <ToggleButton active={showRisk} label="Risk" onClick={() => setShowRisk((value) => !value)} />
              <ToggleButton active={showEvents} label="Events" onClick={() => setShowEvents((value) => !value)} />
            </div>
          </header>
        )}

        {error && <div className="alert error">{error}</div>}
        {loading && activeView === "review" && <div className="alert">Chargement...</div>}
        {analyticsLoading && activeView === "analytics" && <div className="alert">Chargement analytics...</div>}
        {activeView === "review" && review?.quality.has_gaps && (
          <div className="alert warning">
            Gaps detectes: {review.quality.missing_m1_candles_across_windows.toLocaleString("fr-FR")} candles M1
            manquantes dans les fenetres affichees.
          </div>
        )}

        {activeView === "builder" ? (
          <StrategyBuilder
            catalog={strategyCatalog}
            error={builderError}
            loading={builderLoading}
            onCreateFromTemplate={createBuilderFromTemplate}
            onDuplicate={duplicateBuilderStrategy}
            onDelete={deleteBuilderStrategy}
            onExport={exportBuilderStrategy}
            onSave={saveBuilderStrategy}
            onSelect={setSelectedStrategyDefinitionId}
            onSelectBlock={setSelectedBuilderBlockId}
            onUseInRunLab={useBuilderInRunLab}
            onValidate={validateBuilderStrategy}
            selected={selectedStrategyDefinition}
            selectedBlockId={selectedBuilderBlockId}
            selectedId={selectedStrategyDefinitionId}
            strategies={strategyDefinitions}
            validation={builderValidation}
          />
        ) : activeView === "data" ? (
          <DataManagement />
        ) : activeView === "runlab" ? (
          <RunLauncher
            error={launcherError}
            fromDate={launcherFrom}
            job={backtestJob}
            label={launcherLabel}
            loading={backtestOptionsLoading}
            onClearAssets={() => setSelectedLauncherAssets([])}
            onFromDateChange={setLauncherFrom}
            onLabelChange={setLauncherLabel}
            onLaunch={launchRun}
            onRefreshOptions={() => void loadBacktestOptions()}
            onSelectAll={selectAllLauncherAssets}
            onStrategyChange={setLauncherStrategy}
            onToDateChange={setLauncherTo}
            onToggleAsset={toggleLauncherAsset}
            options={backtestOptions}
            selectedAssetKeys={selectedLauncherAssets}
            strategyConfig={launcherStrategy}
            toDate={launcherTo}
          />
        ) : activeView === "analytics" ? (
          <AnalyticsDashboard
            analytics={analytics}
            groups={runGroups}
            loading={analyticsLoading}
            onGroupChange={(groupId) => {
              setSelectedGroupId(groupId);
              setSelectedAnalyticsSymbols([]);
            }}
            onRefresh={() => void loadAnalytics()}
            onSelectAllSymbols={() => setSelectedAnalyticsSymbols([])}
            onToggleSymbol={toggleAnalyticsSymbol}
            selectedGroupId={selectedGroupId}
            selectedSymbols={selectedAnalyticsSymbols}
          />
        ) : review ? (
          <div className={chartMode === "single" ? "review-grid single-mode" : "review-grid"}>
            <section className={chartMode === "single" ? "single-chart" : "charts-grid"}>
              {chartMode === "single" ? (
                <TradingChart
                  annotations={review.annotations}
                  fib={review.fib}
                  markers={review.markers}
                  payload={review.timeframes[activeTimeframe]}
                  riskReward={review.risk_reward}
                  showEvents={showEvents}
                  showFib={showFib}
                  showRisk={showRisk}
                  size="large"
                  timeframe={activeTimeframe}
                />
              ) : (
                timeframes.map((timeframe) => (
                  <TradingChart
                    annotations={review.annotations}
                    fib={review.fib}
                    key={timeframe}
                    markers={review.markers}
                    payload={review.timeframes[timeframe]}
                    riskReward={review.risk_reward}
                    showEvents={showEvents}
                    showFib={showFib}
                    showRisk={showRisk}
                    timeframe={timeframe}
                  />
                ))
              )}
            </section>

            <aside className="detail-panel">
              <section>
                <h2>Trade</h2>
                <div className="detail-table">
                  <span>Entry</span>
                  <strong>{formatNumber(review.trade.entry_price, 5)}</strong>
                  <span>Exit</span>
                  <strong>{formatNumber(review.trade.exit_price, 5)}</strong>
                  <span>SL</span>
                  <strong>{formatNumber(review.trade.sl, 5)}</strong>
                  <span>TP</span>
                  <strong>{formatNumber(review.trade.tp, 5)}</strong>
                  <span>PnL</span>
                  <strong>{formatNumber(review.trade.pnl)}</strong>
                  <span>RR</span>
                  <strong>{formatNumber(review.trade.rr)}</strong>
                </div>
              </section>

              <section>
                <h2>Fibo</h2>
                {review.fib.available ? (
                  <>
                    <div className="source-pill">{review.fib.source}</div>
                    <div className="fib-list">
                      {review.fib.levels.map((level) => (
                        <div key={level.label}>
                          <span>{level.label}</span>
                          <strong>{formatNumber(level.price, 5)}</strong>
                        </div>
                      ))}
                    </div>
                  </>
                ) : (
                  <div className="empty-state">Fibo indisponible.</div>
                )}
              </section>

              <section>
                <h2>Events</h2>
                <div className="event-list">
                  {review.events
                    .filter((event) => event.setup_id !== "market")
                    .map((event) => (
                      <div className="event-row" key={event.id}>
                        <span>{formatDate(event.event_time)}</span>
                        <strong>{event.event_type.replace(/_/g, " ")}</strong>
                        <small>
                          {event.state_before ?? "-"} {" -> "} {event.state_after ?? "-"}
                        </small>
                      </div>
                    ))}
                </div>
              </section>
            </aside>
          </div>
        ) : (
          <div className="blank-slate">Aucun trade charge.</div>
        )}
      </section>
    </main>
  );
}

export default App;
