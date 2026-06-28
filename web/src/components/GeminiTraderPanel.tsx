import { usePoll } from "../hooks";
import { api, cls, f } from "../api";
import type { GeminiTrader as GT } from "../types";
import { Table, type Col } from "./Table";

const ts = (s: string) => (s || "").slice(0, 19).replace("T", " ");

function Card({ lbl, val, c = "" }: { lbl: string; val: string | number; c?: string }) {
  return (
    <div className="card">
      <div className="lbl">{lbl}</div>
      <div className={`val ${c}`}>{val}</div>
    </div>
  );
}

const VERDICT_CLS: Record<string, string> = {
  PROMISING: "ok",
  WEAK: "danger",
  REJECTED: "danger",
  INSUFFICIENT: "",
};

export function GeminiTraderPanel() {
  const { data } = usePoll<GT>(() => api.geminiTrader(), 15000);
  if (!data) return null;

  const setupCols: Col<GT["per_setup"][number]>[] = [
    { t: "Setup", render: (r) => r.setup },
    { t: "Trades", render: (r) => r.n },
    { t: "Win%", render: (r) => f(r.win_rate, 1) },
    { t: "exp_R", render: (r) => (r.exp_r > 0 ? "+" : "") + f(r.exp_r, 3), cls: (r) => cls(r.exp_r) },
  ];

  const lessonCols: Col<GT["active_lessons"][number]>[] = [
    { t: "Setup", render: (r) => r.setup },
    { t: "Pelajaran (teruji)", render: (r) => r.text },
    { t: "n", render: (r) => r.n_support },
    { t: "exp_R", render: (r) => (r.exp_r_support > 0 ? "+" : "") + f(r.exp_r_support, 3), cls: (r) => cls(r.exp_r_support) },
    { t: "Conf", render: (r) => r.confidence },
  ];

  const decCols: Col<GT["recent"][number]>[] = [
    { t: "Waktu", render: (r) => ts(r.ts) },
    { t: "Simbol", render: (r) => r.symbol },
    { t: "Setup", render: (r) => r.setup },
    { t: "Sisi", render: (r) => (r.side || "").toUpperCase(), cls: (r) => (r.side === "long" ? "pos" : r.side === "short" ? "neg" : "") },
    { t: "Konviksi", render: (r) => f(r.conviction, 2) },
    { t: "R", render: (r) => (r.outcome_r == null ? "—" : (r.outcome_r > 0 ? "+" : "") + f(r.outcome_r, 3)), cls: (r) => cls(r.outcome_r) },
    { t: "Alasan", render: (r) => r.rationale || "—" },
  ];

  const sig = data.significance;

  return (
    <div className="panel">
      <h2>🤖 Gemini Trader — track record & playbook</h2>

      <div className={VERDICT_CLS[data.verdict] || "sub"} style={{ marginBottom: 12 }}>
        Verdict: <b>{data.verdict}</b>
        {data.verdict_reason ? ` — ${data.verdict_reason}` : ""}
        {data.verdict !== "PROMISING" ? " · (DEMO/paper — belum layak scale-up)" : ""}
      </div>

      <div className="cards" style={{ marginBottom: 14 }}>
        <Card lbl="Trade settled" val={data.n} />
        <Card lbl="Win Rate" val={data.win_rate != null ? f(data.win_rate, 1) + "%" : "—"} />
        <Card
          lbl="Expectancy R"
          val={data.exp_r != null ? (data.exp_r > 0 ? "+" : "") + f(data.exp_r, 3) : "—"}
          c={cls(data.exp_r)}
        />
        <Card lbl="Eff. sample" val={sig ? f(sig.eff_n, 1) : "—"} />
        <Card lbl="p (Bonferroni)" val={sig ? f(sig.p_adj, 3) : "—"} c={sig?.significant ? "pos" : ""} />
      </div>

      <div className="grid" style={{ marginBottom: 0 }}>
        <div>
          <div className="sub" style={{ marginBottom: 6 }}>Per setup</div>
          <Table cols={setupCols} rows={data.per_setup} empty="Belum ada trade" />
        </div>
        <div>
          <div className="sub" style={{ marginBottom: 6 }}>Playbook teruji (lolos bukti)</div>
          <Table cols={lessonCols} rows={data.active_lessons} empty="Belum ada pelajaran teruji" />
        </div>
      </div>

      <div className="sub" style={{ margin: "14px 0 6px" }}>Keputusan terakhir</div>
      <Table cols={decCols} rows={data.recent} empty="Belum ada keputusan — set teknik 'gemini' & jalankan bot" />
    </div>
  );
}
