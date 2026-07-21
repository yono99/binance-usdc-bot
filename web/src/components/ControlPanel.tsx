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

// Default teknik gemini yang direkomendasikan. HANYA knob teknik/gerbang/throttle/fee —
// modal & identitas (saldo, mode, leverage, bet, pair, gemini_model) TAK disentuh.
// Prinsip: jangan over-gate (korelasi 0.55 mereka blok terlalu banyak → 0.85), throttle
// hemat free-tier, circuit breaker harian nyala tapi tak mencekik, fee USDC-M promo benar.
const REKOMENDASI_GEMINI: Partial<Form> = {
  technique: "gemini",
  order_type: "limit",              // maker lebih murah (exit SL/TP tetap taker)
  corr_threshold: 0.85,             // longgarkan dari 0.55 (over-gating = sedikit entry & tetap -EV)
  corr_lookback: 50,
  max_open_positions: 5,            // $ kecil: 20 slot menyebar modal terlalu tipis
  daily_max_loss_pct: 20,           // breaker harian nyala (100 = praktis mati)
  daily_max_trades: 20,
  poll_seconds: 60,
  gemini_decide_seconds: 180,
  gemini_manage_seconds: 60,
  gemini_min_hold_s: 300,           // anti-whipsaw
  gemini_portfolio_seconds: 300,
  gemini_plan_hours: 6,
  gemini_tool_iters: 4,
  taker_fee_pct: 0.05,
  maker_fee_pct: 0.02,
  usdc_taker_fee_pct: 0.04,
  usdc_maker_fee_pct: 0.0,
};

