import { useState } from "react";
import { api, f, fp } from "../api";
import type { Status } from "../types";
import { Table, type Col } from "./Table";
import { Pager } from "./Pager";

export function BotStatus({ status, onAction }: { status: Status | null; onAction: () => void }) {
  const [msg, setMsg] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(10);
  if (!status || !status.ts)
    return (
      <div className="panel">
        <h2>Status Bot</h2>
        <div className="empty">Bot belum jalan — `python forwardtest.py --poll 30 --use-store`</div>
      </div>
    );

  const nv = status.news_veto?.active ? (
    <span className="neg">VETO ({status.news_veto.note})</span>
  ) : (
    <span className="pos">clear</span>
  );

  const flash = (t: string) => {
    setMsg(t);
    setTimeout(() => setMsg(""), 5000);
  };
  const closePos = async (sym: string) => {
    if (!confirm(`Tutup paksa posisi ${sym}? (diproses ≤1 siklus)`)) return;
    await api.close(sym);
    flash(`Permintaan tutup ${sym} dikirim — diproses ≤1 siklus (${status.poll_seconds ?? 30}s).`);
    onAction();
  };
  const closeAll = async () => {
    if (!status.open_count) {
      flash("Tidak ada posisi terbuka untuk ditutup.");
      return;
    }
    if (!confirm("Tutup SEMUA posisi? (diproses ≤1 siklus)")) return;
    await api.closeAll();
    flash(`Permintaan tutup semua (${status.open_count}) dikirim — diproses ≤1 siklus.`);
    onAction();
  };

  type Row = NonNullable<Status["symbols"]>[number];
  const cols: Col<Row>[] = [
    { t: "Pair", render: (r) => r.symbol },
    { t: "Harga", render: (r) => fp(r.price) },
    { t: "ATR%", render: (r) => f(r.atr_pct, 2) },
    {
      t: "Sinyal",
      render: (r) => r.signal || "—",
      cls: (r) => (r.signal === "LONG" ? "pos" : r.signal === "SHORT" ? "neg" : ""),
    },
    {
      t: "Posisi (PnL)",
      render: (r) =>
        r.in_position && r.position
          ? `${r.position.side.toUpperCase()} ${(r.position.pnl_usd >= 0 ? "+" : "") + f(r.position.pnl_usd, 2)}`
          : "—",
      cls: (r) => (r.in_position && r.position ? (r.position.pnl_usd >= 0 ? "pos" : "neg") : ""),
    },
    { t: "Alasan tak-entry", render: (r) => r.blocked || "—" },
    {
      t: "",
      render: (r) =>
        r.in_position ? (
          <button className="btnsm" onClick={() => closePos(r.symbol)}>
            Close
          </button>
        ) : (
          ""
        ),
    },
  ];

  return (
    <>
      <div className="panel">
        <h2>Status Bot</h2>
        <div className="line">
          Status: {status.enabled ? <span className="pos">ON</span> : <span className="neg">OFF</span>} · Teknik:{" "}
          <b>{status.technique}</b> · TF: {status.timeframe} · Leverage: <b>{status.leverage}x</b> · Bet: $
          {f(status.bet_usd, 2)} · Saldo: <b>${f(status.balance_usd, 2)}</b> · Posisi: {status.open_count}/
          {status.max_open} · Order: <b>{status.order_type || "—"}</b> (fee {f(status.fee_pct, 3)}%) · News: {nv} ·{" "}
          <span className="sub">update {(status.ts || "").slice(11, 19)} UTC</span>
        </div>
        <div className="line" style={{ marginTop: 4 }}>
          PnL hari ini:{" "}
          <b className={status.day_pnl != null ? (status.day_pnl >= 0 ? "pos" : "neg") : ""}>
            {status.day_pnl != null ? (status.day_pnl >= 0 ? "+" : "") + f(status.day_pnl, 2) : "—"}
          </b>{" "}
          · Trade hari ini: <b>{status.day_trades ?? 0}</b> · Guard korelasi: ≥{f(status.corr_threshold, 2)} ·{" "}
          {status.circuit_breaker ? (
            <span className="neg">⛔ {status.circuit_breaker}</span>
          ) : (
            <span className="pos">circuit breaker: clear</span>
          )}
        </div>
      </div>
      <div className="panel">
        <h2>
          Aktivitas per Pair — screening &amp; sinyal
          <button
            className="btnsm"
            style={{ float: "right" }}
            onClick={closeAll}
            disabled={!status.open_count}
            title={status.open_count ? "Tutup semua posisi terbuka" : "Tidak ada posisi terbuka"}
          >
            Close All ({status.open_count ?? 0})
          </button>
        </h2>
        <div className="sub" style={{ marginBottom: 8 }}>
          Daftar <b>pantau (screening)</b> — bukan posisi terbuka. Sinyal &amp; ATR terisi saat bar baru
          tertutup, tiap siklus screening (~{Math.round((status.poll_seconds ?? 900) / 60)} mnt).
          {status.gemini_decide_budget != null && (
            <>
              {" "}Budget Gemini: <b>{status.gemini_decide_budget}</b>/siklus
              {status.gemini_decide_cap != null && status.gemini_decide_budget >= status.gemini_decide_cap ? (
                <span className="sub"> (mentok cap {status.gemini_decide_cap})</span>
              ) : (
                status.gemini_decide_cap != null && <span className="sub"> (cap {status.gemini_decide_cap})</span>
              )}
              .
            </>
          )}
        </div>
        {msg && <div className="ok">{msg}</div>}
        {(() => {
          const all = status.symbols || [];
          const pages = Math.max(1, Math.ceil(all.length / size));
          const cur = Math.min(page, pages);
          const rows = all.slice((cur - 1) * size, cur * size);
          return (
            <>
              <Table cols={cols} rows={rows} />
              {all.length > 10 && (
                <Pager total={all.length} page={page} size={size} onPage={setPage} onSize={setSize} />
              )}
            </>
          );
        })()}
      </div>
    </>
  );
}
