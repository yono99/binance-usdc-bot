import { useMemo, useState } from "react";
import { api, f } from "../api";
import { usePoll } from "../hooks";
import type { NewsLogEntry, ScreenLogEntry } from "../types";
import { type Col } from "./Table";
import { PaginatedTable } from "./PaginatedTable";

// Timestamp disimpan UTC (ISO). Tampilkan dalam zona pilihan (default WIB).
const TZ: Record<string, string> = {
  WIB: "Asia/Jakarta", // UTC+7
  WITA: "Asia/Makassar", // UTC+8
  WIT: "Asia/Jayapura", // UTC+9
  UTC: "UTC",
};
const fmtTime = (s: string, tz: string): string => {
  if (!s) return "—";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s.slice(0, 19).replace("T", " ");
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: TZ[tz] || "Asia/Jakarta",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(d).replace(",", "");
};

export function HistoryPanels() {
  const { data: news } = usePoll(() => api.newsLog(100), 30000);
  const { data: screen } = usePoll(() => api.screenLog(100), 30000);

  // filter + urut news veto
  const [newsStatus, setNewsStatus] = useState("");
  const [newsSort, setNewsSort] = useState("new");
  // filter + urut screening
  const [scPair, setScPair] = useState("");
  const [scSignal, setScSignal] = useState("");
  const [scReason, setScReason] = useState("");
  const [scSort, setScSort] = useState("new");
  // zona waktu tampilan (default WIB), persisten
  const [tz, setTz] = useState(() => localStorage.getItem("tz") || "WIB");
  const changeTz = (v: string) => {
    setTz(v);
    localStorage.setItem("tz", v);
  };

  // backend mengembalikan ts DESC (terbaru dulu) → "new" = apa adanya, "old" = dibalik.
  const newsRows = useMemo(() => {
    let rows = news?.log ?? [];
    if (newsStatus) rows = rows.filter((r) => (newsStatus === "veto" ? r.active : !r.active));
    return newsSort === "old" ? [...rows].reverse() : rows;
  }, [news, newsStatus, newsSort]);

  const screenRows = useMemo(() => {
    let rows = screen?.log ?? [];
    if (scPair) rows = rows.filter((r) => (r.symbol || "").toLowerCase().includes(scPair.toLowerCase()));
    if (scSignal) rows = rows.filter((r) => (r.signal || "") === scSignal);
    if (scReason) rows = rows.filter((r) => (r.blocked || "").toLowerCase().includes(scReason.toLowerCase()));
    if (scSort === "old") return [...rows].reverse();
    if (scSort === "pair") return [...rows].sort((a, b) => (a.symbol || "").localeCompare(b.symbol || ""));
    return rows;
  }, [screen, scPair, scSignal, scReason, scSort]);

  const newsCols: Col<NewsLogEntry>[] = [
    { t: `Waktu (${tz})`, render: (r) => fmtTime(r.ts, tz) },
    { t: "Status", render: (r) => (r.active ? "⛔ VETO" : "clear"), cls: (r) => (r.active ? "neg" : "pos") },
    { t: "Catatan", render: (r) => r.note || "—" },
  ];

  const screenCols: Col<ScreenLogEntry>[] = [
    { t: `Waktu (${tz})`, render: (r) => fmtTime(r.ts, tz) },
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
        <div className="grid" style={{ marginBottom: 12 }}>
          <label>
            Status
            <select value={newsStatus} onChange={(e) => setNewsStatus(e.target.value)}>
              <option value="">semua</option>
              <option value="veto">⛔ VETO</option>
              <option value="clear">clear</option>
            </select>
          </label>
          <label>
            Urutkan
            <select value={newsSort} onChange={(e) => setNewsSort(e.target.value)}>
              <option value="new">Waktu terbaru</option>
              <option value="old">Waktu terlama</option>
            </select>
          </label>
          <label>
            Zona waktu
            <select value={tz} onChange={(e) => changeTz(e.target.value)}>
              <option value="WIB">WIB (UTC+7)</option>
              <option value="WITA">WITA (UTC+8)</option>
              <option value="WIT">WIT (UTC+9)</option>
              <option value="UTC">UTC</option>
            </select>
          </label>
          <div className="sub" style={{ alignSelf: "end" }}>{newsRows.length} baris</div>
        </div>
        <PaginatedTable cols={newsCols} rows={newsRows} empty="Tak ada data sesuai filter" />
      </div>
      <div className="panel">
        <h2>Log Screening (per perubahan)</h2>
        <div className="grid" style={{ marginBottom: 12 }}>
          <label>
            Pair
            <input value={scPair} onChange={(e) => setScPair(e.target.value)} placeholder="mis. BTC" />
          </label>
          <label>
            Sinyal
            <select value={scSignal} onChange={(e) => setScSignal(e.target.value)}>
              <option value="">semua</option>
              <option value="LONG">LONG</option>
              <option value="SHORT">SHORT</option>
              <option value="skip">skip</option>
            </select>
          </label>
          <label>
            Alasan tak-entry
            <input value={scReason} onChange={(e) => setScReason(e.target.value)} placeholder="mis. news / korelasi / slot" />
          </label>
          <label>
            Urutkan
            <select value={scSort} onChange={(e) => setScSort(e.target.value)}>
              <option value="new">Waktu terbaru</option>
              <option value="old">Waktu terlama</option>
              <option value="pair">Pair A–Z</option>
            </select>
          </label>
          <label>
            Zona waktu
            <select value={tz} onChange={(e) => changeTz(e.target.value)}>
              <option value="WIB">WIB (UTC+7)</option>
              <option value="WITA">WITA (UTC+8)</option>
              <option value="WIT">WIT (UTC+9)</option>
              <option value="UTC">UTC</option>
            </select>
          </label>
          <div className="sub" style={{ alignSelf: "end" }}>
            {screenRows.length} baris
            {(scPair || scSignal || scReason) && (
              <button
                className="del"
                style={{ marginLeft: 8 }}
                onClick={() => {
                  setScPair("");
                  setScSignal("");
                  setScReason("");
                }}
              >
                reset
              </button>
            )}
          </div>
        </div>
        <PaginatedTable cols={screenCols} rows={screenRows} empty="Tak ada data sesuai filter" />
      </div>
    </>
  );
}
