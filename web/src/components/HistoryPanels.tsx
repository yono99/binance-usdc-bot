import { api, f } from "../api";
import { usePoll } from "../hooks";
import type { NewsLogEntry, ScreenLogEntry } from "../types";
import { type Col } from "./Table";
import { PaginatedTable } from "./PaginatedTable";

const ts = (s: string) => (s || "").slice(0, 19).replace("T", " ");

export function HistoryPanels() {
  const { data: news } = usePoll(() => api.newsLog(100), 30000);
  const { data: screen } = usePoll(() => api.screenLog(100), 30000);

  const newsCols: Col<NewsLogEntry>[] = [
    { t: "Waktu (UTC)", render: (r) => ts(r.ts) },
    {
      t: "Status",
      render: (r) => (r.active ? "⛔ VETO" : "clear"),
      cls: (r) => (r.active ? "neg" : "pos"),
    },
    { t: "Catatan", render: (r) => r.note || "—" },
  ];

  const screenCols: Col<ScreenLogEntry>[] = [
    { t: "Waktu (UTC)", render: (r) => ts(r.ts) },
    { t: "Pair", render: (r) => r.symbol },
    {
      t: "Sinyal",
      render: (r) => r.signal || "—",
      cls: (r) => (r.signal === "LONG" ? "pos" : r.signal === "SHORT" ? "neg" : ""),
    },
    { t: "Harga", render: (r) => f(r.price, 4) },
    { t: "ATR%", render: (r) => f(r.atr_pct, 2) },
    { t: "Alasan tak-entry", render: (r) => r.blocked || "—" },
  ];

  return (
    <>
      <div className="panel">
        <h2>Riwayat News Veto</h2>
        <PaginatedTable cols={newsCols} rows={news?.log ?? []} empty="Belum ada perubahan news veto tercatat" />
      </div>
      <div className="panel">
        <h2>Log Screening (per perubahan)</h2>
        <PaginatedTable cols={screenCols} rows={screen?.log ?? []} empty="Belum ada perubahan screening tercatat" />
      </div>
    </>
  );
}
