# Binance USDC Bot — Dokumentasi Arsitektur Lengkap (Updated 2026-07-14)

> **Catatan**: Dokumentasi ini dibuat dari analisis penuh terhadap seluruh kodebase (70+ file Python, ~18.000 baris). Termasuk implementasi Phase 0-4 (2026-07-14) untuk memperbaiki bug kritis, membangun S/R detection, hard gates fade family, structured TP, dan pure trend-following system.

> **Patch log 2026-07-14 (sesi-2)** — bug operasional yang ditemukan & diperbaiki:
>
> | Symptom | Root cause | Fix |
> |---|---|---|
> | `/api/account` → `AttributeError: 'RuntimeSettings' object has no attribute 'gemini_enabled'` | Proses `dashboard.py` lama memuat class `RuntimeSettings` dari stale `bot/__pycache__/settings_store.cpython-313.pyc` (lebih baru dari `.py`). Source sudah berisi commit `58261fb` (penambahan `gemini_keys`/`gemini_enabled`), tapi bytecode di-cache. | Hapus `bot/__pycache__/` setelah `git pull`; restart dashboard. |
> | `/api/symbols` → `0 symbols` atau `'RuntimeSettings' object has no attribute 'credentials'` | Commit `1f26ebf` mengalihkan beberapa endpoint dari `bot.config.Settings` (yang punya `credentials()`) ke `bot.settings_store.RuntimeSettings` (yang tidak). `_get_ex()` butuh `settings.credentials()`. | Tambah method `credentials()` ke `RuntimeSettings` di `bot/settings_store.py:149` — membaca `.env` on-demand (live = key, dry/test = ""). Aman karena secret tidak disimpan di memori. |
> | `/api/symbols` cuma return 39 pair (hanya USDC-M), padahal screener memproses 74 pair USDC+USDT | Filter `settle == "USDC" and swap` di `dashboard.py:460` terlalu sempit. | Ubah filter mengikuti `Exchange.perp_symbols`: `swap, settle ∈ {USDC,USDT}, active, underlyingType == "COIN"` (eksklusi saham/komoditas tokenisasi). Hasil: 566 pair. |
> | `/api/ohlcv` → `'RuntimeSettings' object has no attribute 'raw'` | Sama dengan `/api/symbols`: `load_settings().raw["signals"]` setelah refactor 1f26ebf. `RuntimeSettings` tidak punya `.raw`; kontrak itu milik `bot.config.Settings`. | `dashboard.py:489-495` — pakai `bot.config.load_settings()` untuk akses `["signals"]`; tetap panggil `settings_store.load_settings()` dulu agar KV/runtime di-warm. Bug indent serupa di `_candle_close_watcher()` di `dashboard.py:206` juga diperbaiki. |
> | `forward.py:_write_status()` hanya menulis 1 simbol di `status["symbols"]` walaupun `self.symbols` 80+ pair | Crash indentasi di `bot/forward.py:2661`: `syms.append(...)` di-indent 8 spasi (level `for`), bukan 12 spasi (level dalam loop). Akibat: `syms.append` hanya dieksekusi sekali setelah loop dengan variabel sisa (`sym`, `pos`, `pos_view`) dari iterasi terakhir. Posisi VANRY & TRB di `self.open` tidak tertulis di status KV → `/api/positions` 0 entri, `PositionsPanel` kosong. | `bot/forward.py:2661` — kena indent 4 spasi ke kanan. Setelah fix: `_write_status` menghasilkan `len(self.symbols)` entri di `symbols[].in_position`. |
> | `PositionsPanel` tidak menampilkan kolom SL & TP, padahal user expects "close loss dan TP bagi 2 posisi" | Tabel `posCols` di `web/src/components/PositionsPanel.tsx:47-76` hanya punya Cols Pair/Arah/Qty/Margin/Entry/Mark/Liq/PnL. | Tambah col `SL` (span className="neg") & col `TP` (span className="pos") di `PositionsPanel.tsx:60-67`. |

