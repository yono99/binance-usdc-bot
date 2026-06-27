import { useEffect, useRef, useState } from "react";
import {
  createChart,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { api } from "../api";
import type { Ohlcv, Status } from "../types";
import { SearchSelect } from "./SearchSelect";

const TFS = ["5m", "15m", "1h", "4h"];
const baseOpts = {
  layout: { background: { color: "transparent" }, textColor: "#8aa0c0" },
  grid: { vertLines: { color: "#243049" }, horzLines: { color: "#243049" } },
  rightPriceScale: { borderColor: "#243049" },
  timeScale: { borderColor: "#243049", timeVisible: true },
};

export function PriceChart({ status, available }: { status: Status | null; available: string[] }) {
  const active = status?.symbols?.map((x) => x.symbol) ?? [];
  const symbols = [...new Set([...active, ...available])];
  const [sym, setSym] = useState("");
  const [tf, setTf] = useState("15m");
  const [data, setData] = useState<Ohlcv | null>(null);

  const pxRef = useRef<HTMLDivElement>(null);
  const rsiRef = useRef<HTMLDivElement>(null);
  const pxChart = useRef<IChartApi | null>(null);
  const rsiChart = useRef<IChartApi | null>(null);
  const candle = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const emaSeries = useRef<ISeriesApi<"Line">[]>([]);
  const priceLines = useRef<ReturnType<ISeriesApi<"Candlestick">["createPriceLine"]>[]>([]);
  const rsiSeries = useRef<ISeriesApi<"Line"> | null>(null);

  useEffect(() => {
    if (symbols.length && !sym) setSym(symbols[0]);
  }, [symbols, sym]);

  // init charts sekali
  useEffect(() => {
    if (!pxRef.current || !rsiRef.current) return;
    const px = createChart(pxRef.current, { height: 320, ...baseOpts });
    const rsi = createChart(rsiRef.current, {
      height: 110,
      ...baseOpts,
      timeScale: { ...baseOpts.timeScale, visible: false },
    });
    candle.current = px.addCandlestickSeries({
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderUpColor: "#22c55e",
      borderDownColor: "#ef4444",
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    });
    rsiSeries.current = rsi.addLineSeries({ color: "#06b6d4", lineWidth: 1 });
    rsiSeries.current.createPriceLine({ price: 70, color: "#ef4444", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: "" });
    rsiSeries.current.createPriceLine({ price: 30, color: "#22c55e", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: "" });
    rsi.priceScale("right").applyOptions({ autoScale: false });
    rsiSeries.current.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) });
    pxChart.current = px;
    rsiChart.current = rsi;
    const onResize = () => {
      px.applyOptions({ width: pxRef.current!.clientWidth });
      rsi.applyOptions({ width: rsiRef.current!.clientWidth });
    };
    onResize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      px.remove();
      rsi.remove();
    };
  }, []);

  // fetch saat sym/tf berubah + refresh tiap 30 dtk
  useEffect(() => {
    if (!sym) return;
    let alive = true;
    const fetchData = () => api.ohlcv(sym, tf).then((d) => alive && setData(d));
    fetchData();
    const id = setInterval(fetchData, 30000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [sym, tf]);

  // render data ke chart
  useEffect(() => {
    if (!data || !candle.current || !rsiSeries.current) return;
    if (!data.bars?.length) return;
    const t = (x: number) => (x / 1000) as UTCTimestamp;
    candle.current.setData(data.bars.map((b) => ({ time: t(b.x), open: b.o, high: b.h, low: b.l, close: b.c })));

    // EMA overlays
    emaSeries.current.forEach((s) => pxChart.current!.removeSeries(s));
    emaSeries.current = [];
    const emaDefs: [number[] | undefined, string][] = [
      [data.ema_fast, "#eab308"],
      [data.ema_mid, "#3b82f6"],
      [data.ema_slow, "#a855f7"],
    ];
    for (const [arr, color] of emaDefs) {
      if (!arr) continue;
      const ls = pxChart.current!.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      ls.setData(
        arr
          .map((y, i) => ({ time: t(data.bars[i].x), value: y }))
          .filter((p) => Number.isFinite(p.value))
      );
      emaSeries.current.push(ls);
    }

    // garis entry/SL/TP/LIQ bila ada posisi pada simbol ini
    priceLines.current.forEach((pl) => candle.current!.removePriceLine(pl));
    priceLines.current = [];
    const sm = status?.symbols?.find((x) => x.symbol === sym && x.in_position);
    if (sm?.position) {
      const p = sm.position;
      const add = (price: number, color: string, title: string, dash = false) =>
        priceLines.current.push(
          candle.current!.createPriceLine({ price, color, lineWidth: 1, lineStyle: dash ? LineStyle.Dashed : LineStyle.Solid, axisLabelVisible: true, title })
        );
      add(p.entry, "#94a3b8", "entry", true);
      add(p.sl, "#ef4444", "SL");
      add(p.tp, "#22c55e", "TP");
      add(p.liq, "#b91c1c", "LIQ", true);
    }

    if (data.rsi) rsiSeries.current.setData(data.rsi.map((y, i) => ({ time: t(data.bars[i].x), value: y })));
    pxChart.current!.timeScale().fitContent();
    rsiChart.current!.timeScale().fitContent();
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  const cap = data?.periods;
  return (
    <div className="panel">
      <h2>Chart Harga per Pair</h2>
      <div style={{ marginBottom: 10, display: "flex", gap: 8, alignItems: "flex-start" }}>
        <SearchSelect value={sym} options={symbols} onChange={setSym} placeholder="cari pair…" />
        <select value={tf} onChange={(e) => setTf(e.target.value)} style={{ minWidth: 80 }}>
          {TFS.map((x) => (
            <option key={x}>{x}</option>
          ))}
        </select>
      </div>
      <div ref={pxRef} />
      {cap && (
        <div className="sub" style={{ marginTop: 6 }}>
          EMA{cap.fast} <span style={{ color: "#eab308" }}>━</span> EMA{cap.mid}{" "}
          <span style={{ color: "#3b82f6" }}>━</span> EMA{cap.slow} <span style={{ color: "#a855f7" }}>━</span> · RSI{cap.rsi}
        </div>
      )}
      <div ref={rsiRef} style={{ marginTop: 10 }} />
      {data?.error && <div className="danger">{data.error}</div>}
    </div>
  );
}
