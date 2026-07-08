import { useEffect, useMemo, useRef, useState } from "react";
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  type CandlestickData,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
  createChart
} from "lightweight-charts";
import type { FibPayload, RiskRewardPayload, TimeframeCode, TimeframePayload, TradeAnnotations, TradeMarker } from "../types";

type OverlayShape = {
  top: number;
  left: number;
  width: number;
  height: number;
  className: string;
  label?: string;
};

type TradingChartProps = {
  payload: TimeframePayload;
  markers: TradeMarker[];
  annotations: TradeAnnotations;
  fib: FibPayload;
  riskReward: RiskRewardPayload;
  timeframe: TimeframeCode;
  showFib: boolean;
  showRisk: boolean;
  showEvents: boolean;
  size?: "normal" | "large";
};

const timeframeEventFilters: Record<TimeframeCode, Set<string>> = {
  H4: new Set(["H1_SIGNAL"]),
  H1: new Set(["H1_SIGNAL"]),
  M30: new Set(["H1_SIGNAL"]),
  M15: new Set(["H1_SIGNAL"]),
  M5: new Set(["PD_TOUCHED", "REJECTION_CONFIRMED", "RISK_REJECTED"]),
  M1: new Set([
    "PD_TOUCHED",
    "REJECTION_CONFIRMED",
    "RISK_REJECTED"
  ])
};

const eventLabels: Record<string, string> = {
  H1_SIGNAL: "H1",
  M15_DOUBLE_SWING_VALIDATED: "S2",
  LEG_FOUND: "LEG",
  OTE_CREATED: "OTE",
  OB_SELECTED: "OB",
  FVG_SELECTED: "FVG",
  PD_TOUCHED: "PD",
  REJECTION_CONFIRMED: "REJ",
  RISK_REJECTED: "RISK"
};

function toTimestamp(value: string | number): UTCTimestamp {
  if (typeof value === "number") {
    return value as UTCTimestamp;
  }
  return Math.floor(new Date(value).getTime() / 1000) as UTCTimestamp;
}

function markerFor(marker: TradeMarker, timeframe: TimeframeCode): SeriesMarker<Time> | null {
  const isLong = marker.direction === "bullish";
  if (marker.kind === "entry") {
    return {
      time: toTimestamp(marker.time),
      position: isLong ? "belowBar" : "aboveBar",
      color: isLong ? "#20c997" : "#ff6b6b",
      shape: isLong ? "arrowUp" : "arrowDown",
      text: isLong ? "LONG" : "SHORT"
    };
  }
  if (marker.kind === "exit") {
    return {
      time: toTimestamp(marker.time),
      position: isLong ? "aboveBar" : "belowBar",
      color: "#ffd166",
      shape: "circle",
      text: "EXIT"
    };
  }
  if (!marker.event_type) {
    return null;
  }
  if (!timeframeEventFilters[timeframe].has(marker.event_type)) {
    return null;
  }
  return {
    time: toTimestamp(marker.time),
    position: marker.direction === "bearish" ? "aboveBar" : "belowBar",
    color: "#86efac",
    shape: "square",
    text: eventLabels[marker.event_type] ?? marker.event_type.replace(/_/g, " ")
  };
}

