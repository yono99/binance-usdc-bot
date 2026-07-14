import { useState } from "react";
import { api, f, fp } from "../api";
import type { OpenOrder, Status } from "../types";
import { Table, type Col } from "./Table";

type Row = NonNullable<Status["symbols"]>[number];

export function PositionsPanel({
  status,
  orders,
  isLive,
  onAction,
}: {
  status: Status | null;
  orders: OpenOrder[];
  isLive: boolean;
  onAction: () => void;
}) {
  const [tab, setTab] = useState<"pos" | "orders">("pos");
  const [busy, setBusy] = useState<string | null>(null);
  const open = (status?.symbols || []).filter((s) => s.in_position && s.position);

  const close = async (sym: string) => {
    if (!confirm(`Tutup posisi ${sym}? (diproses ≤1 siklus)`)) return;
    await api.close(sym);
    onAction();
  };
  const closeAll = async () => {
    if (!open.length) return;
    if (!confirm(`Tutup SEMUA ${open.length} posisi? (diproses ≤1 siklus)`)) return;
    await api.closeAll();
    onAction();
  };
  const cancel = async (sym: string, oid?: string) => {
    if (!oid) return;
    if (!confirm(`Batalkan order ${sym} (${oid})?`)) return;
    setBusy(oid);
    try {
      const r = await api.cancelOrder(sym, oid);
      if (!r.ok) alert(r.error || "Gagal membatalkan order");
    } finally {
      setBusy(null);
      onAction();
    }
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
    { t: "Entry", render: (r) => fp(r.position!.entry) },
    { t: "Mark", render: (r) => fp(r.position!.mark ?? r.price) },
    {
      t: "SL",
      render: (r) => <span className="neg">{fp(r.position!.sl)}</span>,
    },
    {
      t: "TP",
      render: (r) => <span className="pos">{fp(r.position!.tp)}</span>,
    },
    { t: "Liq", render: (r) => <span className="neg">{fp(r.position!.liq)}</span> },
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

  // Open Orders: data NYATA dari Binance (live) atau pending engine (dry).
  // - LIMIT entry resting (reduce_only=false): oranye
  // - SL/TP STOP_MARKET/TAKE_PROFIT_MARKET (reduce_only=true): merah/hijau
  const ordCols: Col<OpenOrder>[] = [
    { t: "Pair", render: (r) => <b>{r.symbol}</b> },
    {
      t: "Tipe",
      render: (r) => {
        const t = r.type.toUpperCase();
        if (t.includes("TAKE_PROFIT")) return "TAKE PROFIT";
        if (t.includes("STOP")) return "STOP (SL)";
        if (t === "LIMIT") return "LIMIT (entry)";
        return t;
      },
      cls: (r) => {
        const t = r.type.toUpperCase();
        if (t.includes("TAKE_PROFIT")) return "pos";
        if (t.includes("STOP")) return "neg";
        return "";
      },
    },
    { t: "Arah", render: (r) => r.side },
    { t: "Harga", render: (r) => fp(r.price) },
    { t: "Qty", render: (r) => f(r.qty, 4) },
    { t: "Status", render: (r) => (r.status ? r.status : "—") },
    {
      t: "",
      render: (r) =>
        isLive && r.order_id ? (
          <button
            className="btnsm"
            disabled={busy === r.order_id}
            onClick={() => cancel(r.symbol, r.order_id)}
          >
            {busy === r.order_id ? "…" : "Cancel"}
          </button>
        ) : null,
    },
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
        {tab === "pos" && open.length > 0 && (
          <button className="btnsm" style={{ marginLeft: "auto" }} onClick={closeAll}>
            Close All ({open.length})
          </button>
        )}
      </div>
      {tab === "pos" ? (
        <Table cols={posCols} rows={open} empty="Belum ada posisi terbuka." />
      ) : (
        <Table
          cols={ordCols}
          rows={orders}
          empty={
            isLive
              ? "Tidak ada order aktif di Binance."
              : "Tidak ada order pending (dry/paper)."
          }
        />
      )}
    </div>
  );
}
