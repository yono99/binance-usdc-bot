import { cls, f } from "../api";
import { AccountPanel } from "../components/AccountPanel";
import { BotStatus } from "../components/BotStatus";
import { EquityChart } from "../components/EquityChart";
import { PositionsPanel } from "../components/PositionsPanel";
import { StatsCards } from "../components/StatsCards";
import { useDashboard } from "../layout/DashboardContext";

export function OverviewPage() {
  const { stats, status, account, orders, isLive, refreshAll } = useDashboard();
  const dayPnl = (status?.day_pnl_usdt ?? 0) + (status?.day_pnl_usdc ?? 0);
  const openN = status?.open_count ?? 0;
  const maxOpen = status?.max_open ?? 0;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Overview</h1>
          <p>Saldo, posisi, metrik paper — satu layar operasi harian.</p>
        </div>
      </div>

      <div className="cards">
        <div className="card">
          <div className="lbl">Open</div>
          <div className="val mono">
            {openN}
            <span className="muted" style={{ fontSize: 13, fontWeight: 500 }}>
              {" "}
              / {maxOpen || "—"}
            </span>
          </div>
        </div>
        <div className="card">
          <div className="lbl">Day PnL</div>
          <div className={`val mono ${cls(dayPnl)}`}>
            {(dayPnl > 0 ? "+" : "") + f(dayPnl, 2)}
          </div>
        </div>
        <div className="card">
          <div className="lbl">Circuit</div>
          <div className={`val`} style={{ fontSize: 14, fontWeight: 600 }}>
            {status?.circuit_breaker ? (
              <span className="neg">{status.circuit_breaker}</span>
            ) : status?.drawdown?.locked ? (
              <span className="neg">DD lock</span>
            ) : (
              <span className="pos">clear</span>
            )}
          </div>
        </div>
        <div className="card">
          <div className="lbl">News</div>
          <div className="val" style={{ fontSize: 14, fontWeight: 600 }}>
            {status?.news_veto?.active ? (
              <span className="neg">veto</span>
            ) : (
              <span className="muted">ok</span>
            )}
          </div>
        </div>
      </div>

      <AccountPanel acct={account} />
      <PositionsPanel
        status={status}
        orders={orders}
        isLive={isLive}
        onAction={refreshAll}
      />
      {stats && <StatsCards s={stats} />}
      {stats && <EquityChart s={stats} />}
      <BotStatus status={status} onAction={refreshAll} />
    </div>
  );
}