export function TradingChart({
  payload,
  markers,
  annotations,
  fib,
  riskReward,
  timeframe,
  showFib,
  showRisk,
  showEvents,
  size = "normal"
}: TradingChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [overlays, setOverlays] = useState<OverlayShape[]>([]);
  const [chartError, setChartError] = useState<string | null>(null);

  const candleData = useMemo<CandlestickData<Time>[]>(
    () =>
      payload.candles.map((candle) => ({
        time: candle.time as UTCTimestamp,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close
      })),
    [payload.candles]
  );
  const fibAllowed = showFib && fib.available && (fib.visible_timeframes ?? ["M1"]).includes(timeframe);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }
    setChartError(null);
    setOverlays([]);
    let resizeObserver: ResizeObserver | null = null;
    const chart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight,
      layout: {
        background: { type: ColorType.Solid, color: "#101418" },
        textColor: "#cbd5df"
      },
      grid: {
        vertLines: { color: "rgba(148, 163, 184, 0.08)" },
        horzLines: { color: "rgba(148, 163, 184, 0.08)" }
      },
      crosshair: {
        mode: CrosshairMode.Normal
      },
      rightPriceScale: {
        borderColor: "rgba(148, 163, 184, 0.2)",
        scaleMargins: { top: 0.12, bottom: 0.18 }
      },
      timeScale: {
        borderColor: "rgba(148, 163, 184, 0.2)",
        timeVisible: true,
        secondsVisible: false
      }
    });

    try {
      const candleSeries = chart.addCandlestickSeries({
        upColor: "#2dd4bf",
        downColor: "#fb7185",
        borderUpColor: "#2dd4bf",
        borderDownColor: "#fb7185",
        wickUpColor: "#8be9dc",
        wickDownColor: "#ff8ea0"
      });
      candleSeries.setData(candleData);

      if (showEvents) {
        const swingMarkers: SeriesMarker<Time>[] = annotations.swings
          .filter((swing) => swing.visible_timeframes.includes(timeframe))
          .map((swing) => ({
            time: toTimestamp(swing.time),
            position: swing.direction === "bearish" ? "aboveBar" : "belowBar",
            color: swing.kind === "leg" ? "#67e8f9" : "#fbbf24",
            shape: swing.direction === "bearish" ? "arrowDown" : "arrowUp",
            text: swing.label
          }));
        const chartMarkers = [
          ...(markers.map((marker) => markerFor(marker, timeframe)).filter(Boolean) as SeriesMarker<Time>[]),
          ...swingMarkers
        ].sort(
          (left, right) => Number(left.time) - Number(right.time)
        );
        candleSeries.setMarkers(chartMarkers);
      }

      if (showRisk && riskReward) {
        candleSeries.createPriceLine({
          price: riskReward.entry,
          color: "#f8fafc",
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          axisLabelVisible: true,
          title: "ENTRY"
        });
        candleSeries.createPriceLine({
          price: riskReward.sl,
          color: "#fb7185",
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          axisLabelVisible: true,
          title: "SL"
        });
        candleSeries.createPriceLine({
          price: riskReward.tp,
          color: "#2dd4bf",
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          axisLabelVisible: true,
          title: "TP"
        });
        if (riskReward.exit !== null) {
          candleSeries.createPriceLine({
            price: riskReward.exit,
            color: "#ffd166",
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: "EXIT"
          });
        }
      }

      if (fibAllowed) {
        fib.levels.forEach((level) => {
          candleSeries.createPriceLine({
            price: level.price,
            color: level.level === 0.618 || level.level === 0.79 ? "#f59e0b" : "rgba(251, 191, 36, 0.62)",
            lineWidth: level.level === 0.618 || level.level === 0.79 ? 2 : 1,
            lineStyle: level.level === 0 || level.level === 1 ? LineStyle.Dashed : LineStyle.Dotted,
            axisLabelVisible: false,
            title: level.label
          });
        });
      }

      annotations.levels
        .filter((level) => level.visible_timeframes.includes(timeframe))
        .forEach((level) => {
          const isTarget = level.kind === "target";
          const isCandidate = level.kind === "target_candidate";
          candleSeries.createPriceLine({
            price: level.price,
            color: isTarget ? "#22d3ee" : isCandidate ? "rgba(148, 163, 184, 0.58)" : "rgba(125, 211, 252, 0.68)",
            lineWidth: isTarget ? 2 : 1,
            lineStyle: level.kind === "crt_mid" || isCandidate ? LineStyle.Dotted : LineStyle.Dashed,
            axisLabelVisible: isTarget,
            title: level.label
          });
        });

      chart.timeScale().fitContent();

      const renderOverlays = () => {
        const nextOverlays: OverlayShape[] = [];
        const addBand = (bottom: number, top: number, className: string) => {
          const topY = candleSeries.priceToCoordinate(top);
          const bottomY = candleSeries.priceToCoordinate(bottom);
          if (topY === null || bottomY === null) {
            return;
          }
          const y1 = Math.min(topY, bottomY);
          const y2 = Math.max(topY, bottomY);
          nextOverlays.push({ top: y1, left: 0, width: Math.max(1, container.clientWidth - 60), height: Math.max(1, y2 - y1), className });
        };
        const addZone = (
          bottom: number,
          top: number,
          startTime: string | number | null,
          endTime: string | number | null,
          className: string,
          label?: string
        ) => {
          if (!startTime || !endTime) {
            return;
          }
          const topY = candleSeries.priceToCoordinate(top);
          const bottomY = candleSeries.priceToCoordinate(bottom);
          const startX = chart.timeScale().timeToCoordinate(toTimestamp(startTime));
          const endX = chart.timeScale().timeToCoordinate(toTimestamp(endTime));
          if (topY === null || bottomY === null || startX === null || endX === null) {
            return;
          }
          const y1 = Math.min(topY, bottomY);
          const y2 = Math.max(topY, bottomY);
          const x1 = Math.min(startX, endX);
          const x2 = Math.max(startX, endX);
          nextOverlays.push({
            top: y1,
            left: x1,
            width: Math.max(8, x2 - x1),
            height: Math.max(6, y2 - y1),
            className,
            label
          });
        };
        if (showRisk && riskReward) {
          const entryTime = markers.find((marker) => marker.kind === "entry")?.time ?? payload.candles[0]?.time ?? null;
          const exitTime =
            markers.find((marker) => marker.kind === "exit")?.time ??
            payload.candles[payload.candles.length - 1]?.time ??
            null;
          addZone(riskReward.risk_zone.bottom, riskReward.risk_zone.top, entryTime, exitTime, "timed-zone risk-zone", "Risk");
          addZone(riskReward.reward_zone.bottom, riskReward.reward_zone.top, entryTime, exitTime, "timed-zone reward-zone", "Reward");
        }
        if (fibAllowed && fib.ote_zone) {
          addBand(fib.ote_zone.bottom, fib.ote_zone.top, "zone ote-zone");
        }
        if (showEvents) {
          annotations.zones
            .filter((zone) => zone.visible_timeframes.includes(timeframe))
            .forEach((zone) => {
              addZone(
                zone.bottom,
                zone.top,
                zone.start_time,
                zone.end_time,
                `timed-zone ${zone.kind.toLowerCase()}-zone-box`,
                zone.label
              );
            });
        }
        setOverlays(nextOverlays);
      };

      resizeObserver = new ResizeObserver(() => {
        chart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
        renderOverlays();
      });
      resizeObserver.observe(container);
      const scheduleOverlayRender = () => {
        requestAnimationFrame(renderOverlays);
      };
      chart.timeScale().subscribeVisibleLogicalRangeChange(scheduleOverlayRender);
      chart.timeScale().subscribeVisibleTimeRangeChange(scheduleOverlayRender);
      requestAnimationFrame(renderOverlays);

      return () => {
        chart.timeScale().unsubscribeVisibleLogicalRangeChange(scheduleOverlayRender);
        chart.timeScale().unsubscribeVisibleTimeRangeChange(scheduleOverlayRender);
        resizeObserver?.disconnect();
        chart.remove();
      };
    } catch (exc) {
      setChartError(exc instanceof Error ? exc.message : String(exc));
    }

    return () => {
      resizeObserver?.disconnect();
      chart.remove();
    };
  }, [annotations, candleData, fib, fibAllowed, markers, payload.candles, riskReward, showEvents, showRisk, timeframe]);

  return (
    <section className={size === "large" ? "chart-panel is-large" : "chart-panel"}>
      <header className="chart-panel-header">
        <span>{timeframe}</span>
        <small>
          {payload.candles.length.toLocaleString("fr-FR")} candles
          {payload.gap_summary.gap_count > 0 ? ` | ${payload.gap_summary.missing_candles} M1 manquantes` : ""}
        </small>
      </header>
      <div className="chart-canvas-shell">
        {chartError && <div className="chart-error">{chartError}</div>}
        <div className="chart-zones" aria-hidden="true">
          {overlays.map((band, index) => (
            <div
              className={band.className}
              key={`${band.className}-${index}`}
              style={{ top: `${band.top}px`, left: `${band.left}px`, width: `${band.width}px`, height: `${band.height}px` }}
            >
              {band.label ? <span>{band.label}</span> : null}
            </div>
          ))}
        </div>
        <div className="chart-canvas" ref={containerRef} />
      </div>
    </section>
  );
}
