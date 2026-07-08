import { useMemo, useState } from "react";
import { AlertTriangle, BrainCircuit, RefreshCcw, Square, SquareCheckBig } from "lucide-react";
import type { AnalyticsBreakdownRow, AnalyticsPoint, RunAnalytics, RunGroupSummary } from "../types";

type ChartPoint = {
  label: string;
  value: number;
};

type AnalyticsDashboardProps = {
  analytics: RunAnalytics | null;
  loading: boolean;
  groups: RunGroupSummary[];
  selectedGroupId: string;
  selectedSymbols: string[];
  onRefresh: () => void;
  onGroupChange: (groupId: string) => void;
  onToggleSymbol: (symbol: string) => void;
  onSelectAllSymbols: () => void;
};

const breakdownLabels: Array<{ key: keyof RunAnalytics["breakdowns"]; label: string }> = [
  { key: "symbol", label: "Asset" },
  { key: "target_source", label: "Target" },
  { key: "pd_type", label: "PD" },
  { key: "session", label: "Session" },
  { key: "direction", label: "Direction" },
  { key: "hour_of_day", label: "Hour" },
  { key: "exit_reason", label: "Exit" },
  { key: "day_of_week", label: "Day" }
];

function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return value.toLocaleString("fr-FR", { maximumFractionDigits: digits });
}

function formatPct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return `${formatNumber(value * 100, 1)}%`;
}

function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("fr-FR", { month: "short", day: "2-digit" }).format(new Date(value));
}

