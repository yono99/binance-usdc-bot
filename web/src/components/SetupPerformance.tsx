import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { f, cls } from "../api";
import { PaginatedTable } from "./PaginatedTable";
import type { Col } from "./Table";
import type { SetupStatsEntry } from "../types";

export function SetupPerformance() {
  const [data, setData] = useState<SetupStatsEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const res = await api.setupStats();
      setData(res.setups);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 10000);
    return () => clearInterval(id);
  }, [fetchData]);

  if (error) {
    return (
      <div className="panel">
        <h2>Setup Performance</h2>
        <div className="danger">{error}</div>
      </div>
    );
  }

  if (!data.length) {
    return (
      <div className="panel">
        <h2>Setup Performance</h2>
        <div className="empty">Belum ada data — jalankan forwardtest.py</div>
      </div>
    );
  }

  const cols: Col<SetupStatsEntry>[] = [
    { t: "Setup", render: (r) => <span title={r.reason}>{r.setup}</span> },
    { t: "Trades", render: (r) => r.trades },
    { t: "Win%", render: (r) => f(r.win_rate, 1) + "%" },
    { t: "exp_R", render: (r) => (r.exp_r > 0 ? "+" : "") + f(r.exp_r, 3), cls: (r) => cls(r.exp_r) },
    { t: "Status", render: (r) => r.status, cls: (r) => (r.status === "enable" ? "pos" : "neg") },
  ];

  return (
    <div className="panel">
      <h2>Setup Performance</h2>
      <PaginatedTable cols={cols} rows={data} pageSizeOptions={[5, 10, 20, 50, 100]} />
    </div>
  );
}