---

## 1. ARSITEKTUR HIGH-LEVEL (Updated)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        BINANCE USDC BOT ARCHITECTURE                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────┐  │
│  │   MARKET     │───▶│  SCREENER    │───▶│  SIGNALS     │───▶│ RISK     │  │
│  │   DATA       │    │  (Layer 2)   │    │  (Layer 4)   │    │  GATE    │  │
│  │  (ccxt)      │    │  vol/spread/ │    │  trend_v8/   │    │ (Layer 5)│  │
│  └──────────────┘    │  atr filter  │    │  structure   │    │  size/SL │  │
│         │            └──────────────┘    └──────┬───────┘    │  /TP     │  │
│         ▼                     │                  │            └────┬─────┘  │
│  ┌──────────────┐            ▼                  ▼                 │       │
│  │  ALT DATA    │    ┌──────────────┐    ┌──────────────┐         ▼       │
│  │  funding/OI  │    │   BTC GATE   │    │  ROTATOR     │    ┌──────────┐  │
│  │  CVD/basis   │    │ (Mothercoin) │    │ (Layer 3)    │    │ EXECUTOR │  │
│  └──────────────┘    └──────┬───────┘    │ slots/cooldown│    │ (Layer 6)│  │
│                            │            └──────────────┘    └────┬─────┘  │
│         ┌───────────────────┘                    │                │        │
│         ▼                                        ▼                ▼        │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                    GEMINI LAYER (ReAct Agent)                        │   │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐    │   │
│  │  │ Regime      │ │ News Veto   │ │ Lessons     │ │ Devil's     │    │   │
│  │  │ Score       │ │ (real-time) │ │ Engine      │ │ Advocate    │    │   │
│  │  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘    │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│         │                                        │                          │
│         ▼                                        ▼                          │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                    FORWARD TESTER (Paper/Live)                       │   │
│  │  • Realtime loop  • Position mgmt  • SL/TP monitor  • Circuit break │   │
│  │  • VRP/MTF shadow  • Flat shadow  • State persistence (SQLite)      │   │
│  │  • S/R Level Gate  • BTC Fade Gate  • Cleanliness Gate             │   │
│  │  • Structured TP  • Partial Close  • Dynamic RR by Regime          │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│         │                                                                   │
│         ▼                                                                   │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                    RESEARCH PIPELINE (Walk-Forward OOS)              │   │
│  │  v1-trend → v2-HTF → v3-funding/OI → v4-CVD → v5-basis → v6-liq     │   │
│  │  v7-funding-primary → v8-sector → v9-lifecycle → v10-settlement →   │   │
│  │  v11-illiq → v12-carry → v13-downside-beta → v14-TSMOM → v15-VRP    │   │
│  │  v16-venue-basis → v17-spread-capture (H30) → v18-OI-crowding (H19) │   │
│  │  RESULT: 25 hipotesis diuji, 24 DITOLAK, 1 (H28) gagal langkah 3    │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**New Phase 0-4 Additions (highlighted in diagram):**
- **S/R Level Gate** (Layer 4→5): Validates fade entries against true structural levels
- **BTC Fade Gate** (Layer 4→5): Stricter BTC confirmation for fade family v2
- **Cleanliness Gate** (Layer 4→5): ADX/wick/ATR stability filter for fade family v2
- **Structured TP + Partial Close** (Layer 5→6): min(structural, 5% cap) + 75/25 split
- **Dynamic RR by Regime** (Layer 5): Trend RR~1.5, Range RR~1.2, Chaos NO ENTRY
- **Pure Trend Following** (Layer 4): Only `trend_continuation` setup, BTC as primary filter

---

## 2. LAYER ARSITEKTUR — UPDATED (7 LAYER + NEW GATES)

### Layer 4: Signal Engine — **UPDATED: `bot/signals_v8.py` (Pure Trend Following)**

