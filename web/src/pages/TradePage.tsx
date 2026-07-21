import { cls, f, fmtWIB } from "../api";
import { EntryConfluenceShadow } from "../components/EntryConfluenceShadow";
import { PaginatedTable } from "../components/PaginatedTable";
import { PositionsPanel } from "../components/PositionsPanel";
import { PriceChart } from "../components/PriceChart";
import { SetupPerformance } from "../components/SetupPerformance";
import { TradeHistory } from "../components/TradeHistory";
import { type Col } from "../components/Table";
import { useDashboard } from "../layout/DashboardContext";
import type { Stats } from "../types";

export function TradePage() {
  const { stats, status, available, orders, isLive, tick, refreshAll } = useDashboard();

  const symCols: Col<Stats["per_symbol"][number]>[] = [
    { t: "Simbol", render: (r) => r.symbol },
    { t: "Trades", render: (r) => r.trades },
    { t: "Win%", render: (r) => f(r.win_rate, 1) },
    {
      t: "Σ R",
      render: (r) => (r.sum_r > 0 ? "+" : "") + f(r.sum_r, 3),
      cls: (r) => cls(r.sum_r),
    },
  ];
  const recentCols: Col<Stats["recent"][number]>[] = [
    { t: "Waktu", render: (r) => fmtWIB(r.ts) },
    { t: "Simbol", render: (r) => r.symbol },
    {
      t: "Alasan",
      render: (r) => (r.reason === "liq" ? "LIKUIDASI" : r.reason),
    },
    {
      t: "R",
      render: (r) => (r.r > 0 ? "+" : "") + f(r.r, 3),
      cls: (r) => cls(r.r),
    },
    { t: "Equity", render: (r) => f(r.equity, 2) },
  ];

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Trade</h1>
          <p>Chart, posisi, setup, riwayat eksekusi.</p>
        </div>
      </div>

      <PositionsPanel
        status={status}
        orders={orders}
        isLive={isLive}
        onAction={refreshAll}
      />
      <PriceChart status={status} available={available} />
      <div className="row2">
        <SetupPerformance />
        <EntryConfluenceShadow />
      </div>
      {stats && (
        <div className="row2">
          <div className="panel">
            <h2>Per simbol</h2>
            <PaginatedTable cols={symCols} rows={stats.per_symbol} />
          </div>
          <div className="panel">
            <h2>Close terakhir</h2>
            <PaginatedTable
              cols={recentCols}
              rows={stats.recent}
              rowCls={(r) => (r.reason === "liq" ? "liqrow" : "")}
            />
          </div>
        </div>
      )}
      <TradeHistory tick={tick} />
    </div>
  );
}
