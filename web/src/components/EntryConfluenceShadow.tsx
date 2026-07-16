import { useCallback, useEffect, useState } from "react";
import { api, f, cls, fmtWIB } from "../api";
import { PaginatedTable } from "./PaginatedTable";
import type { Col } from "./Table";

interface EcRecord {
  id: number;
  ts: string;
  symbol: string;
  side: string;
  setup: string;
  btc_tier: string;
  structure_pass: number;
  location_quality: string | null;
  would_enter: number;
  actually_entered: number;
  conviction: number;
  price: number;
  reason: string;
  outcome_r: number | null;
}

interface EcAgg {
  total_logged: number;
  would_enter: number;
  would_skip: number;
  actually_entered: number;
  would_enter_and_entered: number;
  would_skip_but_entered: number;
  by_setup: Record<string, { n: number; would_enter: number; actually_entered: number; avg_outcome_r: number }>;
  by_btc_tier: Record<string, { n: number; would_enter: number; avg_outcome_r: number }>;
  by_location: Record<string, { n: number; would_enter: number; avg_outcome_r: number }>;
}

interface EcShadowData {
  records: EcRecord[];
  aggregation: EcAgg;
}

export function EntryConfluenceShadow() {
  const [data, setData] = useState<EcShadowData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"records" | "agg">("agg");

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch("/api/entry-confluence-shadow");
      const json = await res.json();
      if (json.error) { setError(json.error); return; }
      setData(json);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 15000);
    return () => clearInterval(id);
  }, [fetchData]);

  if (error) {
    return (
      <div className="panel">
        <h2>Entry Confluence Shadow</h2>
        <div className="danger">{error}</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="panel">
        <h2>Entry Confluence Shadow</h2>
        <div className="empty">Memuat...</div>
      </div>
    );
  }

  const agg = data.aggregation;
  if (!agg.total_logged) {
    return (
      <div className="panel">
        <h2>Entry Confluence Shadow</h2>
        <div className="empty">Belum ada data — jalankan forwardtest.py</div>
      </div>
    );
  }

  const cols: Col<EcRecord>[] = [
    { t: "WIB", render: (r) => fmtWIB(r.ts) },
    { t: "Symbol", render: (r) => r.symbol, cls: () => "mono" },
    { t: "Side", render: (r) => r.side, cls: (r) => (r.side === "long" ? "pos" : "neg") },
    { t: "Setup", render: (r) => r.setup },
    { t: "BTC", render: (r) => r.btc_tier, cls: (r) => r.btc_tier === "full" ? "pos" : r.btc_tier === "blocked" ? "neg" : "" },
    { t: "Struct", render: (r) => r.structure_pass ? "Y" : "N", cls: (r) => r.structure_pass ? "pos" : "neg" },
    { t: "Location", render: (r) => r.location_quality ?? "—", cls: (r) => r.location_quality === "strong" ? "pos" : r.location_quality === "secondary" ? "" : "neg" },
    { t: "Gate", render: (r) => r.would_enter ? "ENTER" : "SKIP", cls: (r) => r.would_enter ? "pos" : "neg" },
    { t: "Entered", render: (r) => r.actually_entered ? "✓" : "—", cls: (r) => r.actually_entered ? "pos" : "" },
    { t: "R", render: (r) => r.outcome_r != null ? (r.outcome_r > 0 ? "+" : "") + f(r.outcome_r, 3) : "—", cls: (r) => cls(r.outcome_r) },
  ];

  const setupRows = Object.entries(agg.by_setup).sort(([, a], [, b]) => b.n - a.n);
  const tierRows = Object.entries(agg.by_btc_tier).sort(([, a], [, b]) => b.n - a.n);
  const locRows = Object.entries(agg.by_location).sort(([, a], [, b]) => b.n - a.n);

  return (
    <div className="panel">
      <h2>Entry Confluence Shadow</h2>
      <div className="tab-bar" style={{ marginBottom: 8 }}>
        <button className={activeTab === "agg" ? "active" : ""} onClick={() => setActiveTab("agg")}>
          Ringkasan
        </button>
        <button className={activeTab === "records" ? "active" : ""} onClick={() => setActiveTab("records")}>
          Records ({agg.total_logged})
        </button>
      </div>

      {activeTab === "agg" && (
        <div className="compact-table-wrap">
          <table className="compact-table">
            <thead>
              <tr>
                <th>Metric</th>
                <th>N</th>
                <th>Would Enter</th>
                <th>Actually Entered</th>
                <th>Skip But Entered</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><strong>Total</strong></td>
                <td>{agg.total_logged}</td>
                <td className={agg.would_enter > 0 ? "pos" : ""}>{agg.would_enter}</td>
                <td>{agg.actually_entered}</td>
                <td className="neg">{agg.would_skip_but_entered}</td>
              </tr>
            </tbody>
          </table>

          <h3 style={{ marginTop: 12 }}>Per Setup</h3>
          <table className="compact-table">
            <thead>
              <tr><th>Setup</th><th>N</th><th>Would Enter</th><th>Entered</th><th>Avg R</th></tr>
            </thead>
            <tbody>
              {setupRows.map(([k, v]) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td>{v.n}</td>
                  <td>{v.would_enter}</td>
                  <td>{v.actually_entered}</td>
                  <td className={cls(v.avg_outcome_r)}>{v.avg_outcome_r ? (v.avg_outcome_r > 0 ? "+" : "") + f(v.avg_outcome_r, 3) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3 style={{ marginTop: 12 }}>Per BTC Tier</h3>
          <table className="compact-table">
            <thead>
              <tr><th>BTC Tier</th><th>N</th><th>Would Enter</th><th>Avg R</th></tr>
            </thead>
            <tbody>
              {tierRows.map(([k, v]) => (
                <tr key={k}>
                  <td className={k === "full" ? "pos" : k === "blocked" ? "neg" : ""}>{k}</td>
                  <td>{v.n}</td>
                  <td>{v.would_enter}</td>
                  <td className={cls(v.avg_outcome_r)}>{v.avg_outcome_r ? (v.avg_outcome_r > 0 ? "+" : "") + f(v.avg_outcome_r, 3) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3 style={{ marginTop: 12 }}>Per Location Quality</h3>
          <table className="compact-table">
            <thead>
              <tr><th>Location</th><th>N</th><th>Would Enter</th><th>Avg R</th></tr>
            </thead>
            <tbody>
              {locRows.map(([k, v]) => (
                <tr key={k}>
                  <td className={k === "strong" ? "pos" : k === "secondary" ? "" : "neg"}>{k ?? "null"}</td>
                  <td>{v.n}</td>
                  <td>{v.would_enter}</td>
                  <td className={cls(v.avg_outcome_r)}>{v.avg_outcome_r ? (v.avg_outcome_r > 0 ? "+" : "") + f(v.avg_outcome_r, 3) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {activeTab === "records" && (
        <PaginatedTable cols={cols} rows={data.records} pageSizeOptions={[5, 10, 20, 50]} />
      )}
    </div>
  );
}