**Hanya setup: `trend_continuation` (pullback complete + momentum resumes)**
**Dibunuh: `trend_pullback` (-1.25R), `range_fade`, `breakout_continuation`, `scalp_range`**

```python
# Bot/signals_v8.py:evaluate_v8()
weights: {trend: 0.40, momentum: 0.30, structure: 0.30}
entry_confidence: 0.55         # lowered from 0.62 for calibration

# Komponen skor (HANYA arah trend):
1. TREND: EMA 9/21/50 + ADX 14 → direction + strength
2. PULLBACK: RSI + MACD hist turn → pullback complete?
3. STRUCTURE: 20-bar breakout in trend direction

# BTC MACRO BIAS (primary filter, bukan blocker saja):
halving_phase: bull/post-halving → LONG bias (+20%)
              bear/blow-off → SHORT bias (+20%)
              accumulation → FLAT
BTC dump ≥2% 3-bar → SHORT bias ×1.5 (existing conviction boost)

# Dynamic SL/TP by regime:
regime=trend  (ADX≥20): sl_mult=1.75, tp_mult=2.6, RR~1.49
regime=range  (ADX<20): sl_mult=1.0,  tp_mult=1.2, RR~1.2
regime=chaos  (ATR%≥8%): NO ENTRY

# BTC Gate as PRIMARY FILTER:
btc_gate() BLOCKS counter-trend → HARD SKIP
```

**Config `config.yaml` untuk Trend Only mode:**
```yaml
strategy:
  gate_overext: true
  gate_runup: true
  gate_corr: true
  adx_range: 999          # DISABLE mean-reversion entirely
gemini:
  role: "veto"
  sideways_sniper:
    enabled: false        # MATIKAN range scalper
```

---

### Layer 4b: S/R Level Detection — **NEW: `bot/levels.py` (Phase 1)**

**Masalah**: `signals.py` lama cuma pakai 20-bar high/low + pos_in_range → tidak bedakan level "teruji" vs "kebetulan".

**Solusi**: Deteksi S/R dari timeframe lebih tinggi (default 1h) dengan lookback 200-300 candle.

```python
# Method: Time-at-price binning dengan ATR-based bin width
bin_width = 0.15 × ATR(1h)          # ~0.15-0.25% harga
lookback = 250 candles (1h)         # ~10 hari
min_touches = 15                    # threshold level valid
recency_weight = exponential_decay  # halflife=50 bars

# Level Type ditentukan CARA diuji (bukan posisi harga sekarang):
high_touches ≥ low_touches → RESISTANCE (diuji dari bawah)
low_touches  > high_touches → SUPPORT   (diuji dari atas)

# Output: Level{price, type, strength, raw_touches, H/L touches, dist_atr}
# Cache: refresh HANYA saat candle 1h baru close (cheap pre-gate)

# API:
get_valid_levels(symbol) → List[Level]
find_nearest_level(symbol, price, type, max_dist_atr) → Level | None
is_price_at_valid_level(symbol, price, side, max_dist_atr) → (bool, Level)
```

**Validasi BNB case study (TODO1.md):**
- Real level 572-574: ~40 touches → **PASS** min_touches=15
- Fake level 577: 6 touches → **REJECTED** by threshold
- Level 572-577 berada >41 hari lalu (outside current 250-bar lookback) → expected

---

### Layer 5: Risk Gate — **UPDATED with Phase 0-3 Gates**

#### Phase 0: Evidence Gate Hard Block (A1)
```python
# bot/store.py:is_setup_retired() + forward.py entry logic
# Setup RETIRED = active=0 AND n_support≥10 AND exp_r_support<0
# → HARD SKIP entry (not reduced conviction)
# Log: "evidence-gate: setup {setup_id} RETIRED (akurasi < 0.4, ≥10 pemicu)"
```

