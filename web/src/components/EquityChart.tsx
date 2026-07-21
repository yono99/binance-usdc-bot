import { useEffect, useRef } from "react";
import { createChart, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import type { Stats } from "../types";

export function EquityChart({ s }: { s: Stats }) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Area"> | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const c = createChart(ref.current, {
      height: 220,
      layout: { background: { color: "transparent" }, textColor: "#8aa0c0" },
      grid: { vertLines: { color: "#243049" }, horzLines: { color: "#243049" } },
      rightPriceScale: { borderColor: "#243049" },
      timeScale: { visible: false, borderColor: "#243049" },
      handleScroll: false,
      handleScale: false,
    });
    series.current = c.addAreaSeries({
      lineColor: "#6366f1",
      topColor: "rgba(99,102,241,.25)",
      bottomColor: "rgba(99,102,241,.02)",
      lineWidth: 2,
    });
    chart.current = c;
    const onResize = () => c.applyOptions({ width: ref.current!.clientWidth });
    onResize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      c.remove();
    };
  }, []);

  useEffect(() => {
    if (!series.current || !chart.current) return;
    const data = s.equity_curve.map((v, i) => ({ time: i as UTCTimestamp, value: v }));
    series.current.setData(data);
    const liq = new Set(s.liq_points || []);
    series.current.setMarkers(
      [...liq].map((i) => ({
        time: i as UTCTimestamp,
        position: "inBar" as const,
        color: "#ef4444",
        shape: "circle" as const,
        text: "LIQ",
      }))
    );
    chart.current.timeScale().fitContent();
  }, [s.equity_curve, s.liq_points]);

  return (
    <div className="panel">
      <h2>Equity</h2>
      <div ref={ref} />
    </div>
  );
}
