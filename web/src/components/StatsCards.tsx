import type { ReactNode } from "react";
import { cls, f } from "../api";
import type { Stats } from "../types";

function Card({ lbl, val, c = "" }: { lbl: string; val: ReactNode; c?: string }) {
  return (
    <div className="card">
      <div className="lbl">{lbl}</div>
      <div className={`val ${c}`}>{val}</div>
    </div>
  );
}

export function StatsCards({ s }: { s: Stats }) {
  const pf = s.profit_factor == null ? "—" : s.profit_factor > 1e6 ? "∞" : f(s.profit_factor);
  return (
    <div className="cards">
      <Card lbl="Trades" val={s.trades} />
      <Card lbl="Liquidations" val={s.liquidations || 0} c={s.liquidations > 0 ? "neg" : ""} />
      <Card lbl="Win Rate" val={f(s.win_rate, 1) + "%"} />
      <Card
        lbl="Expectancy R"
        val={(s.expectancy_r > 0 ? "+" : "") + f(s.expectancy_r, 3)}
        c={cls(s.expectancy_r)}
      />
      <Card lbl="Profit Factor" val={pf} />
      <Card lbl="Equity" val={f(s.equity, 2)} />
      <Card
        lbl="Return"
        val={(s.return_pct > 0 ? "+" : "") + f(s.return_pct, 2) + "%"}
        c={cls(s.return_pct)}
      />
    </div>
  );
}