#### Phase 1: S/R Level Validity Gate (C1) — Fade Family v2
```python
# bot/forward.py:_maybe_open() untuk setup ∈ {range_fade, scalp_range, _v2}
# 0.5×ATR tolerance to valid S/R
# HARD SKIP kalau no valid level (not reduced conviction)
# Log: "S/R-gate: {setup} requires valid {S/R} within 0.5×ATR (none found)"
```

#### Phase 2: BTC Directional Confirmation (C2) — Fade Family v2
```python
# bot/altdata.py:btc_fade_confirm() — STRICTER than btc_gate()
# SHORT-at-resistance: BTC down bias (price<EMA OR EMA slope<0)
# LONG-at-support: BTC neutral/positive (NOT strong down)
# Config: btc.fade_confirm.ema_period=20, require_both=false
```

#### Phase 2: Pair Cleanliness Filter (C3) — Fade Family v2
```python
# bot/forward.py:_pair_cleanliness_check()
# 1. ADX ≤ 25 (trending = bad for fade)
# 2. Wick/body ratio ≤ 3.0 (manipulation/liq risk)
# 3. ATR CV ≤ 0.8 (std/mean = unstable volatility)
# Config: strategy.cleanliness.{max_adx, max_wick_body_ratio, max_atr_cv}
```

#### Phase 3: Structured TP for Fade v2 (D)
```python
# bot/forward.py:_open_usd() untuk fade_v2 setups
# Structural target: nearest opposite valid level
# Cap target: entry ± 5% price move (NOT 5% ROI on margin!)
# Final TP = min(structural, cap) — closer to entry wins
# Partial TP: 75% at target, 25% trailing with existing trailing_atr_mult
# _close_partial_usd() + _live_partial_close() for live mode
```

---

### Layer 4c: Phase 4 — Pure Trend Following Signal Engine

```python
# bot/signals_v8.py:evaluate_v8()
# ONLY setup: trend_continuation
# Components:
ts, td = _score_trend_v8()        # EMA alignment + ADX
ps, pd_dir = _score_pullback()    # RSI + MACD turn
ss = _score_structure_v8(td)      # Breakout in trend direction

# Combined — ONLY in trend direction:
if td == 1:  long = ts*w_t + ps*w_m + ss*w_s; short = 0
if td == -1: long = 0; short = ts*w_t + ps*w_m + ss*w_s

# BTC Macro Bias (primary filter):
btc_bias = _btc_bias(cfg)  # halving_phase + BTC ret
# boost aligned, penalize counter

# Entry threshold:
entry_conf = 0.55  # calibrated
if max_score >= entry_conf → ENTRY
else → SKIP

# Dynamic SL/TP by regime (see Layer 5)
```

---

### Layer 7: Position Manager — **UPDATED with Partial Close**

```python
# _monitor_usd(): TP hit → partial close for fade_v2
if pos.get("partial_tp_pct") and pos.get("partial_tp_price"):
    _close_partial_usd(sym, tp_price, 0.75, "tp_partial")
    pos["trailing_active"] = True
    pos["trailing_sl"] = pos["sl"]  # start trailing from original SL

# _close_partial_usd(): scale PnL, reduce qty/bet proportionally
# _live_partial_close(): reduceOnly market order for live mode
```

---

## 3. GEMINI LAYER — UPDATED

### Evidence-Gate Integration
- `store.is_setup_retired()` called from forward.py before entry
- Lessons that fail evidence-gate (active=0, n≥10, exp_R<0) → HARD BLOCK
- Re-eval rule: document periodic re-evaluation (every 50 trades/weekly)

### Halving Boost Conditioning (A3)
```python
# forward.py:2268-2292
# Boost ONLY if setup exp_R ≥ -0.02 (tolerance noise)
# scalp_range (-0.590), range_fade (-0.124), trend_pullback (-0.026) → ZERO boost
```

---

## 4. FORWARD TESTER — UPDATED

