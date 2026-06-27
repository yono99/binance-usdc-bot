import { usePoll } from "../hooks";
import { api } from "../api";
import type { GeminiUsage as GU } from "../types";
import { Table, type Col } from "./Table";
import { PaginatedTable } from "./PaginatedTable";

const n = (x: number) => x.toLocaleString("id-ID");
const ts = (s: string) => (s || "").slice(0, 19).replace("T", " ");

function Card({ lbl, val, c = "" }: { lbl: string; val: string | number; c?: string }) {
  return (
    <div className="card">
      <div className="lbl">{lbl}</div>
      <div className={`val ${c}`}>{val}</div>
    </div>
  );
}

export function GeminiUsage() {
  const { data } = usePoll<GU>(() => api.geminiUsage(300), 15000);
  if (!data) return null;

  const recentCols: Col<GU["recent"][number]>[] = [
    { t: "Waktu", render: (r) => ts(r.ts) },
    { t: "Tujuan", render: (r) => r.purpose || "—" },
    { t: "Model", render: (r) => r.model || "—" },
    { t: "Key#", render: (r) => r.key_idx },
    { t: "Prompt", render: (r) => n(r.prompt_tokens) },
    { t: "Output", render: (r) => n(r.output_tokens) },
    { t: "Total tok", render: (r) => n(r.total_tokens) },
    {
      t: "Status",
      render: (r) => (r.ok ? "✓" : "✕"),
      cls: (r) => (r.ok ? "pos" : "neg"),
    },
  ];

  return (
    <div className="panel">
      <h2>Pemantauan Token Gemini</h2>
      <div className="cards" style={{ marginBottom: 14 }}>
        <Card lbl="Token hari ini" val={n(data.today.tokens)} />
        <Card lbl="Panggilan hari ini" val={n(data.today.calls)} />
        <Card lbl="Total token" val={n(data.total.tokens)} />
        <Card lbl="Total panggilan" val={n(data.total.calls)} />
        <Card lbl="Error (gagal)" val={n(data.total.errors)} c={data.total.errors ? "neg" : ""} />
      </div>

      <div className="grid" style={{ marginBottom: 0 }}>
        <div>
          <div className="sub" style={{ marginBottom: 6 }}>Per model</div>
          <Table
            cols={[
              { t: "Model", render: (r) => r.model || "—" },
              { t: "Calls", render: (r) => n(r.calls) },
              { t: "Token", render: (r) => n(r.tok) },
            ]}
            rows={data.per_model}
          />
        </div>
        <div>
          <div className="sub" style={{ marginBottom: 6 }}>Per tujuan</div>
          <Table
            cols={[
              { t: "Tujuan", render: (r) => r.purpose || "—" },
              { t: "Calls", render: (r) => n(r.calls) },
              { t: "Token", render: (r) => n(r.tok) },
            ]}
            rows={data.per_purpose}
          />
        </div>
        <div>
          <div className="sub" style={{ marginBottom: 6 }}>Per key (rotasi)</div>
          <Table
            cols={[
              { t: "Key#", render: (r) => r.key_idx },
              { t: "Calls", render: (r) => n(r.calls) },
              { t: "Token", render: (r) => n(r.tok) },
              { t: "Err", render: (r) => n(r.errs), cls: (r) => (r.errs ? "neg" : "") },
            ]}
            rows={data.per_key}
          />
        </div>
      </div>

      <div className="sub" style={{ margin: "14px 0 6px" }}>Panggilan terakhir</div>
      <PaginatedTable cols={recentCols} rows={data.recent} empty="Belum ada panggilan Gemini" />
    </div>
  );
}
