import { useEffect, useState } from "react";
import { api } from "../api";
import { usePoll } from "../hooks";
import type { AgentAB, AgentHealth, AgentPlan, AgentSettings } from "../types";

const FLAGS: { k: keyof AgentSettings; lbl: string; hint: string }[] = [
  { k: "agent_full_auto", lbl: "Full-auto", hint: "tool-loop + autonomous + planner" },
  { k: "agent_tool_loop", lbl: "Tool-loop", hint: "nalar + panggil tool (boros LLM)" },
  { k: "agent_autonomous", lbl: "Autonomous", hint: "kelola portofolio (REDUCE_RISK/FLAT)" },
  { k: "agent_planner", lbl: "Planner", hint: "tujuan sesi (stance/bias/kuota)" },
  { k: "agent_ab_shadow", lbl: "A/B shadow", hint: "catat verdict tanpa memblokir" },
];

export function AgentControls() {
  const [s, setS] = useState<AgentSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const { data: health } = usePoll<AgentHealth>(api.agentHealth, 10000);
  const { data: plan } = usePoll<AgentPlan>(api.agentPlan, 15000);
  const { data: ab } = usePoll<AgentAB>(api.agentAB, 15000);

  useEffect(() => {
    api.agentSettings().then(setS).catch(() => {});
  }, []);

  const toggle = async (k: keyof AgentSettings, v: boolean) => {
    setBusy(true);
    try {
      setS(await api.saveAgentSettings({ [k]: v }));
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
      <div className="sub" style={{ marginTop: 8 }}>
        full_auto menyalakan tool_loop + autonomous + planner. LIVE FLAT tetap butuh
        allow_live_trader. Tool-loop = lebih banyak panggilan Gemini.
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