### New Entry Flow (`_maybe_open()`):
```
1. Pre-gates (OFF, news, VRP, DD, CB, slot, position, ATR, price cache)
2. CORR conflict check
3. Planner enforce
4. Evidence-gate (A1): is_setup_retired() → BLOCK
5. S/R Gate (C1): fade_v2 needs valid level within 0.5×ATR
6. BTC Fade Gate (C2): btc_fade_confirm() → BLOCK if no bias
7. Cleanliness Gate (C3): _pair_cleanliness_check() → BLOCK
8. If all pass → _open_usd()
```

### TP Logic in `_open_usd()`:
```python
# Fade v2 (range_fade_v2, scalp_range_v2):
structural_tp = opposite valid level (from bot.levels)
cap_tp = entry ± 5% price move
tp = min(structural_tp, cap_tp) by distance to entry
partial_tp_pct = 0.75, partial_tp_price = tp

# Other setups: ATR-based TP logic unchanged (micro-TP, user TP%, ATR TP)
```

---

## 5. RESEARCH PIPELINE — STATUS

**25 hipotesis diuji → 24 DITOLAK → 1 (H30) gagal langkah 3**

| Phase | Hipotesis | Status |
|-------|-----------|--------|
| 1 | v1-v7 (trend, HTF, funding, CVD, basis, liq cascade, funding regime) | REJECTED |
| 2 | Cross-sectional (mom, rev, carry, skew, vol, BAB, stat-arb, combiner) | REJECTED |
| 3 | H13 sector lead-lag, H14 listing-age | REJECTED |
| 4 | H24-H32 (settlement, carry×mom, illiq, VRP, venue basis, downside-beta, TSMOM) | REJECTED |
| 5 | H30 spread capture (FIL +3.3bps → Step 3 -7 to -11bps) | **REJECTED** |
|     | H19 OI crowding | REJECTED |

**H28 VRP**: Lolos awal (p=0.036) → gagal replikasi 1800d (p=0.336) → REJECTED

**Kesimpulan**: Ruang prediksi arah direksional di Binance USDC perp untuk retail — HABIS.

---

## 6. STATE MANAGEMENT — UPDATED

### SQLite Store (`bot/store.py`)
- **New function**: `is_setup_retired(setup, mode)` — hard gate check
- **Tables unchanged** (mode isolation already implemented in Tahap 0)

### Runtime Settings (`bot/settings_store.py`)
- Per-mode buckets already implemented (Tahap 1 balance split)
- Phase 0-4 config loaded via `RuntimeSettings` from SQLite

---

## 7. RISK MANAGEMENT — UPDATED

### Level 3: Dynamic RR by Regime
```python
regime=trend  (ADX≥20): sl=1.75×ATR, tp=2.6×ATR, RR=1.49, aggressive trailing
regime=range  (ADX<20): sl=1.0×ATR,  tp=1.2×ATR,  RR=1.2,  micro-TP, forced exit 3 bar
regime=chaos  (ATR%≥8%): NO ENTRY
```

### Phase 3: Structured TP + Partial Close (Fade v2)
```python
# TP = min(nearest opposite level, entry±5%)
# 75% close at TP → remaining 25% trails from original SL
# _close_partial_usd() handles proportional PnL/qty/bet
```

---

## 8. AGENT AUTONOMI — UNCHANGED

Existing flags still apply. Phase 4 config disables tool_loop/autonomous for pure trend mode.

---

## 9. SHADOW GATES — UNCHANGED

All running as SHADOW (measure only):
- VRP Brake (H28): exp_R brake-on vs off
- MTF Agreement: win_rate agree vs disagree + Brier
- Flat Shadow: miss_rate per regime/conviction
- A/B ReAct: exp_R rules vs rules+ReAct

**Enforce only after**: p_adj < 0.05 AND positive evidence

---

## 10. DASHBOARD — UPDATED

