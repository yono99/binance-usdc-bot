import { useEffect, useState } from "react";
import { api, f, fmtWIB } from "../api";
import { AgentControls } from "../components/AgentControls";
import { Pager } from "../components/Pager";
import { usePoll } from "../hooks";
import type { AgentAB, AgentHealth, AgentPlan } from "../types";

type DecPage = {
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  decisions: Record<string, unknown>[];
};
type LesPage = {
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  lessons: Record<string, unknown>[];
};
type EvoPage = {
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  events: Record<string, unknown>[];
};

function actionPill(a: unknown) {
  const s = String(a ?? "—");
  const u = s.toUpperCase();
  let cls = "pill";
  if (u.includes("ENTER") || u.includes("LONG") || u.includes("SHORT")) cls += " enter";
  else if (u.includes("SKIP") || u.includes("FLAT") || u.includes("REDUCE")) cls += " skip";
  else if (u.includes("FAIL") || u.includes("ERROR")) cls += " fail";
  return <span className={cls}>{s}</span>;
}

export function AgentPage() {
  const [dPage, setDPage] = useState(1);
  const [dSize, setDSize] = useState(10);
  const [lPage, setLPage] = useState(1);
  const [lSize, setLSize] = useState(10);
  const [ePage, setEPage] = useState(1);
  const [eSize, setESize] = useState(10);

  const { data: health } = usePoll<AgentHealth>(api.agentHealth, 10000);
  const { data: plan } = usePoll<AgentPlan>(api.agentPlan, 15000);
  const { data: ab } = usePoll<AgentAB>(api.agentAB, 15000);

  const { data: dec, refetch: refDec } = usePoll<DecPage>(
    () => api.decisions(dPage, dSize),
    12000,
  );
  const { data: les, refetch: refLes } = usePoll<LesPage>(
    () => api.lessons(lPage, lSize),
    20000,
  );
  const { data: evo, refetch: refEvo } = usePoll<EvoPage>(
    () => api.evolution(ePage, eSize),
    30000,
  );

  useEffect(() => {
    refDec();
  }, [dPage, dSize, refDec]);
  useEffect(() => {
    refLes();
  }, [lPage, lSize, refLes]);
  useEffect(() => {
    refEvo();
  }, [ePage, eSize, refEvo]);

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Agent</h1>
          <p>Manajer disiplin — keputusan, pelajaran, A/B. Bukan mesin sinyal.</p>
        </div>
      </div>

      <div className="cards">
        <div className="card">
          <div className="lbl">LLM / fallback</div>
          <div
            className={`val mono ${health && health.fallback_rate > 0.5 ? "neg" : ""}`}
            style={{ fontSize: 18 }}
          >
            {health ? `${health.llm} / ${health.fallbacks}` : "—"}
          </div>
        </div>
        <div className="card">
          <div className="lbl">Fallback rate</div>
          <div className="val mono" style={{ fontSize: 18 }}>
            {health ? f(health.fallback_rate * 100, 1) + "%" : "—"}
          </div>
        </div>
        <div className="card">
          <div className="lbl">Plan</div>
          <div className="val" style={{ fontSize: 15, fontWeight: 600 }}>
            {plan?.stance ? `${plan.stance} · ${plan.bias ?? "—"}` : "—"}
          </div>
        </div>
        <div className="card">
          <div className="lbl">A/B</div>
          <div
            className={`val ${ab?.significant ? "pos" : ""}`}
            style={{ fontSize: 14, fontWeight: 650 }}
          >
            {ab?.verdict ?? "—"}
          </div>
        </div>
        <div className="card">
          <div className="lbl">Risk ↓?</div>
          <div
            className={`val ${ab?.reduces_risk ? "pos" : "muted"}`}
            style={{ fontSize: 14, fontWeight: 650 }}
          >
            {ab?.reduces_risk ? "ya" : ab?.risk_rules ? "tidak / n kecil" : "—"}
          </div>
        </div>
      </div>

      {plan?.reasoning && (
        <div className="panel panel-tight">
          <h2>Rencana sesi</h2>
          <div className="sub" style={{ whiteSpace: "pre-wrap" }}>
            kuota {plan.max_new_trades ?? "—"} · exposure{" "}
            {plan.max_exposure_frac != null ? f(plan.max_exposure_frac, 2) : "—"}
            {"\n"}
            {plan.reasoning}
          </div>
        </div>
      )}

      {ab && (
        <div className="panel panel-tight">
          <h2>A/B shadow</h2>
          <div className="line">
            n total <b>{ab.n_total ?? "—"}</b> · kept <b>{ab.n_kept ?? "—"}</b> · denied{" "}
            <b>{ab.n_denied ?? "—"}</b>
            <br />
            exp_R rules <b className={clsNum(ab.exp_r_rules)}>{fmtR(ab.exp_r_rules)}</b>
            {" → "}
            rules+ReAct{" "}
            <b className={clsNum(ab.exp_r_rules_react)}>{fmtR(ab.exp_r_rules_react)}</b>
            {ab.p_value != null && <> · p={f(ab.p_value, 3)}</>}
            {ab.risk_rules && (
              <>
                <br />
                maxDD rules {fmtR(ab.risk_rules.max_drawdown_r)}R → ReAct{" "}
                {fmtR(ab.risk_react?.max_drawdown_r)}R
              </>
            )}
          </div>
          {ab.reason && (
            <div className="sub" style={{ marginTop: 6 }}>
              {ab.reason}
            </div>
          )}
        </div>
      )}

      <AgentControls />

      <div className="panel">
        <h2>
          Keputusan
          <span className="sub">{dec ? `${dec.total} total` : ""}</span>
        </h2>
        {!dec?.decisions?.length ? (
          <div className="empty">Belum ada decision_log</div>
        ) : (
          <>
            <div className="kv-list">
              {dec.decisions.map((r, i) => (
                <div className="kv-row" key={i}>
                  <div className="kv-top">
                    {actionPill(r.action ?? r.react_action)}
                    <b>{String(r.symbol ?? "—")}</b>
                    <span className="muted mono">{String(r.source ?? "")}</span>
                    {r.confidence != null && (
                      <span className="muted">conf {f(Number(r.confidence), 2)}</span>
                    )}
                    {r.outcome_r != null && (
                      <span className={clsNum(Number(r.outcome_r))}>
                        R {fmtR(Number(r.outcome_r))}
                      </span>
                    )}
                    <span className="muted" style={{ marginLeft: "auto" }}>
                      {fmtWIB(String(r.ts ?? r.filled_at ?? ""))}
                    </span>
                  </div>
                  {r.reasoning != null || r.reason != null ? (
                    <div className="kv-body">{String(r.reasoning ?? r.reason ?? "")}</div>
                  ) : null}
                </div>
              ))}
            </div>
            <Pager
              total={dec.total}
              page={dPage}
              size={dSize}
              onPage={setDPage}
              onSize={setDSize}
            />
          </>
        )}
      </div>

      <div className="row2">
        <div className="panel">
          <h2>
            Pelajaran
            <span className="sub">{les ? `${les.total}` : ""}</span>
          </h2>
          {!les?.lessons?.length ? (
            <div className="empty">Kosong</div>
          ) : (
            <>
              <div className="kv-list">
                {les.lessons.map((l, i) => {
                  const trig = Number(l.times_triggered ?? l.triggered ?? 0);
                  const ok = Number(l.times_correct ?? 0);
                  const acc = trig > 0 ? ok / trig : null;
                  return (
                    <div className="kv-row" key={i}>
                      <div className="kv-top">
                        <span className="pill">acc {acc == null ? "—" : f(acc, 2)}</span>
                        <span className="muted">
                          {ok}/{trig}
                        </span>
                      </div>
                      <div className="kv-body">
                        {String(l.lesson ?? l.text ?? l.rule ?? JSON.stringify(l))}
                      </div>
                    </div>
                  );
                })}
              </div>
              <Pager
                total={les.total}
                page={lPage}
                size={lSize}
                onPage={setLPage}
                onSize={setLSize}
              />
            </>
          )}
        </div>

        <div className="panel">
          <h2>
            Evolusi
            <span className="sub">{evo ? `${evo.total}` : ""}</span>
          </h2>
          {!evo?.events?.length ? (
            <div className="empty">Belum ada event</div>
          ) : (
            <>
              <div className="kv-list">
                {evo.events.map((e, i) => (
                  <div className="kv-row" key={i}>
                    <div className="kv-top">
                      <span className={`pill ${e.applied ? "enter" : "skip"}`}>
                        {e.applied ? "applied" : "skip"}
                      </span>
                      {e.p_value != null && (
                        <span className="muted">p={f(Number(e.p_value), 3)}</span>
                      )}
                      <span className="muted" style={{ marginLeft: "auto" }}>
                        {fmtWIB(String(e.ts ?? ""))}
                      </span>
                    </div>
                    <div className="kv-body mono">
                      conf {String(e.before ?? e.before_conf ?? "?")} →{" "}
                      {String(e.after ?? e.after_conf ?? "?")}
                      {e.reason ? `\n${e.reason}` : ""}
                    </div>
                  </div>
                ))}
              </div>
              <Pager
                total={evo.total}
                page={ePage}
                size={eSize}
                onPage={setEPage}
                onSize={setESize}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function fmtR(n: number | null | undefined) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  const v = Number(n);
  return (v > 0 ? "+" : "") + f(v, 3);
}
function clsNum(n: number | null | undefined) {
  if (n == null || Number.isNaN(Number(n))) return "";
  return Number(n) > 0 ? "pos" : Number(n) < 0 ? "neg" : "";
}
