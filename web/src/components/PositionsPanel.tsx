import { useState } from "react";
import { api, f } from "../api";
import type { Status } from "../types";
import { Table, type Col } from "./Table";

type Row = NonNullable<Status["symbols"]>[number];

export function PositionsPanel({ status, onAction }: { status: Status | null; onAction: () => void }) {
  const [tab, setTab] = useState<"pos" | "orders">("pos");
  const open = (status?.symbols || []).filter((s) => s.in_position && s.position);

  const close = async (sym: string) => {
    if (!confirm(`Tutup posisi ${sym}? (diproses ≤1 siklus)`)) return;
    await api.close(sym);
    onAction();
  };

  const posCols: Col<Row>[] = [
    { t: "Pair", render: (r) => <b>{r.symbol}</b> },
    {
      t: "Arah",
      render: (r) => (r.position!.side === "long" ? "LONG" : "SHORT"),
      cls: (r) => (r.position!.side === "long" ? "pos" : "neg"),
    },
    { t: "Qty", render: (r) => f(r.position!.qty, 4) },
    { t: "Margin", render: (r) => "$" + f(r.position!.bet, 2) },
    { t: "Entry", render: (r) => f(r.position!.entry, 4) },
    { t: "Mark", render: (r) => f(r.position!.mark ?? r.price, 4) },
    { t: "Liq", render: (r) => <span className="neg">{f(r.position!.liq, 4)}</span> },
    {
      t: "PnL (ROI)",
      render: (r) => {
        const p = r.position!;
        const sign = p.pnl_usd >= 0 ? "+" : "";
        return `${sign}$${f(p.pnl_usd, 2)} (${sign}${f(p.roi_pct, 1)}%)`;
      },
      cls: (r) => (r.position!.pnl_usd >= 0 ? "pos" : "neg"),
    },
    {
      t: "",
      render: (r) => (
        <button className="btnsm" onClick={() => close(r.symbol)}>
          Close
        </button>
      ),
    },
  ];

  // Open Orders (paper): SL & TP sebagai conditional order per posisi.
  type Ord = { symbol: string; type: string; side: string; trigger: number; qty: number; cls: string };
  const orders: Ord[] = [];
  for (const r of open) {
    const p = r.position!;
    const closeSide = p.side === "long" ? "SELL" : "BUY";
    orders.push({ symbol: r.symbol, type: "STOP (SL)", side: closeSide, trigger: p.sl, qty: p.qty ?? 0, cls: "neg" });
    orders.push({ symbol: r.symbol, type: "TAKE PROFIT (TP)", side: closeSide, trigger: p.tp, qty: p.qty ?? 0, cls: "pos" });
  }
  const ordCols: Col<Ord>[] = [
    { t: "Pair", render: (r) => <b>{r.symbol}</b> },
    { t: "Tipe", render: (r) => r.type, cls: (r) => r.cls },
    { t: "Arah", render: (r) => r.side },
    { t: "Harga trigger", render: (r) => f(r.trigger, 4) },
    { t: "Qty", render: (r) => f(r.qty, 4) },
  ];

  return (
    <div className="panel">
      <div className="postabs">
        <button className={tab === "pos" ? "active" : ""} onClick={() => setTab("pos")}>
          Positions ({open.length})
        </button>
        <button className={tab === "orders" ? "active" : ""} onClick={() => setTab("orders")}>
          Open Orders ({orders.length})
        </button>
      </div>
      {tab === "pos" ? (
        <Table cols={posCols} rows={open} empty="Belum ada posisi terbuka." />
      ) : (
        <Table cols={ordCols} rows={orders} empty="Tidak ada order aktif." />
      )}
    </div>
  );
}
