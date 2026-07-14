import { useEffect, useState } from "react";
import { api, cls, f, fp, fmtWIB } from "../api";
import type { Trade, TradesResp } from "../types";
import { type Col } from "./Table";
import { PaginatedTable } from "./PaginatedTable";

const REASONS = ["tp", "sl", "liq", "manual", "eod"];

export function TradeHistory({ tick }: { tick: number }) {
  const [fsym, setFsym] = useState("");
  const [freason, setFreason] = useState("");
  const [ffrom, setFfrom] = useState("");
  const [fto, setFto] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(5);
  const [data, setData] = useState<TradesResp>({ count: 0, trades: [], total_pages: 1 });

  const query = () => {
    const p = new URLSearchParams();
    if (fsym) p.set("symbol", fsym);
    if (freason) p.set("reason", freason);
    if (ffrom) p.set("dfrom", ffrom);
    if (fto) p.set("dto", fto);
    p.set("page", String(page));
    p.set("page_size", "100"); // fetch up to 100, PaginatedTable will slice to 5 per page
    return p.toString();
  };

  const load = () => api.trades(query()).then(setData);

  // refresh saat tick global (auto-refresh) berubah
  useEffect(() => {
    load();
  }, [tick]); // eslint-disable-line react-hooks/exhaustive-deps

  // reset ke halaman 1 saat filter berubah
  useEffect(() => {
    setPage(1);
  }, [fsym, freason, ffrom, fto]);

  const del = async (id: number) => {
    if (!confirm("Hapus trade ini dari riwayat?")) return;
    await api.deleteTrade(id);
    load();
  };
  const clearAll = async () => {
    if (!confirm("Hapus SELURUH riwayat trade? Tidak bisa dibatalkan.")) return;
    await api.clearTrades();
    load();
  };

  const cols: Col<Trade>[] = [
    { t: "Waktu (WIB)", render: (r) => fmtWIB(r.close_ts) },
    { t: "Pair", render: (r) => r.symbol },
    { t: "Side", render: (r) => (r.side || "").toUpperCase(), cls: (r) => (r.side === "long" ? "pos" : r.side === "short" ? "neg" : "") },
    { t: "Reason", render: (r) => (r.reason === "liq" ? "⚠ LIQ" : r.reason || "—") },
    { t: "R", render: (r) => (r.r != null ? (r.r > 0 ? "+" : "") + f(r.r, 3) : "—"), cls: (r) => cls(r.r) },
    { t: "PnL$", render: (r) => (r.pnl_usd != null ? (r.pnl_usd >= 0 ? "+" : "") + f(r.pnl_usd, 2) : "—"), cls: (r) => (r.pnl_usd == null ? "" : r.pnl_usd >= 0 ? "pos" : "neg") },
    { t: "Entry", render: (r) => fp(r.entry) },
    { t: "Exit", render: (r) => fp(r.exit) },
    { t: "Equity", render: (r) => f(r.equity, 2) },
    { t: "", render: (r) => (r.id != null ? <button className="del" title="Hapus trade ini" onClick={() => del(r.id!)}>✕</button> : "") },
  ];

  // gunakan data.trades yg sudah di-fetch (max 100) — PaginatedTable akan slice per 5
  const displayRows = data.trades;

  return (
    <div className="panel">
      <h2>
        Riwayat Trade
        <a href={api.csvHref(query())} style={{ float: "right", fontSize: 13 }}>
          ⬇ Export CSV
        </a>
      </h2>
      <div className="grid" style={{ marginBottom: 12 }}>
        <label>
          Pair
          <input value={fsym} onChange={(e) => setFsym(e.target.value)} placeholder="mis. BTC" />
        </label>
        <label>
          Reason
          <select value={freason} onChange={(e) => setFreason(e.target.value)}>
            <option value="">semua</option>
            {REASONS.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        </label>
        <label>
          Dari
          <input type="date" value={ffrom} onChange={(e) => setFfrom(e.target.value)} />
        </label>
        <label>
          Sampai
          <input type="date" value={fto} onChange={(e) => setFto(e.target.value)} />
        </label>
      </div>
      <button onClick={load}>Filter</button>{" "}
      <button className="danger" onClick={clearAll}>
        Hapus semua
      </button>{" "}
      <span className="sub">{data.count} trade</span>
      <div style={{ marginTop: 10 }}>
        <PaginatedTable
          cols={cols}
          rows={displayRows}
          rowCls={(r) => (r.reason === "liq" ? "liqrow" : "")}
          initialSize={5}
        />
      </div>
    </div>
  );
}