type Form = {
  enabled: boolean;
  technique: string;
  symbols: string[];
  leverage: number;
  bet_usd: number;
  bet_pct: number;
  // saldo per-wallet (USDC/USDT). Form UI exposes kedua input.
  balance_usdt: number;
  balance_usdc: number;
  target_profit_pct: number;
  max_open_positions: number;
  daily_max_loss_pct: number;
  daily_max_trades: number;
  corr_threshold: number;
  corr_lookback: number;
  poll_seconds: number;
  gemini_decide_seconds: number;
  gemini_manage_seconds: number;
  gemini_min_hold_s: number;
  gemini_portfolio_seconds: number;
  gemini_plan_hours: number;
  gemini_tool_iters: number;
  mode: string;
  order_type: string;
  taker_fee_pct: number;
  maker_fee_pct: number;
  usdc_maker_fee_pct: number;
  usdc_taker_fee_pct: number;
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
  const isLive = status?.mode === "live";              // mode efektif yang BERJALAN
  const [s, setS] = useState<Settings | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [saved, setSaved] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [adjusted, setAdjusted] = useState<string[]>([]);
  const [lastManual, setLastManual] = useState(5);
  const balRefUsdt = useRef<HTMLInputElement>(null);
  const balRefUsdc = useRef<HTMLInputElement>(null);
  const pendingBalUsdt = useRef<number | null>(null);
  const pendingBalUsdc = useRef<number | null>(null);

  const toForm = (d: Settings): Form => ({
    enabled: d.enabled,
    technique: d.technique,
    symbols: d.symbols || [],
    leverage: d.leverage,
    bet_usd: d.bet_usd,
    bet_pct: d.bet_pct ?? 0,
    balance_usdt: d.balance_usdt ?? 0,
    balance_usdc: d.balance_usdc ?? 0,
    target_profit_pct: d.target_profit_pct,
    max_open_positions: d.max_open_positions,
    daily_max_loss_pct: d.daily_max_loss_pct,
    daily_max_trades: d.daily_max_trades,
    corr_threshold: d.corr_threshold ?? 0.85,
    corr_lookback: d.corr_lookback ?? 50,
    poll_seconds: d.poll_seconds,
    gemini_decide_seconds: d.gemini_decide_seconds ?? 180,
    gemini_manage_seconds: d.gemini_manage_seconds ?? 60,
    gemini_min_hold_s: d.gemini_min_hold_s ?? 300,
    gemini_portfolio_seconds: d.gemini_portfolio_seconds ?? 300,
    gemini_plan_hours: d.gemini_plan_hours ?? 6,
    gemini_tool_iters: d.gemini_tool_iters ?? 4,
    mode: d.mode || "",
    order_type: d.order_type,
    taker_fee_pct: d.taker_fee_pct,
    maker_fee_pct: d.maker_fee_pct,
    usdc_maker_fee_pct: d.usdc_maker_fee_pct,
    usdc_taker_fee_pct: d.usdc_taker_fee_pct,
    gemini_model: d.gemini_model || "",
  });

  useEffect(() => {
    api.settings().then((d) => {
      setS(d);
      setForm(toForm(d));
    });
    api.geminiModels().then((r) => setModels(r.models));
  }, []);

  // Form Saldo = saldo hidup. LIVE: dari Binance Futures USDC (read-only).
  // DEMO/paper: dari status (paper, naik/turun mengikuti PnL), bisa diinput manual.
  // saldo TERPISAH per-wallet (USDT/USDC). Jangan timpa saat user mengetik atau
  // menunggu bot menerapkan (pendingBal*).
  useEffect(() => {
    if (!form) return;
    const usdt = isLive ? account?.balance_usdt : status?.balance_usdt;
    const usdc = isLive ? account?.balance_usdc : status?.balance_usdc;
    const apply = (key: "balance_usdt" | "balance_usdc", val: number) => {
      if (val == null) return;
      const refKey = key === "balance_usdt" ? pendingBalUsdt : pendingBalUsdc;
      if (refKey.current != null && Math.abs(val - refKey.current) < 1e-9)
        refKey.current = null;
      const activeRef = key === "balance_usdt" ? balRefUsdt.current : balRefUsdc.current;
      if (!isLive && (document.activeElement === activeRef || refKey.current != null)) return;
      setForm((p) => (p ? { ...p, [key]: val } : p));
    };
    apply("balance_usdt", usdt ?? form.balance_usdt);
    apply("balance_usdc", usdc ?? form.balance_usdc);
  }, [status?.balance_usdt, status?.balance_usdc,
      account?.balance_usdt, account?.balance_usdc, isLive]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!s || !form) return <div className="panel"><h2>Bot control</h2><div className="empty">memuat…</div></div>;

  const set = (k: keyof Form, v: Form[keyof Form]) => setForm((p) => (p ? { ...p, [k]: v } : p));

  const resetRekomendasi = () => {
    if (!confirm("Muat default teknik gemini rekomendasi?\n\nMengubah: teknik, guard korelasi, throttle Gemini, breaker harian, jenis order & fee.\nTIDAK menyentuh: saldo, mode, leverage, bet, pair, model.\n\nNilai hanya DIMUAT ke form — tekan Simpan untuk menerapkan."))
      return;
    setForm((p) => (p ? { ...p, ...REKOMENDASI_GEMINI } : p));
    setSaved(" rekomendasi dimuat — tekan Simpan untuk menerapkan");
    setTimeout(() => setSaved(""), 6000);
  };
  const warn = riskWarn(form.leverage, +liqPct(form.leverage).toFixed(3));

  const setMode = async (m: string) => {
    if (m === "live") {
      if (!confirm("⚠ AKTIFKAN MODE LIVE — UANG NYATA?\n\nBot akan menempatkan order ASLI di Binance Futures memakai API key live kamu. Pastikan: key Futures-only + withdrawal OFF + IP-locked, dan mulai dengan bet SANGAT KECIL. Methodology: strategi ini belum ada edge (impas).\n\nLanjut?"))
        return;
      if (!confirm("Konfirmasi sekali lagi — ini UANG NYATA. Yakin mengaktifkan LIVE?")) return;
    }
    // persist pilihan mode ke backend (tanpa ini, refresh kembali ke mode aktif lama
    // dan Simpan menulis ke bucket mode yang salah), lalu muat setting milik mode itu
    try {
      await api.setMode(m);
      const d = await api.settings(m);
      setS(d);
      setForm(toForm(d));
    } catch {
      set("mode", m);
    }
  };

  // Bidang numerik yang divalidasi engine (clamp). Jika user input ngawur,
  // engine kembalikan ke batas wajar -> tampilkan peringatan apa yang disesuaikan.
  const NUM_FIELDS: [keyof Form, string][] = [
    ["leverage", "Leverage"], ["bet_usd", "Bet"], ["bet_pct", "Bet % saldo"],
    ["target_profit_pct", "Target profit %"],
    ["max_open_positions", "Max posisi"], ["poll_seconds", "Interval screening"],
    ["daily_max_loss_pct", "Stop-loss harian %"], ["daily_max_trades", "Max trade harian"],
    ["taker_fee_pct", "Fee taker USDT %"], ["maker_fee_pct", "Fee maker USDT %"],
    ["usdc_taker_fee_pct", "Fee taker USDC %"], ["usdc_maker_fee_pct", "Fee maker USDC %"],
    ["gemini_decide_seconds", "Interval keputusan"], ["gemini_manage_seconds", "Interval kelola"],
    ["gemini_min_hold_s", "Grace tahan minimal"],
    ["gemini_portfolio_seconds", "Interval portofolio"], ["gemini_plan_hours", "Interval planner"],
    ["gemini_tool_iters", "Maks tool-loop"],
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
    pendingBalUsdt.current = res.balance_usdt ?? null;
    pendingBalUsdc.current = res.balance_usdc ?? null;
    setS((prev) => (prev ? { ...prev, ...res } : res)); // merge, jaga 'techniques'
    // pakai nilai hasil clamp engine (kalau user input ngawur, ikut engine)
    setForm((p) =>
      p ? { ...p, leverage: res.leverage, bet_usd: res.bet_usd, bet_pct: res.bet_pct ?? 0,
            target_profit_pct: res.target_profit_pct,
            max_open_positions: res.max_open_positions, poll_seconds: res.poll_seconds,
            daily_max_loss_pct: res.daily_max_loss_pct, daily_max_trades: res.daily_max_trades,
            corr_threshold: res.corr_threshold ?? p.corr_threshold, corr_lookback: res.corr_lookback ?? p.corr_lookback,
            taker_fee_pct: res.taker_fee_pct, maker_fee_pct: res.maker_fee_pct,
            usdc_taker_fee_pct: res.usdc_taker_fee_pct, usdc_maker_fee_pct: res.usdc_maker_fee_pct,
            gemini_decide_seconds: res.gemini_decide_seconds ?? p.gemini_decide_seconds,
            gemini_manage_seconds: res.gemini_manage_seconds ?? p.gemini_manage_seconds,
            gemini_min_hold_s: res.gemini_min_hold_s ?? p.gemini_min_hold_s,
            gemini_portfolio_seconds: res.gemini_portfolio_seconds ?? p.gemini_portfolio_seconds,
            gemini_plan_hours: res.gemini_plan_hours ?? p.gemini_plan_hours,
            gemini_tool_iters: res.gemini_tool_iters ?? p.gemini_tool_iters } : p
    );
    setAdjusted(adj);
    setSaved(" tersimpan ✓ (bot menerapkan tiap siklus)");
    setTimeout(() => setSaved(""), 4000);
  };

  return (
    <div className="panel">
      <h2>
        Bot control
        <span className="sub">{isLive ? "LIVE — uang nyata" : "paper"}</span>
      </h2>
      {isLive && (
        <div className="danger">
          ⚠ <b>MODE LIVE AKTIF — UANG NYATA.</b> Order ditempatkan ASLI di Binance Futures.
          Circuit breaker &amp; guard tetap aktif, tapi risiko penuh milikmu. Saldo diambil dari akun live.
        </div>
      )}
      {form.mode === "live" && !isLive && (
        <div className="danger">
          ⚠ Mode <b>live</b> dipilih — berlaku setelah <b>Simpan</b> &amp; bot beralih (butuh
          BINANCE_LIVE_KEY/SECRET di .env). Bila key tak ada, bot tetap paper.
        </div>
      )}
      {warn && <div className="danger">{warn}</div>}
      <div className="grid">
        <label>
          Mode
          <select value={form.mode} onChange={(e) => setMode(e.target.value)}>
            <option value="">ikut .env</option>
            <option value="dry">dry (paper)</option>
            <option value="test">test (paper)</option>
            <option value="live">live (UANG NYATA)</option>
          </select>
        </label>
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
          Bet / margin (USD) ·{" "}
          <span className="sub">{form.bet_pct > 0 ? "diabaikan (pakai % saldo)" : "margin tetap"}</span>
          <input type="number" min={0.01} step={0.01} value={form.bet_usd}
            disabled={form.bet_pct > 0}
            onChange={(e) => set("bet_usd", +e.target.value)} />
        </label>
        <label>
          Bet % saldo (adaptif) ·{" "}
          <span className="sub">0 = pakai margin tetap · &gt;0 = margin auto-scale saat modal tumbuh ($10→naik)</span>
          <input type="number" min={0} max={100} step={0.5} value={form.bet_pct}
            onChange={(e) => set("bet_pct", +e.target.value)} />
        </label>
        <label style={{ gridColumn: "1 / -1" }}>
          Saldo per-wallet —{" "}
          <span className="sub">
            {isLive ? "LIVE: dari Binance Futures — read-only · " : ""}
            USDT (wallet USDT-M) & USDC (wallet USDC-M) terpisah
          </span>
          <div style={{ display: "flex", gap: 12, marginTop: 4 }}>
            <label style={{ flex: 1 }}>
              USDT
              <input
                ref={balRefUsdt}
                type="number" min={0} step={0.01}
                value={form.balance_usdt}
                disabled={isLive}
                title={isLive ? "Saldo USDT diambil otomatis dari Binance" : ""}
                onChange={(e) => set("balance_usdt", +e.target.value)}
              />
            </label>
            <label style={{ flex: 1 }}>
              USDC
              <input
                ref={balRefUsdc}
                type="number" min={0} step={0.01}
                value={form.balance_usdc}
                disabled={isLive}
                title={isLive ? "Saldo USDC diambil otomatis dari Binance" : ""}
                onChange={(e) => set("balance_usdc", +e.target.value)}
              />
            </label>
          </div>
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
          Stop-loss harian % · <span className="sub">circuit breaker; rugi ≥ % saldo awal hari → stop (0 = nonaktif)</span>
          <input type="number" min={0} max={100} step={0.1} value={form.daily_max_loss_pct} onChange={(e) => set("daily_max_loss_pct", +e.target.value)} />
        </label>
        <label>
          Max trade harian · <span className="sub">stop buka posisi setelah N trade hari ini (0 = nonaktif)</span>
          <input type="number" min={0} max={1000} step={1} value={form.daily_max_trades} onChange={(e) => set("daily_max_trades", +e.target.value)} />
        </label>
        <label>
          Guard korelasi · <span className="sub">blok entry SEARAH bila korelasi return ≥ ini (0 = nonaktif)</span>
          <input type="number" min={0} max={1} step={0.01} value={form.corr_threshold} onChange={(e) => set("corr_threshold", +e.target.value)} />
        </label>
        <label>
          Lookback korelasi (bar) · <span className="sub">jendela hitung korelasi (&lt;20 = nonaktif)</span>
          <input type="number" min={0} max={500} step={1} value={form.corr_lookback} onChange={(e) => set("corr_lookback", +e.target.value)} />
        </label>
        <label>
          Interval refresh bot (dtk) · <span className="sub">sinyal dievaluasi per bar TF</span>
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
          Fee taker USDT-M % <span className="sub">(market)</span>
          <input type="number" min={0} step={0.001} value={form.taker_fee_pct} onChange={(e) => set("taker_fee_pct", +e.target.value)} />
        </label>
        <label>
          Fee maker USDT-M % <span className="sub">(limit)</span>
          <input type="number" min={0} step={0.001} value={form.maker_fee_pct} onChange={(e) => set("maker_fee_pct", +e.target.value)} />
        </label>
        <label>
          Fee taker USDC-M % <span className="sub">(promo ~0.04)</span>
          <input type="number" min={0} step={0.001} value={form.usdc_taker_fee_pct} onChange={(e) => set("usdc_taker_fee_pct", +e.target.value)} />
        </label>
        <label>
          Fee maker USDC-M % <span className="sub">(promo 0)</span>
          <input type="number" min={0} step={0.001} value={form.usdc_maker_fee_pct} onChange={(e) => set("usdc_maker_fee_pct", +e.target.value)} />
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
        <label style={{ gridColumn: "1 / -1", marginTop: 6, fontWeight: 600 }}>
          Penyetelan Gemini (frekuensi panggilan → hemat RPM/token)
          <span className="sub" style={{ fontWeight: 400 }}>
            {" "}· makin besar = makin jarang panggil Gemini = makin hemat
          </span>
        </label>
        <label>
          Interval keputusan (dtk) · <span className="sub">teknik gemini per simbol</span>
          <input type="number" min={30} max={3600} step={5} value={form.gemini_decide_seconds}
            onChange={(e) => set("gemini_decide_seconds", +e.target.value)} />
        </label>
        <label>
          Interval kelola posisi (dtk)
          <input type="number" min={30} max={3600} step={5} value={form.gemini_manage_seconds}
            onChange={(e) => set("gemini_manage_seconds", +e.target.value)} />
        </label>
        <label>
          Grace tahan minimal (dtk) <span className="sub">manajer tak exit dini; SL/TP tetap jaga</span>
          <input type="number" min={0} max={86400} step={30} value={form.gemini_min_hold_s}
            onChange={(e) => set("gemini_min_hold_s", +e.target.value)} />
        </label>
        <label>
          Interval review portofolio (dtk)
          <input type="number" min={60} max={3600} step={10} value={form.gemini_portfolio_seconds}
            onChange={(e) => set("gemini_portfolio_seconds", +e.target.value)} />
        </label>
        <label>
          Interval planner (jam)
          <input type="number" min={1} max={24} step={1} value={form.gemini_plan_hours}
            onChange={(e) => set("gemini_plan_hours", +e.target.value)} />
        </label>
        <label>
          Maks langkah tool-loop · <span className="sub">makin kecil = makin hemat token</span>
          <input type="number" min={1} max={8} step={1} value={form.gemini_tool_iters}
            onChange={(e) => set("gemini_tool_iters", +e.target.value)} />
        </label>
      </div>
      {adjusted.length > 0 && (
        <div className="danger">
          ⚠ Nilai tak masuk akal — engine menyesuaikan ke batas wajar:{" "}
          {adjusted.join(" · ")}
        </div>
      )}
      <button onClick={save}>Simpan pengaturan</button>{" "}
      <button onClick={resetRekomendasi} className="pg" title="Muat default teknik gemini rekomendasi (tak menyentuh modal/mode/pair)">
        Reset ke rekomendasi
      </button>
      <span className="sub">{saved}</span>
    </div>
  );
}
