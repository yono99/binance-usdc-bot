import { useEffect, useState } from "react";
import { api } from "../api";
import type { Account, Settings, Status } from "../types";
import {
  NUM_FIELDS,
  PersonalSettings,
  REKOMENDASI_GEMINI,
  ServerSettings,
  liqPct,
  riskWarn,
  toSettingsForm,
  type SettingsForm,
} from "./settings";

export function ControlPanel({
  status,
  available,
  account,
}: {
  status: Status | null;
  available: string[];
  account: Account | null;
}) {
  const isLive = status?.mode === "live"; // mode efektif yang BERJALAN
  const [s, setS] = useState<Settings | null>(null);
  const [form, setForm] = useState<SettingsForm | null>(null);
  const [saved, setSaved] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [adjusted, setAdjusted] = useState<string[]>([]);
  const [lastManual, setLastManual] = useState(5);

  // Seed form balances dari ledger hidup (status paper / account live),
  // BUKAN dari runtime settings (nilai frozen seed — bisa basi vs PnL).
  const liveUsdt = isLive ? account?.balance_usdt : status?.balance_usdt;
  const liveUsdc = isLive ? account?.balance_usdc : status?.balance_usdc;

  useEffect(() => {
    api.settings().then((d) => {
      setS(d);
      // Seed config saja; balance_* di-overwrite effect sync di bawah
      // (hindari closure basi: status sering tiba sebelum/sesudah settings).
      setForm(toSettingsForm(d));
    });
    api.geminiModels().then((r) => setModels(r.models));
  }, []);

  // Form Saldo = ledger hidup (sama sumber dengan bar Status / equity).
  // LIVE: Binance Futures. Paper: status bot (naik/turun via PnL).
  // POST /api/settings sengaja pop balance_* (anti-overwrite PnL) → field read-only.
  // formReady = form sudah di-seed → re-run juga saat settings load selesai
  // (status bisa sudah ada lebih dulu; dulu effect cuma depend balance → race).
  const formReady = form != null;
  useEffect(() => {
    if (!formReady) return;
    const usdt = liveUsdt;
    const usdc = liveUsdc;
    setForm((p) => {
      if (!p) return p;
      let next = p;
      if (usdt != null && Math.abs(usdt - p.balance_usdt) > 1e-9)
        next = { ...next, balance_usdt: usdt };
      if (usdc != null && Math.abs(usdc - p.balance_usdc) > 1e-9)
        next = { ...next, balance_usdc: usdc };
      return next;
    });
  }, [formReady, liveUsdt, liveUsdc]);

  if (!s || !form)
    return (
      <div className="panel">
        <h2>Bot control</h2>
        <div className="empty">memuat…</div>
      </div>
    );

  const set = <K extends keyof SettingsForm>(k: K, v: SettingsForm[K]) =>
    setForm((p) => (p ? { ...p, [k]: v } : p));

  const resetRekomendasi = () => {
    if (
      !confirm(
        "Muat default teknik gemini rekomendasi?\n\nMengubah: teknik, guard korelasi, throttle Gemini, breaker harian, jenis order & fee.\nTIDAK menyentuh: saldo, mode, leverage, bet, pair, model.\n\nNilai hanya DIMUAT ke form — tekan Simpan untuk menerapkan.",
      )
    )
      return;
    setForm((p) => (p ? { ...p, ...REKOMENDASI_GEMINI } : p));
    setSaved(" rekomendasi dimuat — tekan Simpan untuk menerapkan");
    setTimeout(() => setSaved(""), 6000);
  };
  const warn = riskWarn(form.leverage, +liqPct(form.leverage).toFixed(3));

  const setMode = async (m: string) => {
    if (m === "live") {
      if (
        !confirm(
          "⚠ AKTIFKAN MODE LIVE — UANG NYATA?\n\nBot akan menempatkan order ASLI di Binance Futures memakai API key live kamu. Pastikan: key Futures-only + withdrawal OFF + IP-locked, dan mulai dengan bet SANGAT KECIL. Methodology: strategi ini belum ada edge (impas).\n\nLanjut?",
        )
      )
        return;
      if (
        !confirm(
          "Konfirmasi sekali lagi — ini UANG NYATA. Yakin mengaktifkan LIVE?",
        )
      )
        return;
    }
    // persist pilihan mode ke backend (tanpa ini, refresh kembali ke mode aktif lama
    // dan Simpan menulis ke bucket mode yang salah), lalu muat setting milik mode itu
    try {
      await api.setMode(m);
      const d = await api.settings(m);
      setS(d);
      // Mode switch: seed config mode baru; balance tetap dari ledger hidup bila ada.
      setForm(
        toSettingsForm(d, {
          usdt: liveUsdt ?? d.balance_usdt,
          usdc: liveUsdc ?? d.balance_usdc,
        }),
      );
    } catch {
      set("mode", m);
    }
  };

  const save = async () => {
    // Jangan kirim balance_* — backend pop() anti-overwrite PnL ledger.
    // Field form hanya mirror status/account (read-only di UI).
    const {
      balance_usdt: _bu,
      balance_usdc: _bc,
      ...payload
    } = form as SettingsForm & Record<string, unknown>;
    void _bu;
    void _bc;
    const sent = form;
    const res = await api.saveSettings(payload as Record<string, unknown>);
    const adj: string[] = [];
    for (const [k, label] of NUM_FIELDS) {
      const a = sent[k] as number;
      const b = res[k] as unknown as number;
      if (typeof a === "number" && typeof b === "number" && Math.abs(a - b) > 1e-9)
        adj.push(`${label}: ${a} → ${b}`);
    }
    setS((prev) => (prev ? { ...prev, ...res } : res)); // merge, jaga 'techniques'
    // pakai nilai hasil clamp engine (kalau user input ngawur, ikut engine).
    // balance_* tetap dari liveUsdt/liveUsdc (effect sync) — jangan ambil seed settings.
    setForm((p) =>
      p
        ? {
            ...p,
            leverage: res.leverage,
            bet_usd: res.bet_usd,
            bet_pct: res.bet_pct ?? 0,
            target_profit_pct: res.target_profit_pct,
            max_open_positions: res.max_open_positions,
            poll_seconds: res.poll_seconds,
            max_drawdown_pct: res.max_drawdown_pct ?? p.max_drawdown_pct,
            daily_max_trades: res.daily_max_trades,
            corr_threshold: res.corr_threshold ?? p.corr_threshold,
            corr_lookback: res.corr_lookback ?? p.corr_lookback,
            taker_fee_pct: res.taker_fee_pct,
            maker_fee_pct: res.maker_fee_pct,
            usdc_taker_fee_pct: res.usdc_taker_fee_pct,
            usdc_maker_fee_pct: res.usdc_maker_fee_pct,
            gemini_decide_seconds:
              res.gemini_decide_seconds ?? p.gemini_decide_seconds,
            gemini_manage_seconds:
              res.gemini_manage_seconds ?? p.gemini_manage_seconds,
            gemini_min_hold_s: res.gemini_min_hold_s ?? p.gemini_min_hold_s,
            gemini_portfolio_seconds:
              res.gemini_portfolio_seconds ?? p.gemini_portfolio_seconds,
            gemini_plan_hours: res.gemini_plan_hours ?? p.gemini_plan_hours,
            gemini_tool_iters: res.gemini_tool_iters ?? p.gemini_tool_iters,
            balance_usdt: liveUsdt ?? p.balance_usdt,
            balance_usdc: liveUsdc ?? p.balance_usdc,
          }
        : p,
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
          ⚠ <b>MODE LIVE AKTIF — UANG NYATA.</b> Order ditempatkan ASLI di
          Binance Futures. Circuit breaker &amp; guard tetap aktif, tapi risiko
          penuh milikmu. Saldo diambil dari akun live.
        </div>
      )}
      {form.mode === "live" && !isLive && (
        <div className="danger">
          ⚠ Mode <b>live</b> dipilih — berlaku setelah <b>Simpan</b> &amp; bot
          beralih (butuh BINANCE_LIVE_KEY/SECRET di .env). Bila key tak ada, bot
          tetap paper.
        </div>
      )}
      {warn && <div className="danger">{warn}</div>}

      {/* Mode switch — lintas section (memilih bucket settings) */}
      <div className="settings-mode-bar">
        <label>
          Mode
          <select value={form.mode} onChange={(e) => setMode(e.target.value)}>
            <option value="">ikut .env</option>
            <option value="dry">dry (paper)</option>
            <option value="test">test (paper)</option>
            <option value="live">live (UANG NYATA)</option>
          </select>
        </label>
        <span className="sub">
          Mode memilih bucket settings (dry / test / live). Simpan menulis ke
          bucket yang dipilih.
        </span>
      </div>

      <div className="settings-split">
        <PersonalSettings
          form={form}
          set={set}
          techniques={s.techniques || []}
          available={available}
          isLive={isLive}
          lastManual={lastManual}
          setLastManual={setLastManual}
        />
        <ServerSettings
          form={form}
          set={set}
          models={models}
          timeframe={s.timeframe}
        />
      </div>

      {adjusted.length > 0 && (
        <div className="danger">
          ⚠ Nilai tak masuk akal — engine menyesuaikan ke batas wajar:{" "}
          {adjusted.join(" · ")}
        </div>
      )}
      <div className="settings-actions">
        <button onClick={save}>Simpan pengaturan</button>{" "}
        <button
          onClick={resetRekomendasi}
          className="pg"
          title="Muat default teknik gemini rekomendasi (tak menyentuh modal/mode/pair)"
        >
          Reset ke rekomendasi
        </button>
        <span className="sub">{saved}</span>
      </div>
    </div>
  );
}