### FastAPI (`bot/dashboard.py`)
- SSE `candle` event now includes `emas` + `rsi` for high-TF (1h/1d/1w/1M)
- Frontend (`web/src/components/PriceChart.tsx`) updates EMA/RSI via SSE
- New endpoints: `/api/ohlcv` (with EMA/RSI), `/api/candles` (1w/1M support)

### Frontend (`web/src/`)
- React/Vite, Lightweight Charts
- Timeframes: 1m/5m/15m/30m/1h/2h/4h/1d/1w/1M
- SSE subscribe for high-TF (no polling)
- Partial TP indicator in position panel (planned)

---

## 11. ENTRY POINTS & CLI

| Script | Fungsi |
|--------|--------|
| `forwardtest.py` | Paper forward test — `python forwardtest.py --poll 30 --use-store` |
| `dashboard.py` | Web monitor → http://localhost:8000 |
| `optimize.py` | Walk-forward sweep + co-pilot Gemini |
| `backtest.py` | Event-driven backtest (fee+slippage jujur) |
| `regime_ev.py` | Expectancy per regime |
| `sl_calibrate.py` | SL floor calibration (1yr × 11 pair) |
| `h30_hist.py` / `h30_sim.py` | H30 spread capture replay |
| `chart_ingest.py` | Fill chartstore (1w/1M pagination) |
| `l2collect.py` | L2 orderbook collection (RUNNING) |

---

## 12. STRATEGI PROFIT KONSISTEN — CURRENT STATE

### Sudah Terimplementasi (Ready for Paper Test):

1. **BTC Mother Coin Asymmetry** (Edge Struktural)
   - `btc_gate()` + `btc_fade_confirm()` + conviction boost
   - BTC dump ≥2% → alt beta>1 → SHORT alt

2. **Pure Trend Following** (Halving Cycle Awareness)
   - `signals_v8.py` — only `trend_continuation`
   - Halving phase macro bias (bull→LONG, bear→SHORT)

3. **Risk/Exit Mastery** (Kontribusi > Entry)
   - SL Floor 1.75×ATR (calibrated)
   - Give-back trigger (≥50%→TP then retraced ≥15pp)
   - Partial TP 75/25 for fade v2
   - Dynamic RR by regime

4. **Fade Family v2 Hard Gates** (Phase 0-3)
   - Evidence-gate block (retired setups)
   - S/R proximity gate (0.5×ATR)
   - BTC directional confirm
   - Cleanliness filter (ADX/wick/ATR)

---

## 13. FILE KUNCI — UPDATED

| Tujuan | File | Fungsi |
|--------|------|--------|
| **Signal Engine v8** | `bot/signals_v8.py:evaluate_v8()` | Pure trend following, BTC macro, dynamic RR |
| **S/R Detection** | `bot/levels.py:get_valid_levels()` | Time-at-price binning, 1h TF, recency weighting |
| **Evidence Gate** | `bot/store.py:is_setup_retired()` | Hard block retired setups |
| **S/R Gate** | `bot/forward.py` (entry logic) | Fade v2 needs valid level 0.5×ATR |
| **BTC Fade Gate** | `bot/altdata.py:btc_fade_confirm()` | Stricter BTC bias for fade |
| **Cleanliness Gate** | `bot/forward.py:_pair_cleanliness_check()` | ADX/wick/ATR filter |
| **Structured TP** | `bot/forward.py:_open_usd()` | min(structural, 5% cap) + 75/25 |
| **Partial Close** | `bot/forward.py:_close_partial_usd()` | Proportional PnL/qty/bet |
| **Halving Boost** | `bot/forward.py:2268-2292` | exp_R ≥ -0.02 gate |
| **Dynamic RR** | `bot/signals_v8.py:evaluate_v8()` | Regime-adaptive SL/TP |
| **Walk-forward** | `bot/optimize.py:run_walk()` | Train → OOS → verdict |
| **Settings** | `bot/settings_store.py` | Per-mode hot-reload |
| **Balance** | `bot/forward.py:balance_usd` (property) | Per-wallet USDC/USDT |
| **SSE Candle** | `bot/dashboard.py:_candle_close_watcher()` | 1h/1d/1w/1M real-time |
| **Charts** | `bot/chartstore.py:ingest()` | 1w/1M pagination ≤10yr |

