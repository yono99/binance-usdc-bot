import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Account, Settings, Status } from "../types";
import { PairPicker } from "./PairPicker";
import { SearchSelect } from "./SearchSelect";

function riskWarn(lev: number, liq: number) {
  if (lev >= 50)
    return `⚠ Leverage ${lev}x: gerakan melawan ~${liq}% = LIKUIDASI (modal habis). SL berbasis ATR biasanya lebih lebar, jadi posisi kena likuidasi lebih dulu. Ini judi, bukan trading. Backtest strategi ini masih impas.`;
  if (lev >= 20) return `⚠ Leverage ${lev}x berisiko tinggi: likuidasi pada gerakan ~${liq}%.`;
  return "";
}
const liqPct = (lev: number) => Math.max(1 / lev - 0.005, 0.0005) * 100;

type Form = {
  enabled: boolean;
  technique: string;
  symbols: string[];
  leverage: number;
  bet_usd: number;
  balance_usd: number;
  target_profit_pct: number;
  max_open_positions: number;
  poll_seconds: number;
  order_type: string;
  taker_fee_pct: number;
  maker_fee_pct: number;
  gemini_model: string;
};

export function ControlPanel({
  status,
  available,
  account,
}: {
  status: Status | null;
  available: string[];
  account: Account | null;
}) {
  const isLive = account?.mode === "live";
  const liveBalance = account?.balance_usdc;
  const [s, setS] = useState<Settings | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [saved, setSaved] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [adjusted, setAdjusted] = useState<string[]>([]);
  const [lastManual, setLastManual] = useState(5);
  const balRef = useRef<HTMLInputElement>(null);
  const pendingBal = useRef<number | null>(null);

  useEffect(() => {
    api.settings().then((d) => {
      setS(d);
      setForm({
        enabled: d.enabled,
        technique: d.technique,
        symbols: d.symbols || [],
        leverage: d.leverage,
        bet_usd: d.bet_usd,
        balance_usd: d.balance_usd,
        target_profit_pct: d.target_profit_pct,
        max_open_positions: d.max_open_positions,
        poll_seconds: d.poll_seconds,
        order_type: d.order_type,
        taker_fee_pct: d.taker_fee_pct,
        maker_fee_pct: d.maker_fee_pct,
        gemini_model: d.gemini_model || "",
      });
    });
    api.geminiModels().then((r) => setModels(r.models));
  }, []);

  // Form Saldo = saldo hidup. LIVE: dari Binance Futures USDC (read-only).
  // DEMO/paper: dari status (paper, naik/turun mengikuti PnL), bisa diinput manual.
  // Jangan timpa saat user mengetik atau menunggu bot menerapkan (pendingBal).
  useEffect(() => {
    const live = isLive ? liveBalance : status?.balance_usd;
    if (live == null || !form) return;
    if (pendingBal.current != null && Math.abs(live - pendingBal.current) < 1e-9)
      pendingBal.current = null;
    if (!isLive && (document.activeElement === balRef.current || pendingBal.current != null)) return;
    setForm((p) => (p ? { ...p, balance_usd: live } : p));
  }, [status?.balance_usd, liveBalance, isLive]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!s || !form) return <div className="panel"><h2>Kontrol Bot (paper)</h2><div className="empty">memuat…</div></div>;

  const set = (k: keyof Form, v: Form[keyof Form]) => setForm((p) => (p ? { ...p, [k]: v } : p));
  const warn = riskWarn(form.leverage, +liqPct(form.leverage).toFixed(3));

  // Bidang numerik yang divalidasi engine (clamp). Jika user input ngawur,
  // engine kembalikan ke batas wajar -> tampilkan peringatan apa yang disesuaikan.
  const NUM_FIELDS: [keyof Form, string][] = [
    ["leverage", "Leverage"], ["bet_usd", "Bet"], ["target_profit_pct", "Target profit %"],
    ["max_open_positions", "Max posisi"], ["poll_seconds", "Interval screening"],
    ["taker_fee_pct", "Fee taker %"], ["maker_fee_pct", "Fee maker %"],
  ];

  const save = async () => {
    const sent = form;
    const res = await api.saveSettings(form as unknown as Record<string, unknown>);
    const adj: string[] = [];
    for (const [k, label] of NUM_FIELDS) {
      const a = sent[k] as number;
      const b = res[k] as unknown as number;
      if (typeof a === "number" && typeof b === "number" && Math.abs(a - b) > 1e-9)
        adj.push(`${label}: ${a} → ${b}`);
    }
    pendingBal.current = res.balance_usd;
    setS((prev) => (prev ? { ...prev, ...res } : res)); // merge, jaga 'techniques'
    // pakai nilai hasil clamp engine (kalau user input ngawur, ikut engine)
    setForm((p) =>
      p ? { ...p, leverage: res.leverage, bet_usd: res.bet_usd, target_profit_pct: res.target_profit_pct,
            max_open_positions: res.max_open_positions, poll_seconds: res.poll_seconds,
            taker_fee_pct: res.taker_fee_pct, maker_fee_pct: res.maker_fee_pct } : p
    );
    setAdjusted(adj);
    setSaved(" tersimpan ✓ (bot menerapkan tiap siklus)");
    setTimeout(() => setSaved(""), 4000);
  };

  return (
    <div className="panel">
      <h2>Kontrol Bot (paper)</h2>
      {warn && <div className="danger">{warn}</div>}
      <div className="grid">
        <label>
          Status
          <select value={String(form.enabled)} onChange={(e) => set("enabled", e.target.value === "true")}>
            <option value="false">OFF</option>
            <option value="true">ON (buka posisi)</option>
          </select>
        </label>
        <label>
          Teknik
          <select value={form.technique} onChange={(e) => set("technique", e.target.value)}>
            {(s.techniques || []).map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </label>
        <label style={{ gridColumn: "1 / -1" }}>
          Pair (multi — cari &amp; tambah) ·{" "}
          <span className="sub">kosong = screening SEMUA pair USDC ({available.length})</span>
          <PairPicker value={form.symbols} options={available} onChange={(v) => set("symbols", v)} />
        </label>
        <label>
          Leverage (x)
          <input type="number" min={1} max={125} value={form.leverage} onChange={(e) => set("leverage", +e.target.value)} />
        </label>
        <label>
          Bet / margin (USD)
          <input type="number" min={0.01} step={0.01} value={form.bet_usd} onChange={(e) => set("bet_usd", +e.target.value)} />
        </label>
        <label>
          Saldo (USD) — hidup{" "}
          <span className="sub">{isLive ? "(LIVE: Binance Futures USDC)" : "(paper: input manual)"}</span>
          <input
            ref={balRef}
            type="number"
            min={0}
            step={0.01}
            value={form.balance_usd}
            disabled={isLive}
            title={isLive ? "Saldo live diambil otomatis dari Binance — tidak bisa diubah" : ""}
            onChange={(e) => set("balance_usd", +e.target.value)}
          />
        </label>
        <label>
          Target profit ·{" "}
          <span className="sub">Auto = engine tentukan per-pair (volatilitas/ATR)</span>
          <select
            value={form.target_profit_pct > 0 ? "manual" : "auto"}
            onChange={(e) => set("target_profit_pct", e.target.value === "auto" ? 0 : lastManual || 5)}
          >
            <option value="auto">Auto (smart, per-pair ATR)</option>
            <option value="manual">Manual %</option>
          </select>
        </label>
        {form.target_profit_pct > 0 && (
          <label>
            Target profit % (manual)
            <input
              type="number"
              min={0.1}
              max={100}
              step={0.1}
              value={form.target_profit_pct}
              onChange={(e) => {
                const v = +e.target.value;
                setLastManual(v);
                set("target_profit_pct", v);
              }}
            />
          </label>
        )}
        <label>
          Max posisi terbuka
          <input type="number" min={1} max={20} step={1} value={form.max_open_positions} onChange={(e) => set("max_open_positions", +e.target.value)} />
        </label>
        <label>
          Interval screening (dtk)
          <input type="number" min={5} max={3600} step={1} value={form.poll_seconds} onChange={(e) => set("poll_seconds", +e.target.value)} />
        </label>
        <label>
          Jenis order
          <select value={form.order_type} onChange={(e) => set("order_type", e.target.value)}>
            <option value="limit">limit (maker)</option>
            <option value="market">market (taker)</option>
          </select>
        </label>
        <label>
          Fee taker % (market)
          <input type="number" min={0} step={0.001} value={form.taker_fee_pct} onChange={(e) => set("taker_fee_pct", +e.target.value)} />
        </label>
        <label>
          Fee maker % (limit)
          <input type="number" min={0} step={0.001} value={form.maker_fee_pct} onChange={(e) => set("maker_fee_pct", +e.target.value)} />
        </label>
        <label style={{ gridColumn: "1 / -1" }}>
          Model Gemini (screening regime/news) · <span className="sub">kosong = default config</span>
          <SearchSelect
            value={form.gemini_model || "(default config)"}
            options={["(default config)", ...models]}
            onChange={(v) => set("gemini_model", v === "(default config)" ? "" : v)}
            placeholder="cari model…"
          />
        </label>
        <label>
          Timeframe (otomatis)
          <input value={s.timeframe} disabled />
        </label>
      </div>
      {adjusted.length > 0 && (
        <div className="danger">
          ⚠ Nilai tak masuk akal — engine menyesuaikan ke batas wajar:{" "}
          {adjusted.join(" · ")}
        </div>
      )}
      <button onClick={save}>Simpan pengaturan</button>
      <span className="sub">{saved}</span>
    </div>
  );
}
