import { PairPicker } from "../PairPicker";
import type { FormSetter, SettingsForm } from "./settingsForm";

/**
 * Setting pribadi — risk, sizing, order, pair, limit trade.
 * Bukan knob server (timeframe, model, interval Gemini).
 */
export function PersonalSettings({
  form,
  set,
  techniques,
  available,
  isLive,
  lastManual,
  setLastManual,
}: {
  form: SettingsForm;
  set: FormSetter;
  techniques: string[];
  available: string[];
  isLive: boolean;
  lastManual: number;
  setLastManual: (v: number) => void;
}) {
  return (
    <section className="settings-section">
      <header className="settings-section-head">
        <h3>Setting pribadi</h3>
        <span className="sub">
          Risk, sizing, order, pair — pilihan trading kamu (bukan config server)
        </span>
      </header>

      <div className="settings-group">
        <div className="settings-group-title">Mode &amp; teknik</div>
        <div className="grid">
          <label>
            Status
            <select
              value={String(form.enabled)}
              onChange={(e) => set("enabled", e.target.value === "true")}
            >
              <option value="false">OFF</option>
              <option value="true">ON (buka posisi)</option>
            </select>
          </label>
          <label>
            Teknik
            <select
              value={form.technique}
              onChange={(e) => set("technique", e.target.value)}
            >
              {techniques.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label style={{ gridColumn: "1 / -1" }}>
            Pair (multi — cari &amp; tambah) ·{" "}
            <span className="sub">
              kosong = screening SEMUA pair USDC ({available.length})
            </span>
            <PairPicker
              value={form.symbols}
              options={available}
              onChange={(v) => set("symbols", v)}
            />
          </label>
        </div>
      </div>

      <div className="settings-group">
        <div className="settings-group-title">Sizing &amp; modal</div>
        <div className="grid">
          <label>
            Leverage (x)
            <input
              type="number"
              min={1}
              max={125}
              value={form.leverage}
              onChange={(e) => set("leverage", +e.target.value)}
            />
          </label>
          <label>
            Bet / margin (USD) ·{" "}
            <span className="sub">
              {form.bet_pct > 0 ? "diabaikan (pakai % saldo)" : "margin tetap"}
            </span>
            <input
              type="number"
              min={0.01}
              step={0.01}
              value={form.bet_usd}
              disabled={form.bet_pct > 0}
              onChange={(e) => set("bet_usd", +e.target.value)}
            />
          </label>
          <label>
            Bet % saldo (adaptif) ·{" "}
            <span className="sub">
              0 = pakai margin tetap · &gt;0 = margin auto-scale saat modal tumbuh
            </span>
            <input
              type="number"
              min={0}
              max={100}
              step={0.5}
              value={form.bet_pct}
              onChange={(e) => set("bet_pct", +e.target.value)}
            />
          </label>
          <label style={{ gridColumn: "1 / -1" }}>
            Saldo per-wallet —{" "}
            <span className="sub">
              {isLive
                ? "LIVE: dari Binance Futures — read-only · "
                : "paper: dari status bot (ikut PnL) — read-only · "}
              sinkron dengan bar Status / equity · USDT (USDT-M) &amp; USDC (USDC-M)
              terpisah
            </span>
            <div style={{ display: "flex", gap: 12, marginTop: 4 }}>
              <label style={{ flex: 1 }}>
                USDT
                <input
                  type="number"
                  min={0}
                  step={0.01}
                  value={form.balance_usdt}
                  readOnly
                  disabled
                  title={
                    isLive
                      ? "Saldo USDT dari Binance Futures (ledger live)"
                      : "Saldo USDT paper dari status bot — berubah otomatis via PnL"
                  }
                />
              </label>
              <label style={{ flex: 1 }}>
                USDC
                <input
                  type="number"
                  min={0}
                  step={0.01}
                  value={form.balance_usdc}
                  readOnly
                  disabled
                  title={
                    isLive
                      ? "Saldo USDC dari Binance Futures (ledger live)"
                      : "Saldo USDC paper dari status bot — berubah otomatis via PnL"
                  }
                />
              </label>
            </div>
          </label>
        </div>
      </div>

      <div className="settings-group">
        <div className="settings-group-title">Target &amp; risk lock</div>
        <div className="grid">
          <label>
            Target profit ·{" "}
            <span className="sub">
              Auto = engine tentukan per-pair (volatilitas/ATR)
            </span>
            <select
              value={form.target_profit_pct > 0 ? "manual" : "auto"}
              onChange={(e) =>
                set(
                  "target_profit_pct",
                  e.target.value === "auto" ? 0 : lastManual || 5,
                )
              }
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
            <input
              type="number"
              min={1}
              max={20}
              step={1}
              value={form.max_open_positions}
              onChange={(e) => set("max_open_positions", +e.target.value)}
            />
          </label>
          <label>
            Drawdown lock % ·{" "}
            <span className="sub">
              kunci entry bila equity turun ≥ % dari puncak (kumulatif, 0 =
              nonaktif) · lepas hanya via tombol Reset di Status
            </span>
            <input
              type="number"
              min={0}
              max={90}
              step={0.5}
              value={form.max_drawdown_pct}
              onChange={(e) => set("max_drawdown_pct", +e.target.value)}
            />
          </label>
          <label>
            Max trade harian ·{" "}
            <span className="sub">
              stop buka posisi setelah N trade hari ini (0 = nonaktif)
            </span>
            <input
              type="number"
              min={0}
              max={1000}
              step={1}
              value={form.daily_max_trades}
              onChange={(e) => set("daily_max_trades", +e.target.value)}
            />
          </label>
          <label>
            Guard korelasi ·{" "}
            <span className="sub">
              blok entry SEARAH bila korelasi return ≥ ini (0 = nonaktif)
            </span>
            <input
              type="number"
              min={0}
              max={1}
              step={0.01}
              value={form.corr_threshold}
              onChange={(e) => set("corr_threshold", +e.target.value)}
            />
          </label>
          <label>
            Lookback korelasi (bar) ·{" "}
            <span className="sub">
              jendela hitung korelasi (&lt;20 = nonaktif)
            </span>
            <input
              type="number"
              min={0}
              max={500}
              step={1}
              value={form.corr_lookback}
              onChange={(e) => set("corr_lookback", +e.target.value)}
            />
          </label>
        </div>
      </div>

      <div className="settings-group">
        <div className="settings-group-title">Order &amp; fee</div>
        <div className="grid">
          <label>
            Jenis order
            <select
              value={form.order_type}
              onChange={(e) => set("order_type", e.target.value)}
            >
              <option value="limit">limit (maker)</option>
              <option value="market">market (taker)</option>
            </select>
          </label>
          <label>
            Fee taker USDT-M % <span className="sub">(market)</span>
            <input
              type="number"
              min={0}
              step={0.001}
              value={form.taker_fee_pct}
              onChange={(e) => set("taker_fee_pct", +e.target.value)}
            />
          </label>
          <label>
            Fee maker USDT-M % <span className="sub">(limit)</span>
            <input
              type="number"
              min={0}
              step={0.001}
              value={form.maker_fee_pct}
              onChange={(e) => set("maker_fee_pct", +e.target.value)}
            />
          </label>
          <label>
            Fee taker USDC-M % <span className="sub">(promo ~0.04)</span>
            <input
              type="number"
              min={0}
              step={0.001}
              value={form.usdc_taker_fee_pct}
              onChange={(e) => set("usdc_taker_fee_pct", +e.target.value)}
            />
          </label>
          <label>
            Fee maker USDC-M % <span className="sub">(promo 0)</span>
            <input
              type="number"
              min={0}
              step={0.001}
              value={form.usdc_maker_fee_pct}
              onChange={(e) => set("usdc_maker_fee_pct", +e.target.value)}
            />
          </label>
        </div>
      </div>
    </section>
  );
}
