import { useEffect, useState } from "react";
import { api, cls, f, fp, fmtWIB } from "../api";
import type { Trade, TradesResp } from "../types";
import { type Col } from "./Table";
import { Table } from "./Table";
import { Pager } from "./Pager";

const REASONS = ["tp", "sl", "liq", "manual", "eod"];

export function TradeHistory({ tick }: { tick: number }) {
  const [fsym, setFsym] = useState("");
  const [freason, setFreason] = useState("");
  const [ffrom, setFfrom] = useState("");
  const [fto, setFto] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(5);
  const [data, setData] = useState<TradesResp>({
    total: 0,
    page: 1,
    page_size: 5,
    max_page: 1,
    total_pages: 1,
    trades: [],
  });

  const buildQuery = (p: number = page, ps: number = pageSize) => {
    const q = new URLSearchParams();
    if (fsym) q.set("symbol", fsym);
    if (freason) q.set("reason", freason);
    if (ffrom) q.set("dfrom", ffrom);
    if (fto) q.set("dto", fto);
    q.set("page", String(p));
    q.set("page_size", String(ps));
    return q.toString();
  };

  const load = (p: number = page, ps: number = pageSize) =>
    api.trades(buildQuery(p, ps)).then(setData);

  useEffect(() => {
    load();
  }, [tick]);

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

  const onPage = (p: number) => {
    setPage(p);
    load(p, pageSize);
  };
  const onSize = (s: number) => {
    setPageSize(s);
    setPage(1);
    load(1, s);
  };

  const maxPage = data.max_page || 1;
  const cur = Math.min(Math.max(1, page), maxPage);
  const from = data.total === 0 ? 0 : (cur - 1) * data.page_size + 1;
  const to = Math.min(cur * data.page_size, data.total);

  return (
    <div className="panel">
      <h2>
        Riwayat Trade
        <a href={api.csvHref(buildQuery(cur, pageSize))} style={{ float: "right", fontSize: 13 }}>
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
      <button onClick={() => load()}>Filter</button>{" "}
      <button className="danger" onClick={clearAll}>
        Hapus semua
      </button>{" "}
      <span className="sub">
        {from > 0 ? `${from}–${to}` : "0"} dari {data.total} trade · hal {cur}/{maxPage}
      </span>
      <div style={{ marginTop: 10 }}>
        <Table
          cols={cols}
          rows={data.trades}
          rowCls={(r) => (r.reason === "liq" ? "liqrow" : "")}
          empty="Tidak ada trade."
        />
        {data.total > data.page_size ? (
          <Pager total={data.total} page={cur} size={data.page_size} onPage={onPage} onSize={onSize} />
        ) : null}
      </div>
    </div>
  );
}
