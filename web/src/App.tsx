import { useEffect, useState } from "react";
import { api, cls, f } from "./api";
import { usePoll } from "./hooks";
import type { Stats } from "./types";
import { StatsCards } from "./components/StatsCards";
import { ControlPanel } from "./components/ControlPanel";
import { AccountPanel } from "./components/AccountPanel";
import { BotStatus } from "./components/BotStatus";
import { PriceChart } from "./components/PriceChart";
import { EquityChart } from "./components/EquityChart";
import { TradeHistory } from "./components/TradeHistory";
import { HistoryPanels } from "./components/HistoryPanels";
import { GeminiUsage } from "./components/GeminiUsage";
import { PositionsPanel } from "./components/PositionsPanel";
import { Table, type Col } from "./components/Table";

export default function App() {
  const { data: stats, refetch: refetchStats } = usePoll(api.stats, 10000);
  const { data: status, refetch: refetchStatus } = usePoll(api.status, 10000);
  const { data: account } = usePoll(api.account, 10000);
  const { data: symbolsResp } = usePoll(api.symbols, 600000);
  const available = symbolsResp?.symbols ?? [];
  const [tick, setTick] = useState(0);
  const [updated, setUpdated] = useState("");

  useEffect(() => {
    const id = setInterval(() => {
      setTick((t) => t + 1);
      setUpdated(new Date().toLocaleTimeString());
    }, 10000);
    return () => clearInterval(id);
  }, []);

  const refreshAll = () => {
    refetchStats();
    refetchStatus();
  };

  return (
    <>
      <header>
        <div>
          <h1>Bot Monitor</h1>
          <div className="sub">Forward-test (paper) · data live · React/Vite</div>
        </div>
        <div className="sub">
          <span className="dot" />
          <span>{updated ? `diperbarui ${updated}` : "memuat…"}</span>
        </div>
      </header>
      <div className="wrap">
        <ControlPanel status={status} available={available} account={account} />
        <AccountPanel acct={account} />
        <GeminiUsage />
        <BotStatus status={status} onAction={refreshAll} />
        <PositionsPanel status={status} onAction={refreshAll} />
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
        <Table cols={symCols} rows={s.per_symbol} />
      </div>
      <div className="panel">
        <h2>Trade Terakhir</h2>
        <Table cols={recentCols} rows={s.recent} rowCls={(r) => (r.reason === "liq" ? "liqrow" : "")} />
      </div>
    </>
  );
}
