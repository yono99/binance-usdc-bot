import { SearchSelect } from "../SearchSelect";
import type { FormSetter, SettingsForm } from "./settingsForm";

/**
 * Setting server — timeframe, model Gemini, interval poll/keputusan/kelola.
 * Bukan risk/sizing/order pribadi.
 */
export function ServerSettings({
  form,
  set,
  models,
  timeframe,
}: {
  form: SettingsForm;
  set: FormSetter;
  models: string[];
  timeframe: string;
}) {
  return (
    <section className="settings-section">
      <header className="settings-section-head">
        <h3>Setting server</h3>
        <span className="sub">
          Timeframe, model Gemini, interval bot — config proses (bukan risk
          pribadi)
        </span>
      </header>

      <div className="settings-group">
        <div className="settings-group-title">Proses bot</div>
        <div className="grid">
          <label>
            Timeframe (otomatis)
            <input value={timeframe} disabled title="Dari config/engine — read-only" />
          </label>
          <label>
            Interval refresh bot (dtk) ·{" "}
            <span className="sub">sinyal dievaluasi per bar TF</span>
            <input
              type="number"
              min={5}
              max={3600}
              step={1}
              value={form.poll_seconds}
              onChange={(e) => set("poll_seconds", +e.target.value)}
            />
          </label>
          <label style={{ gridColumn: "1 / -1" }}>
            Model Gemini (screening regime/news) ·{" "}
            <span className="sub">kosong = default config</span>
            <SearchSelect
              value={form.gemini_model || "(default config)"}
              options={["(default config)", ...models]}
              onChange={(v) =>
                set("gemini_model", v === "(default config)" ? "" : v)
              }
              placeholder="cari model…"
            />
          </label>
        </div>
      </div>

      <div className="settings-group">
        <div className="settings-group-title">
          Interval Gemini ·{" "}
          <span className="sub" style={{ fontWeight: 400 }}>
            frekuensi panggilan → hemat RPM/token · makin besar = makin jarang
          </span>
        </div>
        <div className="grid">
          <label>
            Interval keputusan (dtk) ·{" "}
            <span className="sub">teknik gemini per simbol</span>
            <input
              type="number"
              min={30}
              max={3600}
              step={5}
              value={form.gemini_decide_seconds}
              onChange={(e) => set("gemini_decide_seconds", +e.target.value)}
            />
          </label>
          <label>
            Interval kelola posisi (dtk)
            <input
              type="number"
              min={30}
              max={3600}
              step={5}
              value={form.gemini_manage_seconds}
              onChange={(e) => set("gemini_manage_seconds", +e.target.value)}
            />
          </label>
          <label>
            Grace tahan minimal (dtk){" "}
            <span className="sub">manajer tak exit dini; SL/TP tetap jaga</span>
            <input
              type="number"
              min={0}
              max={86400}
              step={30}
              value={form.gemini_min_hold_s}
              onChange={(e) => set("gemini_min_hold_s", +e.target.value)}
            />
          </label>
          <label>
            Interval review portofolio (dtk)
            <input
              type="number"
              min={60}
              max={3600}
              step={10}
              value={form.gemini_portfolio_seconds}
              onChange={(e) =>
                set("gemini_portfolio_seconds", +e.target.value)
              }
            />
          </label>
          <label>
            Interval planner (jam)
            <input
              type="number"
              min={1}
              max={24}
              step={1}
              value={form.gemini_plan_hours}
              onChange={(e) => set("gemini_plan_hours", +e.target.value)}
            />
          </label>
          <label>
            Maks langkah tool-loop ·{" "}
            <span className="sub">makin kecil = makin hemat token</span>
            <input
              type="number"
              min={1}
              max={8}
              step={1}
              value={form.gemini_tool_iters}
              onChange={(e) => set("gemini_tool_iters", +e.target.value)}
            />
          </label>
        </div>
      </div>
    </section>
  );
}