---

## 14. LIVE READINESS CHECKLIST (NON-NEGOTIABLE)

```
[ ] OOS exp_R > +0.05R DAN ≥3 window positif DAN ≥3 simbol konsisten
[ ] Lolos signifikansi statistik (bootstrap Bonferroni + effective-n ≥ 30)
[ ] Parameter stabil ≥50% antar-window
[ ] Cost-stress 2× (fee+slippage lipat) → edge bertahan
[ ] Lockbox (holdout 20% → single test) → bertahan
[ ] Paper forward-test data live parameter TETAP berhari-hari
[ ] Micro-live (modal sangat kecil) → tetap positif
[ ] Naikkan ukuran perlahan bila tetap positif
```

**JIKA SATU SATU GAGAL → KEMBALI KE FLAT / RISET. JANGAN LIVE-KAN.**

---

## 15. KESIMPULAN & SARAN (2026-07-14)

**Arsitektur sudah matang** — infrastructure lengkap, 25 hipotesis diuji jujur, bug kritis diperbaiki.

### Implementasi Baru (Phase 0-4):
1. ✅ **Bug fixes**: Evidence gate hard block, halving boost gating
2. ✅ **S/R Level Detection**: True structural levels via time-at-price binning
3. ✅ **Fade Family v2 Hard Gates**: S/R proximity + BTC confirm + Cleanliness
4. ✅ **Structured TP + Partial Close**: min(level, 5% cap) + 75/25 trailing
5. ✅ **Pure Trend Following**: `signals_v8.py` — only trend_continuation, BTC primary filter

### Rekomendasi:
1. **JALANKAN PAPER TEST SEKARANG**: `python dashboard.py` + `python forwardtest.py --poll 30 --use-store`
2. **AMATI SINYAL**: Hanya `trend_continuation` entry, BTC-aligned direction
3. **EVALUASI 2-4 MINGGU**: OOS exp_R, win rate, max DD, parameter stability
4. **H30 L2 COLLECTION**: Biarkan `l2collect.py` jalan → 3 bulan data → evaluasi spread capture

### Jika SEMUA GAGAL:
- Pivot ke market making / spread capture (butuh H30 success)
- Atau shutdown — "no edge" = kesimpulan jujur & bermartabat
- Infrastruktur (dashboard, risk engine, data pipeline, research framework) tetap reusable

---

## 16. UPDATE LOG

| Tanggal | Perubahan |
|---------|-----------|
| 2026-07-13 | Arsitektur dasar (7 layer, Gemini, Forward, Research, Dashboard) |
| 2026-07-14 | **Phase 0-4 Implementation**: |
| | - Evidence gate hard block (`store.is_setup_retired`) |
| | - Halving boost conditioning (exp_R gate) |
| | - `bot/levels.py`: S/R detection via time-at-price binning |
| | - Fade v2 gates: S/R proximity, BTC fade confirm, Cleanliness |
| | - Structured TP + 75/25 partial close for fade v2 |
| | - `bot/signals_v8.py`: Pure trend following engine |
| | - Config "Trend Only" mode (adx_range=999, gates ON) |
| | - Dynamic RR by regime (trend/range/chaos) |

---

*Dokumentasi ini dihasilkan dari analisis penuh 70+ file Python (total ~18.000 baris) pada 2026-07-14. Setiap referensi baris kode (`file.py:line`) dapat diverifikasi langsung.*

*All Phase 0-4 implementations verified: compiles ✅, web build ✅, integration tests ✅*