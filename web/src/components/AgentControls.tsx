import { useEffect, useState } from "react";
import { api } from "../api";
import { usePoll } from "../hooks";
import type { AgentAB, AgentHealth, AgentPlan, AgentSettings } from "../types";

const FLAGS: { k: keyof AgentSettings; lbl: string; hint: string }[] = [
  { k: "agent_manager_mode", lbl: "Manager-mode", hint: "Jalan A: manajer disiplin (rules arah, planner+autonomous, tool-loop off)" },
  { k: "agent_full_auto", lbl: "Full-auto", hint: "tool-loop + autonomous + planner" },
  { k: "agent_tool_loop", lbl: "Tool-loop", hint: "nalar + panggil tool (boros LLM)" },
  { k: "agent_autonomous", lbl: "Autonomous", hint: "kelola portofolio (REDUCE_RISK/FLAT)" },
  { k: "agent_planner", lbl: "Planner", hint: "tujuan sesi (stance/bias/kuota)" },
  { k: "agent_ab_shadow", lbl: "A/B shadow", hint: "catat verdict tanpa memblokir" },
  { k: "news_veto", lbl: "News-veto", hint: "veto entry saat berita high-impact" },
];

// Peringatan konsekuensi SEBELUM menyalakan (atau mematikan proteksi).
const WARN: Partial<Record<keyof AgentSettings, { on?: string; off?: string }>> = {
  agent_manager_mode: {
    on: "Manager-mode (Jalan A): agent jadi MANAJER DISIPLIN.\n\nArah trade dari RULES deterministik (mematikan teknik gemini/arah-LLM), planner + autonomous ON, tool-loop OFF (hemat token). Fokus: kelola risiko & bertahan, bukan menebak arah.\n\nLanjut?",
  },
  agent_full_auto: {
    on: "Full-auto = tool-loop + autonomous + planner sekaligus.\n\nTool-loop memanggil banyak tool tiap keputusan → BANYAK panggilan Gemini; di free-tier bisa kena rate-limit (429). Di LIVE, aksi FLAT butuh allow_live_trader.\n\nLanjut menyalakan?",
  },
  agent_tool_loop: {
    on: "Tool-loop: agen memanggil tool berulang tiap keputusan → jauh lebih banyak panggilan Gemini. Di free-tier gampang kena 429.\n\nLanjut?",
  },
  agent_autonomous: {
    on: "Autonomous: agen boleh MENUTUP SEMUA posisi (FLAT) atau menggeser stop ke breakeven otomatis. Di LIVE, FLAT butuh allow_live_trader.\n\nLanjut?",
  },
  agent_planner: {
    on: "Planner menetapkan tujuan sesi yang bisa MEMBATASI entry (kuota trade/eksposur, atau risk-off = stop buka posisi).\n\nLanjut?",
  },
  agent_ab_shadow: {
    on: "A/B shadow: ReAct mengevaluasi tapi TIDAK memblokir — rules tetap mengeksekusi SEMUA entry (untuk mengukur nilai agen).\n\nLanjut?",
  },
  news_veto: {
    off: "Mematikan News-veto: entry TETAP dibuka walau ada berita high-impact (proteksi berita nonaktif).\n\nLanjut mematikan?",
  },
};

export function AgentControls() {
  const [s, setS] = useState<AgentSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const { data: health } = usePoll<AgentHealth>(api.agentHealth, 10000);
  const { data: plan } = usePoll<AgentPlan>(api.agentPlan, 15000);
  const { data: ab } = usePoll<AgentAB>(api.agentAB, 15000);

  useEffect(() => {
    api.agentSettings().then(setS).catch(() => {});
  }, []);

  const toggle = async (k: keyof AgentSettings, v: boolean) => {
    const w = v ? WARN[k]?.on : WARN[k]?.off;      // peringatan sesuai arah (nyalakan/matikan)
    if (w && !window.confirm(w)) {
      setS((p) => (p ? { ...p } : p));             // batal → paksa render ulang, kembalikan centang
      return;
    }
    setBusy(true);
    try {
      setS(await api.saveAgentSettings({ [k]: v }));
      const lbl = FLAGS.find((f) => f.k === k)?.lbl ?? k;
      setNotice(`✓ ${lbl} ${v ? "ON" : "OFF"} — diterapkan (bot pakai siklus berikutnya)`);
      setTimeout(() => setNotice(""), 4500);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel">
      <h2>Kontrol Agent <span className="sub">(hot-reload, tanpa restart)</span></h2>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16 }}>
        {FLAGS.map(({ k, lbl, hint }) => (
          <label key={k} title={hint} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <input
              type="checkbox"
              disabled={!s || busy}
              checked={!!s?.[k]}
              onChange={(e) => toggle(k, e.target.checked)}
            />
            <span>{lbl}</span>
          </label>
        ))}
      </div>
      {notice && <div className="pos" style={{ marginTop: 8 }}>{notice}</div>}
      <div className="sub" style={{ marginTop: 8 }}>
        full_auto menyalakan tool_loop + autonomous + planner. LIVE FLAT tetap butuh
        allow_live_trader. Tool-loop = lebih banyak panggilan Gemini (bisa 429 di free-tier).
        Mencentang yang berisiko akan minta konfirmasi dulu.
      </div>

      <div className="cards" style={{ marginTop: 14 }}>
        <Mini lbl="Rencana sesi" val={plan?.stance ? `${plan.stance} / ${plan.bias}` : "—"} />
        <Mini lbl="Kuota trade" val={plan?.max_new_trades != null ? String(plan.max_new_trades) : "—"} />
        <Mini
          lbl="A/B verdict"
          val={ab?.verdict ?? "—"}
          c={ab?.significant ? "pos" : ""}
        />
        <Mini
          lbl="Drawdown rules→ReAct"
          val={
            ab?.risk_rules?.max_drawdown_r != null
              ? `${ab.risk_rules.max_drawdown_r}R → ${ab.risk_react?.max_drawdown_r ?? "—"}R`
              : "—"
          }
          c={ab?.reduces_risk ? "pos" : ""}
        />
        <Mini
          lbl="LLM vs fallback"
          val={health ? `${health.llm}/${health.fallbacks}` : "—"}
          c={health && health.fallback_rate > 0.5 ? "neg" : ""}
        />
      </div>
      {plan?.reasoning && (
        <div className="sub" style={{ marginTop: 8 }}>Plan: {plan.reasoning}</div>
      )}
    </div>
  );
}

function Mini({ lbl, val, c = "" }: { lbl: string; val: string; c?: string }) {
  return (
    <div className="card">
      <div className="lbl">{lbl}</div>
      <div className={`val ${c}`} style={{ fontSize: 16 }}>{val}</div>
    </div>
  );
}