function KpiCard({
  label,
  value,
  detail,
  tone = "neutral"
}: {
  label: string;
  value: string;
  detail: string;
  tone?: "neutral" | "good" | "bad";
}) {
  return (
    <div className={`kpi-card ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function LineChart({ title, data, valueLabel }: { title: string; data: ChartPoint[]; valueLabel: string }) {
  const width = 720;
  const height = 240;
  const padding = { top: 18, right: 20, bottom: 34, left: 56 };
  const values = data.map((point) => point.value).filter((value) => Number.isFinite(value));
  const minValue = values.length ? Math.min(...values) : 0;
  const maxValue = values.length ? Math.max(...values) : 1;
  const spread = maxValue - minValue || Math.max(1, Math.abs(maxValue) || 1);
  const yMin = minValue - spread * 0.08;
  const yMax = maxValue + spread * 0.08;
  const xFor = (index: number) =>
    padding.left + (index / Math.max(1, data.length - 1)) * (width - padding.left - padding.right);
  const yFor = (value: number) =>
    padding.top + ((yMax - value) / Math.max(1e-9, yMax - yMin)) * (height - padding.top - padding.bottom);
  const points = data.map((point, index) => `${xFor(index)},${yFor(point.value)}`).join(" ");
  const zeroY = yMin < 0 && yMax > 0 ? yFor(0) : null;

  return (
    <section className="analysis-panel wide">
      <header className="analysis-panel-header">
        <span>{title}</span>
        <small>{valueLabel}</small>
      </header>
      {data.length < 2 ? (
        <div className="empty-state">Pas assez de points pour tracer la courbe.</div>
      ) : (
        <svg className="line-chart" viewBox={`0 0 ${width} ${height}`} role="img">
          <line x1={padding.left} x2={width - padding.right} y1={padding.top} y2={padding.top} />
          <line x1={padding.left} x2={width - padding.right} y1={height - padding.bottom} y2={height - padding.bottom} />
          {zeroY !== null && <line className="zero-line" x1={padding.left} x2={width - padding.right} y1={zeroY} y2={zeroY} />}
          <polyline points={points} />
          <text x={padding.left} y={height - 10}>
            {data[0]?.label}
          </text>
          <text x={width - padding.right} y={height - 10} textAnchor="end">
            {data[data.length - 1]?.label}
          </text>
          <text x={10} y={padding.top + 4}>
            {formatNumber(maxValue)}
          </text>
          <text x={10} y={height - padding.bottom + 4}>
            {formatNumber(minValue)}
          </text>
        </svg>
      )}
    </section>
  );
}

function MonthlyBars({ data }: { data: RunAnalytics["monthly"] }) {
  const maxAbs = Math.max(1, ...data.map((row) => Math.abs(row.pnl)));
  return (
    <section className="analysis-panel">
      <header className="analysis-panel-header">
        <span>PnL monthly</span>
        <small>{data.length} months</small>
      </header>
      <div className="monthly-bars">
        {data.map((row) => {
          const height = Math.max(3, (Math.abs(row.pnl) / maxAbs) * 100);
          return (
            <div className="month-bar" key={row.month}>
              <span>{formatNumber(row.pnl, 0)}</span>
              <div className={row.pnl >= 0 ? "bar-positive" : "bar-negative"} style={{ height: `${height}%` }} />
              <small>{row.month}</small>
            </div>
          );
        })}
        {data.length === 0 && <div className="empty-state">Pas encore de donnees mensuelles.</div>}
      </div>
    </section>
  );
}

function BreakdownContent({ rows }: { rows: AnalyticsBreakdownRow[] }) {
  const maxAbs = Math.max(1, ...rows.map((row) => Math.abs(row.pnl)));
  return (
    <div className="breakdown-table">
      {rows.slice(0, 8).map((row) => (
        <div className="breakdown-row" key={row.name}>
          <div>
            <strong>{row.name}</strong>
            <small>
              {row.trades} trades | WR {formatPct(row.winrate)} | RR {formatNumber(row.avg_rr)}
            </small>
          </div>
          <div className="breakdown-track">
            <span
              className={row.pnl >= 0 ? "breakdown-fill positive" : "breakdown-fill negative"}
              style={{ width: `${Math.max(4, (Math.abs(row.pnl) / maxAbs) * 100)}%` }}
            />
          </div>
          <strong className={row.pnl >= 0 ? "positive-text" : "negative-text"}>{formatNumber(row.pnl)}</strong>
        </div>
      ))}
      {rows.length === 0 && <div className="empty-state">Aucun breakdown disponible.</div>}
    </div>
  );
}

function Distribution({ analytics }: { analytics: RunAnalytics }) {
  const maxTrades = Math.max(1, ...analytics.rr_distribution.map((row) => row.trades));
  return (
    <section className="analysis-panel">
      <header className="analysis-panel-header">
        <span>RR distribution</span>
        <small>bucket par R</small>
      </header>
      <div className="distribution-list">
        {analytics.rr_distribution.map((row) => (
          <div className="distribution-row" key={row.bucket}>
            <span>{row.bucket}</span>
            <div>
              <i style={{ width: `${Math.max(3, (row.trades / maxTrades) * 100)}%` }} />
            </div>
            <strong>{row.trades}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}

function EventFunnel({ analytics }: { analytics: RunAnalytics }) {
  const rows = analytics.event_funnel.filter((row) => row.count > 0).slice(0, 10);
  const maxCount = Math.max(1, ...rows.map((row) => row.count));
  return (
    <section className="analysis-panel">
      <header className="analysis-panel-header">
        <span>Setup funnel</span>
        <small>events</small>
      </header>
      <div className="funnel-list">
        {rows.map((row) => (
          <div className="funnel-row" key={row.event_type}>
            <span>{row.event_type.replace(/_/g, " ")}</span>
            <div>
              <i style={{ width: `${Math.max(4, (row.count / maxCount) * 100)}%` }} />
            </div>
            <strong>{row.count}</strong>
          </div>
        ))}
        {rows.length === 0 && <div className="empty-state">Aucun event de setup.</div>}
      </div>
    </section>
  );
}

function Diagnostics({ analytics }: { analytics: RunAnalytics }) {
  return (
    <section className="analysis-panel diagnostics-panel">
      <header className="analysis-panel-header">
        <span>Diagnostics</span>
        <small>rule-based v1</small>
      </header>
      <div className="diagnostic-list">
        {analytics.diagnostics.map((item) => (
          <div className={`diagnostic-row ${item.severity}`} key={`${item.title}-${item.detail}`}>
            {item.severity === "critical" ? <AlertTriangle size={17} /> : <BrainCircuit size={17} />}
            <div>
              <strong>{item.title}</strong>
              <small>{item.detail}</small>
            </div>
          </div>
        ))}
        {analytics.diagnostics.length === 0 && (
          <div className="diagnostic-row info">
            <BrainCircuit size={17} />
            <div>
              <strong>Pas de faille evidente</strong>
              <small>Les heuristiques v1 ne detectent pas de segment faible majeur sur ce run.</small>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

function SymbolComparison({ analytics }: { analytics: RunAnalytics }) {
  const rows = analytics.comparisons.symbols.slice(0, 12);
  return (
    <section className="analysis-panel wide">
      <header className="analysis-panel-header">
        <span>Asset comparison</span>
        <small>{rows.length} assets</small>
      </header>
      <BreakdownContent rows={rows} />
    </section>
  );
}

function equityPoints(points: AnalyticsPoint[]): ChartPoint[] {
  return points
    .filter((point) => point.equity !== null && point.equity !== undefined)
    .map((point) => ({ label: formatDate(point.time), value: Number(point.equity) }));
}

function cumulativePoints(points: AnalyticsPoint[]): ChartPoint[] {
  return points
    .filter((point) => point.value !== null && point.value !== undefined)
    .map((point) => ({ label: formatDate(point.time), value: Number(point.value) }));
}

export function AnalyticsDashboard({
  analytics,
  loading,
  groups,
  selectedGroupId,
  selectedSymbols,
  onRefresh,
  onGroupChange,
  onToggleSymbol,
  onSelectAllSymbols
}: AnalyticsDashboardProps) {
  const [breakdownKey, setBreakdownKey] = useState<keyof RunAnalytics["breakdowns"]>("pd_type");
  const equity = useMemo(() => equityPoints(analytics?.equity_curve ?? []), [analytics]);
  const cumulative = useMemo(() => cumulativePoints(analytics?.cumulative_pnl ?? []), [analytics]);

  if (!analytics) {
    return (
      <section className="analytics-dashboard">
        <div className="analytics-controls">
          <label className="field">
            <span>Run</span>
            <select value={selectedGroupId} onChange={(event) => onGroupChange(event.target.value)}>
              {groups.map((group) => (
                <option value={group.group_id} key={group.group_id}>
                  {group.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="blank-slate">Aucune analyse chargee pour ce run.</div>
      </section>
    );
  }

  const summary = analytics.summary;
  const availableSymbols = analytics.available_symbols ?? [analytics.run.symbol_code].filter(Boolean);
  const groupLabel = analytics.group?.label ?? analytics.run.parameter_set_name;
  const isAllSymbols = selectedSymbols.length === 0 || selectedSymbols.length === availableSymbols.length;
  return (
    <section className="analytics-dashboard">
      <header className="analytics-header">
        <div>
          <span className="eyebrow">
            {groupLabel} | {analytics.run.source_name}
          </span>
          <h1>
            {analytics.run.strategy_name} {analytics.run.strategy_version}
          </h1>
        </div>
        <button className="icon-button" disabled={loading} onClick={onRefresh} title="Rafraichir l'analyse" type="button">
          <RefreshCcw size={17} />
          <span>Refresh</span>
        </button>
      </header>

      <div className="analytics-controls">
        <label className="field">
          <span>Run</span>
          <select value={selectedGroupId} onChange={(event) => onGroupChange(event.target.value)}>
            {groups.map((group) => (
              <option value={group.group_id} key={group.group_id}>
                {group.label}
              </option>
            ))}
          </select>
        </label>
        <div className="symbol-filter">
          <button className={isAllSymbols ? "asset-chip is-active" : "asset-chip"} onClick={onSelectAllSymbols} type="button">
            <SquareCheckBig size={15} />
            All
          </button>
          {availableSymbols.map((symbol) => {
            const active = isAllSymbols || selectedSymbols.includes(symbol);
            return (
              <button className={active ? "asset-chip is-active" : "asset-chip"} key={symbol} onClick={() => onToggleSymbol(symbol)} type="button">
                {active ? <SquareCheckBig size={15} /> : <Square size={15} />}
                {symbol}
              </button>
            );
          })}
        </div>
      </div>

      <div className="kpi-grid">
        <KpiCard label="Net PnL" value={formatNumber(summary.net_pnl, 5)} detail={`${summary.total_trades} trades`} tone={summary.net_pnl >= 0 ? "good" : "bad"} />
        <KpiCard label="Winrate" value={formatPct(summary.winrate)} detail={`${summary.wins}W / ${summary.losses}L`} />
        <KpiCard label="Avg RR" value={formatNumber(summary.avg_rr, 4)} detail={`Median ${formatNumber(summary.median_rr, 4)}`} />
        <KpiCard label="Profit factor" value={formatNumber(summary.profit_factor)} detail={`Expectancy ${formatNumber(summary.expectancy, 5)}`} />
        <KpiCard label="Worst trade" value={formatNumber(summary.worst_trade, 5)} detail={`Best ${formatNumber(summary.best_trade, 5)}`} tone="bad" />
        <KpiCard label="Loss streak" value={formatNumber(summary.max_consecutive_losses, 0)} detail={`Avg duration ${formatNumber(summary.avg_duration_minutes, 0)} min`} />
      </div>

      <div className="analytics-grid">
        <LineChart title="Equity curve" data={equity} valueLabel="balance/equity" />
        <LineChart title="Cumulative PnL" data={cumulative} valueLabel="trade by trade" />
        <MonthlyBars data={analytics.monthly} />
        <Distribution analytics={analytics} />
        {analytics.comparisons.symbols.length > 1 && <SymbolComparison analytics={analytics} />}
        <section className="analysis-panel wide">
          <header className="analysis-panel-header">
            <span>Strategy slices</span>
            <div className="mini-tabs">
              {breakdownLabels.map((item) => (
                <button
                  className={breakdownKey === item.key ? "mini-tab is-active" : "mini-tab"}
                  key={item.key}
                  onClick={() => setBreakdownKey(item.key)}
                  type="button"
                >
                  {item.label}
                </button>
              ))}
            </div>
          </header>
          <BreakdownContent rows={analytics.breakdowns[breakdownKey]} />
        </section>
        <EventFunnel analytics={analytics} />
        <Diagnostics analytics={analytics} />
      </div>
    </section>
  );
}
