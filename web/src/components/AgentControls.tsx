import { useEffect, useState } from "react";
import { api } from "../api";
import { usePoll } from "../hooks";
import type { AgentAB, AgentHealth, AgentPlan, AgentSettings } from "../types";

const FLAGS: { k: keyof AgentSettings; lbl: string; hint: string }[] = [
  {
    k: "agent_manager_mode",
    lbl: "Manager-mode",
    hint: "Jalan A: disiplin (rules arah, planner+autonomous, tool-loop off)",
  },
  {
    k: "agent_full_auto",
    lbl: "Full-auto",
    hint: "tool-loop + autonomous + planner",
  },
  {
    k: "agent_tool_loop",
    lbl: "Tool-loop",
    hint: "nalar + tool — boros token",
  },
  {
    k: "agent_autonomous",
    lbl: "Autonomous",
    hint: "REDUCE_RISK / FLAT portofolio",
  },
  {
    k: "agent_planner",
    lbl: "Planner",
    hint: "stance / bias / kuota sesi",
  },
  {
    k: "agent_ab_shadow",
    lbl: "A/B shadow",
    hint: "catat verdict, tidak memblokir",
  },
  {
    k: "news_veto",
    lbl: "News-veto",
    hint: "blokir entry saat berita high-impact",
  },
];

const WARN: Partial<Record<keyof AgentSettings, { on?: string; off?: string }>> = {
  agent_manager_mode: {
    on: "Manager-mode (Jalan A): agent = manajer disiplin.\n\nArah dari RULES (bukan Gemini), planner+autonomous ON, tool-loop OFF.\n\nLanjut?",
  },
  agent_full_auto: {
    on: "Full-auto = tool-loop + autonomous + planner.\n\nLebih banyak panggilan Gemini (risiko 429). LIVE FLAT butuh allow_live_trader.\n\nLanjut?",
  },
  agent_tool_loop: {
    on: "Tool-loop menambah panggilan Gemini per keputusan. Free-tier mudah 429.\n\nLanjut?",
  },
  agent_autonomous: {
    on: "Autonomous boleh FLAT semua posisi atau geser SL ke BE. LIVE butuh allow_live_trader.\n\nLanjut?",
  },
  agent_planner: {
    on: "Planner bisa membatasi entry (kuota / risk-off).\n\nLanjut?",
  },
  agent_ab_shadow: {
    on: "A/B shadow: ReAct mencatat tanpa memblokir entry.\n\nLanjut?",
  },
  news_veto: {
    off: "Matikan news-veto: entry tetap dibuka saat berita high-impact.\n\nLanjut?",
  },
};

export function AgentControls({ compact = false }: { compact?: boolean }) {
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
    const w = v ? WARN[k]?.on : WARN[k]?.off;
    if (w && !window.confirm(w)) {
      setS((p) => (p ? { ...p } : p));
      return;
    }
    setBusy(true);
    try {
      setS(await api.saveAgentSettings({ [k]: v }));
      const lbl = FLAGS.find((f) => f.k === k)?.lbl ?? k;
      setNotice(`${lbl} ${v ? "ON" : "OFF"} — siklus berikutnya`);
      setTimeout(() => setNotice(""), 4000);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel">
      <h2>
        Agent flags
        <span className="sub">hot-reload</span>
      </h2>
      <div className="flag-grid">
        {FLAGS.map(({ k, lbl, hint }) => (
          <label key={k} className="flag-item">
            <input
              type="checkbox"
              disabled={!s || busy}
              checked={!!s?.[k]}
              onChange={(e) => toggle(k, e.target.checked)}
            />
            <span>
              <span className="flag-lbl">{lbl}</span>
              <div className="flag-hint">{hint}</div>
            </span>
          </label>
        ))}
      </div>
      {notice && (
        <div className="ok" style={{ marginTop: 10, marginBottom: 0 }}>
          {notice}
        </div>
      )}
      {!compact && (
        <>
          <div className="cards" style={{ marginTop: 12 }}>
            <Mini
              lbl="Rencana"
              val={plan?.stance ? `${plan.stance} / ${plan.bias}` : "—"}
            />
            <Mini
              lbl="Kuota"
              val={plan?.max_new_trades != null ? String(plan.max_new_trades) : "—"}
            />
            <Mini lbl="A/B" val={ab?.verdict ?? "—"} c={ab?.significant ? "pos" : ""} />
            <Mini
              lbl="LLM / fb"
              val={health ? `${health.llm}/${health.fallbacks}` : "—"}
              c={health && health.fallback_rate > 0.5 ? "neg" : ""}
            />
          </div>
          {plan?.reasoning && (
            <div className="sub" style={{ marginTop: 8 }}>
              {plan.reasoning}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Mini({ lbl, val, c = "" }: { lbl: string; val: string; c?: string }) {
  return (
    <div className="card">
      <div className="lbl">{lbl}</div>
      <div className={`val ${c}`} style={{ fontSize: 15 }}>
        {val}
      </div>
    </div>
  );
}
