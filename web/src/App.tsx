import { useCallback, useEffect, useRef, useState } from "react";
import { api, cls, f } from "./api";
import { useEventSource, usePoll } from "./hooks";
import type { Account, OpenOrder, Stats, Status } from "./types";
import { StatsCards } from "./components/StatsCards";
import { ControlPanel } from "./components/ControlPanel";
import { AgentControls } from "./components/AgentControls";
import { AccountPanel } from "./components/AccountPanel";
import { BotStatus } from "./components/BotStatus";
import { PriceChart } from "./components/PriceChart";
import { EquityChart } from "./components/EquityChart";
import { TradeHistory } from "./components/TradeHistory";
import { HistoryPanels } from "./components/HistoryPanels";
import { GeminiUsage } from "./components/GeminiUsage";
import { GeminiTraderPanel } from "./components/GeminiTraderPanel";
import { PositionsPanel } from "./components/PositionsPanel";
import { type Col } from "./components/Table";
import { PaginatedTable } from "./components/PaginatedTable";

export default function App() {
  // ===== SSE: satu koneksi multiplex untuk stats/status/trades =====
  const { status: sseStatus, subscribe } = useEventSource("/api/stream");

  const [stats, setStats] = useState<Stats | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [updated, setUpdated] = useState("");

  // ===== polling fallback untuk data exchange-backed (jarang berubah) =====
  const { data: account } = usePoll(api.account, 10000);
  const { data: symbolsResp } = usePoll(api.symbols, 600000);
  const { data: ordersResp, refetch: refetchOrders } = usePoll(api.openOrders, 10000);
  const available = symbolsResp?.symbols ?? [];
  const [tick, setTick] = useState(0);

  // subscribe SSE — sekali saat mount, handler stabil via ref
  useEffect(() => {
    // snapshot awal: backend kirim {status, stats, mode} saat connect
    const unsubs = [
      subscribe("snapshot", (e) => {
        const snap = e.data as { status?: Status; stats?: Stats; mode?: string };
        if (snap.status) setStatus(snap.status);
        if (snap.stats) setStats(snap.stats);
        setUpdated(new Date().toLocaleTimeString());
      }),
      subscribe("status", (e) => {
        setStatus(e.data as Status);
        setUpdated(new Date().toLocaleTimeString());
      }),
      subscribe("stats", (e) => {
        setStats(e.data as Stats);
        setUpdated(new Date().toLocaleTimeString());
      }),
      subscribe("trade", () => {
        // trade baru → refetch stats (exp_R/win berubah) + bump tick (trade history)
        setTick((t) => t + 1);
        api.stats().then(setStats).catch(() => {});
        setUpdated(new Date().toLocaleTimeString());
      }),
      subscribe("ping", () => {/* keep-alive, no-op */}),
    ];
    return () => unsubs.forEach((u) => u());
  }, [subscribe]);

  // tick untuk komponen yang masih poll internal (TradeHistory, dll)
  useEffect(() => {
    const id = setInterval(() => {
      setTick((t) => t + 1);
      setUpdated(new Date().toLocaleTimeString());
    }, 10000);
    return () => clearInterval(id);
  }, []);

  const refreshAll = useCallback(() => {
    // action user (close/cancel) → refetch immediate
    api.stats().then(setStats).catch(() => {});
    api.status().then(setStatus).catch(() => {});
    refetchOrders();
  }, [refetchOrders]);

  const isLive = (account?.mode === "live" || status?.mode === "live") ?? false;

  const sseLabel = sseStatus === "open" ? "● live" : sseStatus === "connecting" ? "● menyambung" : "● putus";

  return (
    <>
      <header>
        <div>
          <h1>Bot Monitor</h1>
          <div className="sub">Forward-test (paper) · data live · React/Vite</div>
        </div>
        <div className="sub">
          <span className={`dot ${sseStatus === "open" ? "" : "off"}`} />
          <span>{updated ? `${sseLabel} · ${updated}` : `${sseLabel} · memuat…`}</span>
        </div>
      </header>
      <div className="wrap">
        <ControlPanel status={status} available={available} account={account} />
        <AgentControls />
        <AccountPanel acct={account} />
        <GeminiUsage />
        <BotStatus status={status} onAction={refreshAll} />
        <GeminiTraderPanel />
        <PositionsPanel status={status} orders={ordersResp?.orders ?? []} isLive={isLive} onAction={refreshAll} />
        <PriceChart status={status} available={available} />
        {stats && <StatsCards s={stats} />}
        {stats && <EquityChart s={stats} />}
        {stats && <StatsTables s={stats} />}
        <TradeHistory tick={tick} />
        <HistoryPanels />
      </div>
    </>
  );
}

function StatsTables({ s }: { s: Stats }) {
  const symCols: Col<Stats["per_symbol"][number]>[] = [
    { t: "Simbol", render: (r) => r.symbol },
    { t: "Trades", render: (r) => r.trades },
    { t: "Win%", render: (r) => f(r.win_rate, 1) },
    { t: "Σ R", render: (r) => (r.sum_r > 0 ? "+" : "") + f(r.sum_r, 3), cls: (r) => cls(r.sum_r) },
  ];
  const recentCols: Col<Stats["recent"][number]>[] = [
    { t: "Waktu", render: (r) => (r.ts || "").slice(11, 19) },
    { t: "Simbol", render: (r) => r.symbol },
    { t: "Alasan", render: (r) => (r.reason === "liq" ? "⚠ LIKUIDASI" : r.reason) },
    { t: "R", render: (r) => (r.r > 0 ? "+" : "") + f(r.r, 3), cls: (r) => cls(r.r) },
    { t: "Equity", render: (r) => f(r.equity, 2) },
  ];
  return (
    <>
      <div className="panel">
        <h2>Per Simbol</h2>
        <PaginatedTable cols={symCols} rows={s.per_symbol} />
      </div>
      <div className="panel">
        <h2>Trade Terakhir</h2>
        <PaginatedTable cols={recentCols} rows={s.recent} rowCls={(r) => (r.reason === "liq" ? "liqrow" : "")} />
      </div>
    </>
  );
}
