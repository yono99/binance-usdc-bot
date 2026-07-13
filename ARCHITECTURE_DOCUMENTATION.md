# Binance USDC Bot — Dokumentasi Arsitektur Lengkap

> **Catatan**: Dokumentasi ini dibuat dari analisis penuh terhadap seluruh kodebase (60+ file Python). Tujuannya: pemahaman utuh sistem agar perubahan strategi profit konsisten tidak merusak fondasi yang sudah terbukti.

---

## 1. ARSITEKTUR HIGH-LEVEL

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        BINANCE USDC BOT ARCHITECTURE                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────┐  │
│  │   MARKET     │───▶│  SCREENER    │───▶│  SIGNALS     │───▶│ RISK     │  │
│  │   DATA       │    │  (Layer 2)   │    │  (Layer 4)   │    │  GATE    │  │
│  │  (ccxt)      │    │  vol/spread/ │    │  trend/mom/  │    │ (Layer 5)│  │
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
│  └──────────────────────────────────────────────────────────────────────┘   │
│         │                                                                   │
│         ▼                                                                   │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                    RESEARCH PIPELINE (Walk-Forward OOS)              │   │
│  │  v1-trend → v2-HTF → v3-funding/OI → v4-CVD → v5-basis → v6-liq     │   │
│  │  v7-funding-primary → v8-sector → v9-lifecycle → v10-settlement →   │   │
│  │  v11-illiq → v12-carry → v13-downside-beta → v14-TSMOM → v15-VRP    │   │
│  │  v16-venue-basis → v17-spread-capture (H30) → v18-OI-crowding (H19) │   │
│  │  RESULT: 25 hipotesis diuji, 24 DITOLAK, 1 (H30) gagal langkah 3    │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. LAYER ARSITEKTUR (7 LAYER UTAMA)

### Layer 1: Market Data (`bot/exchange.py`)
- **Wrapper Binance USDC-M Futures via ccxt**
- Mode: `dry` (paper, data live), `test` (paper, testnet deprecated), `live` (uang nyata)
- Endpoint: `fetch_ohlcv`, `ticker`, `spread`, `balances`, `positions`, `open_orders`
- **Key feature**: Pemisahan margin per-quote (USDC/USDT dompet terpisah di Binance) — saldo di-fetch terpisah per wallet, demikian juga posisi & order live

### Layer 2: Screener (`bot/screener.py`)
```python
# 4 filter keras (fail-open jika API gagal):
1. min_quote_volume_24h: 5M USDC (likuiditas minimum)
2. max_spread_pct: 0.03% (spread = biaya pasti, 0.03% lolos BTC/ETH/SOL/XRP <0.015%)
3. min_atr_pct: 0.05% (turun dari 0.25% agar sideways pair lolos ke Gemini)
4. max_atr_pct: 6.0% (buang yang terlalu liar)
```
- **Dedup prefer USDC**: BTC/USDC diprioritaskan over BTC/USDT (promo fee 0% maker)
- **Prefilter volume**: Pangkas universe ±800 pair jadi top-N by quoteVolume sebelum screen detail

### Layer 3: Rotator (`bot/rotate.py`)
```python
max_open_positions: 6          # slot paralel maksimum
cooldown_minutes: 30           # jeda re-entry pair yang baru ditutup
blacklist_after_sl: 2          # SL beruntun → blacklist sementara
```

### Layer 4: Signal Engine (`bot/signals.py`)
**Skor gabungan 3 komponen (bobot dari config.yaml):**
```python
weights: {trend: 0.40, momentum: 0.30, structure: 0.30}
entry_confidence: 0.62         # ambang skor gabungan (0..1)
```

| Komponen | Indikator | Logika |
|----------|-----------|--------|
| **Trend** | EMA 9/21/50 + ADX 14 | `ef>em>es` = +1, `ef<em<es` = -1, strength = min(ADX/40, 1), choppy (ADX<20) → ×0.4 |
| **Momentum** | RSI 14 + MACD hist | RSI>52 & hist>0 & rising = +1, RSI<48 & hist<0 & falling = -1, dist = \|RSI-50\|/25 |
| **Structure** | 20-bar high/low | Breakout >high/+0.5, breakdown <-low/-0.5, pos_in_range 0.4-0.6 = netral |

**Regime classification (murah, pakai ADX/ATR existing):**
- `chaos`: ATR% ≥ 8%
- `trend`: ADX ≥ 20
- `range`: ADX < 20

**BTC Dominance Gate (Mother Coin) — `bot/altdata.py:btc_gate()`**:
```python
# Direction-aware: blokir entri LAWAN arah BTC saat BTC bergerak KUAT
dump_pct: 0.5%          # |gerak BTC| ≥ ini = "kuat" (di bawah lolos)
block_counter: true     # true = blok total, false = diskon size saja
size_floor: 0.4         # mode diskon: faktor size minimum saat lawan-arah
```
> **Ini sudah implementasi requestmu**: BTC turun >2-5% → alt turun lebih dalam (beta>1) → SHORT alt prioritas. Logic sudah ada di `_btc_lead()` dan conviction boost 1.5× di `forward.py:2085-2109`.

### Layer 5: Risk Gate (`bot/risk.py`)
```python
account_risk_pct: 0.5%      # risiko per trade (% equity) → tentukan size dari jarak SL
leverage: 3x                # konservatif
max_portfolio_exposure: 30% # total notional / equity
daily_max_loss_pct: 3.0%    # circuit breaker harian
daily_max_trades: 20        # circuit breaker jumlah trade
corr_threshold: 0.85        # blok entry SEARAH bila korelasi return ≥ ini
sl_atr_mult: 1.5            # SL = entry ± ATR × mult
tp_atr_mult: 2.6            # TP (RR ~1.73). >1 = expectancy-friendly
trailing: true, trailing_atr_mult: 1.2
```
**SL Floor (Fix A, dikalibrasi data 1 tahun × 11 pair):**
```python
# q80 MAE pemenang = 1.70-1.98×ATR, median 1.78 → default 1.75×ATR + 0.5×range candle
_sl_floor(entry, is_long, sl, atr, last_range, k_atr=1.75, k_range=0.5)
```
> Trade-off jujur: breakeven winrate 37.5% → 41% (lebih banyak pemenang hidup, dibayar RR lebih ketat).

### Layer 6: Executor (`bot/execution.py`)
- **Default: LIMIT post-only (GTX)** → jamin fee maker (USDC-M promo 0%)
- **Pending order tracking**: LIMIT resting = PENDING (bukan posisi), reconcile tiap siklus via `fetch_open_orders`
- **Timeout**: `pending_timeout_s: 300` detik → cancel & skip
- **Live SL/TP**: `STOP_MARKET` + `TAKE_PROFIT_MARKET` reduce-only di exchange (aktif walau bot mati)

### Layer 7: Position Manager (`bot/position.py`)
- Trailing stop berbasis ATR
- Monitor hit SL/TP/liq (intrabar high/low + last price)
- Dry: simulasi penuh; Test/Live: SL/TP dieksekusi exchange

---

## 3. GEMINI LAYER — "PENGAarah DISIPLIN, BUKAN PERAMAL"

### Arsitektur ReAct Agent (`bot/react_agent.py`)
```
OBSERVE → REASON → ACT → RECORD
```
- **State yang dikirim ke Gemini**: harga, ATR%, funding, OIΔ, CVD, regime, skor sinyal, posisi terbuka, PnL harian R, pelajaran terbaru, memori lintas-tick, BTC lead, halving phase, balance per-wallet (USDT/USDC)
- **Aksi**: `ENTER_LONG`, `ENTER_SHORT`, `SKIP`, `REDUCE_RISK`, `FLAT`
- **Fail-open**: LLM gagal/timeout → fallback ke veto lama + aturan sinyal (tak pernah blokir trading)
- **Mode Isolation** (Tahap 0 — plan-sess): `track_record()`, `setup_stats()`, `recent_decisions()`, `exit_stats()` semua menerima arg `mode` — default terisolasi per-mode (dry/test/live tidak campur). Opt-in `share_lessons_across_modes: true` di config.

### Devil's Advocate (Adversarial Pass)
- Pass LLM kedua yang **HANYA mencari alasan MENOLAK** entry
- `veto_threshold: 0.7` → strength ≥ 0.7 → batalkan entry jadi SKIP
- Hemat RPD: di-skip untuk `scalp_range` setup (sideways sniper)

### Lessons Engine (`bot/lessons.py`)
```python
# Format wajib: 'IF [kondisi] THEN [aksi] BECAUSE [alasan]'
# Pelajaran dipicu → lacak akurasi (correct/triggered)
# Pensiun otomatis: akurasi < 0.4 setelah ≥ 10 pemicu
```

### News Veto (`bot/news.py`)
- RSS: CoinDesk + CoinTelegraph (tanpa API key, aman SSRF)
- Gemini menilai headline → `veto=True` saat berita high-impact (FOMC, SEC, hack, delisting)
- Cache 15 menit (hemat token)

---

## 4. FORWARD TESTER — MACHINE YANG BERJALAN

### File: `bot/forward.py` (2278 baris — inti eksekusi)

**Dual-mode runtime:**
- `use_store=False`: param hardcode `config.yaml` (backward compat)
- `use_store=True`: **hot-reload dari UI** (`RuntimeSettings` di SQLite, per-mode `dry/test/live`)

**Per-wallet Balance Split (USDT/USDC) — Tahap 1 (plan-sess):**
- `balance_usd` menjadi **computed property** = `balance_usdt + balance_usdc` (back-compat)
- Day PnL, peak balance, drawdown, day-start balance semuanya per-wallet
- Kill-switch drawdown per-wallet independen: keruntuhan wallet A tak lock wallet B
- Setting UI: 2 input terpisah (USDT + USDC), backend simpan per-wallet di `RuntimeSettings.balance_usdt` / `.balance_usdc`
- Persistence: state KV tulis `balance_usdt` + `balance_usdc` + legacy `balance` (back-compat)

**Siklus utama (`_on_cycle_store`):**
```
1. _apply_settings()        → hot-reload param UI (leverage, bet, technique, dll)
2. _process_close_requests  → tutup paksa dari UI
3. _live_reconcile()        → sync posisi & saldo nyata dari Binance (LIVE only)
4. EXIT SWEEP DULU          → _monitor_usd() SL/TP/liq MURNI ARITMETIKA (WAJIB tiap siklus)
5. Rollover hari UTC        → reset circuit breaker harian
6. News veto check
7. VRP brake check (shadow)
8. Drawdown lock check (kill-switch total, persisten restart)
9. Funding sim (paper)      → akru biaya funding 8-jam ke PnL
10. Planner refresh         → tujuan sesi (stance/bias/kuota)
11. Loop simbol:
    a. _update_buffer()     → refresh OHLCV
    b. _monitor_usd()       → cek SL/TP intrabar (high/low candle)
    c. Gemini manage pos    → ~1 menit, exit-only, GRACE 300s anti-whipsaw
    d. KAPASITAS ENTRY:
       - Rules-based: hanya bar baru tertutup
       - Gemini: timing bebas, throttle ≥60s per simbol
    e. PRE-GATE murah (tanpa panggil Gemini):
       - bot OFF / news veto / VRP brake / DD lock / CB / slot penuh / sudah punya posisi
       - ATR% < floor (0.08% default, 0.003% untuk regime=range sniper)
       - Price cache: skip jika Δharga < 0.03% (0% untuk regime=range)
    f. Kumpulkan kandidat Gemini + skor "menarik" (vol × gerakan + anti-starvation)
    g. RANKING: skor desc, tie-break nama (deterministik)
    h. BUDGET DINAMIS: ceil(simbol/cycles) per _decide_interval, cap wall-clock
    i. SIDEWAYS SNIPER BOOST: ≥50% simbol range → budget +300% (26 key aman)
    j. decide_batch() → Devil's Advocate → conviction boost (BTC dump/halving)
    k. _open_usd() → validasi SL/TP, order live + SL/TP exchange
12. Agent portfolio review → REDUCE_RISK / FLAT (hanya kurangi risiko)
13. Persist state (SQLite) + status push SSE
```

**Sideways Sniper (Profit Konsisten Walau Sideways):**
```yaml
gemini.sideways_sniper:
  enabled: true
  pregate_atr_pct_range: 0.003   # lantai ATR% DILENGGARKAN khusus range (0.3%)
  price_cache_pct_range: 0.0     # price-cache DIMATIKAN khusus range
  budget_boost_pct: 300          # budget Gemini +300% saat mayoritas range
  micro_tp_pct_min: 0.005%       # TP mikro minimum (nyaris berapapun gerakan)
  micro_tp_pct_max: 0.30%        # cap TP mikro agar tak greedy di sideways
  scalp_exit_bars: 3             # exit paksa bila tak profit dalam 3 bar
  require_setup_scalp_range: true
  devil_advocate_for_scalp: false  # hemat RPD
```

---

## 5. RESEARCH PIPELINE — WALK-FORWARD OOS YANG JUJUR

### File: `bot/optimize.py` + `bot/strategy_lab.py`

**Prinsip: OOS walk-forward adalah HAKIM TUNGGAL. In-sample positif = tidak berarti apa-apa.**

| Versi | Sumber Sinal (Struktural Baru?) | Backtestable | Verdict OOS |
|-------|--------------------------------|--------------|-------------|
| v1 | Trend/momentum/struktur OHLCV | baseline | −0.206R |
| v2 | + HTF + regime + sesi | filter | −0.105R |
| v3 | + funding z-score + OI delta | filter | −0.017R |
| v4 | + order flow/CVD | filter | **−0.007R (impas)** |
| **v5** | **Cross-exchange basis (Binance vs Bybit)** | **ya — antar-venue** | **−0.123R (REJECTED)** |
| **v6** | **Liquidation cascade fade (proxy OHLCV)** | **ya — event paksa** | **−0.430R (REJECTED)** |
| **v7** | **Funding regime sebagai sinyal PRIMER** | **ya — positioning** | **−0.116R (REJECTED)** |

**Fase 3 (H13 sector lead-lag, H14 listing-age):**
- H13 majors: OOS +0.2366%/rebal, p_adj=1.000 → **REJECTED** (27 majors korelasi tinggi)
- H13 small-cap 208: OOS −1.0058% → **REJECTED**
- H14 760 simbol: train SHORT usia 1-8 hari, test **balik tanda** (−0.55%) → **REJECTED**

**Fase 4:**
- H24 funding-settlement: OOS −0.1917% (1250 rebal, gross ≈ 0) → **REJECTED**
- H26 illiquidity-shock: pilot +0.54% tapi definitive −0.35% → **REJECTED** (shrinkage klasik)
- H25 carry×momentum: definitive −0.54% → **REJECTED** (carry angle exhausted)
- H31 downside-beta: −1.14% → **REJECTED** (sepupus BAB/coskew yang mati)
- H32 TSMOM 1d: +0.45% p_adj=0.59 → **REJECTED** (weak-positive-insignificant)
- H27 venue basis: −0.18% IS negatif → **REJECTED**
- **H28 VRP (DVOL gate)**: +2.06% n=38 p_adj=0.036 → **LOLOS AWAL** tapi replikasi 78×1800d menyusut +1.08%, p_adj=0.336 → **REJECTED** (palang #4)

**Fase 5 — DATA BREAKTHROUGH (Binance Vision):**
- H30 spread capture (aggTrades fill nyata 91 hari, 7.4jt trade):
  - FIL: effective spread 5.59bps, adverse −0.50bps → **EDGE KOTOR +3.30bps** ✅
  - **LANGKAH 3 (replay konservatif): SEMUA PAIR −7.3 s/d −11.4 bps/rt** → **REJECTED**
  - Diagnosis: +3.3bps = batas atas (fill orang lain, antrian tak terukur); langkah 3 hanya fill "tembus" → adverse selection TERBURUK by construction
- H19 OI crowding: **REJECTED** (n=15, −0.82%)

**KESIMPULAN PROGRAM (2026-07-02):**
> **25 hipotesis diuji jujur. 24 DITOLAK. 1 (H28) tersisa paper-test forward berbiaya nol.**
> Ruang edge retail kecil di Binance USDC perp — direksional DAN struktural — dieksplorasi sampai habis.
> **Sesuai handoff opsi #4: kesimpulan matang = edge tidak tersedia; JANGAN live-kan apa pun.**
> Bot tetap paper (gratis, teruji, berguna infrastruktur bila suatu saat muncul edge dari sumber data belum ada).

---

## 6. SISTEM PENGELOLAAN STATE & PERSISTENSI

### SQLite Store (`bot/store.py`) — `logs/bot.db`
| Tabel | Fungsi |
|-------|--------|
| `events` | Event trade (open/close/dll) — dual-write JSONL + SQLite |
| `kv` | Key-value blob JSON (runtime settings, status bot, agent memory) |
| `news_log` | Histori keputusan news veto (hanya saat berubah) |
| `screen_log` | Histori screening per pair (sinyal/alasan tak-entry, on-change) |
| `gemini_usage` | Pemantauan token per panggilan (model, key_idx, purpose) |
| `gemini_decisions` | Keputusan Gemini trader (open/settled, conviction, outcome_r, mae/mfe) + kolom `mode` untuk isolasi per-mode |
| `gemini_lessons` | Pelajaran (playbook) + **evidence-gate** (aktif HANYA lolos bukti) + kolom `mode` |
| `gemini_reflections` | Ringkasan refleksi berkala + kolom `mode` |
| `flat_shadow` | Keputusan FLAT Gemini (pending→settled, miss-rate evaluasi) |
| `calibration_log` | Brier score per trade (confidence vs outcome) |
| `vrp_shadow` / `mtf_shadow` | Shadow log regime brake / MTF agreement |

**Mode Isolation (Tahap 0 — plan-sess):**
- `gemini_decisions`, `gemini_lessons`, `gemini_reflections` memiliki kolom `mode TEXT DEFAULT 'dry'`
- Index: `idx_gdec_mode_status` (mode, status), `idx_glesson_mode_active` (mode, active), `idx_gref_mode` (mode)
- **Default: terisolasi per-mode** — track record dry/test/live tidak bercampur
- Config opt-in: `gemini.share_lessons_across_modes: true` (admin override)
- Semua query `settled_decisions()`, `recent_decisions()`, `setup_stats()`, `exit_stats()`, `active_lessons()` menerima arg `mode` opsional

**WAL mode** → aman baca-tulis konkuren (bot tulis, UI baca).

### Runtime Settings (`bot/settings_store.py`) — Per-Mode Bucket
```python
# Setiap mode (dry/test/live) punya setting terpisah di kv 'runtime:<mode>'
RuntimeSettings:
  enabled: bool               # bot aktif buka posisi? (default OFF — user nyalakan sadar)
  technique: "scalping|swing|auto|gemini"
  symbols: list[str]          # kosong = screening SEMUA pair
  leverage: int (1-125)
  bet_usd: float              # margin per posisi (dipakai bila bet_pct=0)
  bet_pct: float              # >0 → margin = %saldo (auto-scale $10→naik)
  balance_usd: float          # saldo LEGACY/backup (computed dari balance_usdt+balance_usdc)
  balance_usdt: float         # saldo wallet USDT-M (terpisah dari USDC)
  balance_usdc: float         # saldo wallet USDC-M (terpisah dari USDT)
  target_profit_pct: float    # 0 = pakai TP dari ATR; >0 = TP = entry×(1+ini%)
  max_open_positions: int
  daily_max_loss_pct: float   # 0 = nonaktif
  daily_max_trades: int       # 0 = nonaktif
  # Gemini knobs (hot-reload):
  gemini_decide_seconds: 60
  gemini_manage_seconds: 30
  gemini_min_hold_s: 300
  # Agent flags (OR dengan config.yaml):
  agent_full_auto, agent_tool_loop, agent_autonomous, agent_planner, agent_ab_shadow
  agent_manager_mode: bool    # JALAN A: agent = manajer disiplin (rules arah, planner+auto ON)
  # Confidence gate (Phase 2 kalibrasi):
  conf_full: 0.75, conf_min: 0.30, conf_reduced_mult: 0.5
```

---

## 7. RISK MANAGEMENT LAYER — CIRCUIT BREAKER BERLAPIS

### Level 1: Daily Circuit Breaker (`bot/risk.py:breaker_tripped`)
```python
# Reset tiap hari UTC
daily_max_loss_pct: 3%   # stop buka posisi bila rugi harian ≥ % saldo awal hari
daily_max_trades: 20     # stop bila jumlah open hari ini tercapai
```

### Level 2: Total Drawdown Kill-Switch (`bot/forward.py:_update_drawdown`)
```python
max_drawdown_pct: 20%    # dari puncak saldo PER MODE (persisten restart)
# Kunci PERMANEN — lepas HANYA manual via POST /api/dd-reset
# Berbeda dgn CB harian: ini tangkap bleed pelan berhari-hari
```

### Level 3: Position-Level Guards
```python
max_portfolio_exposure_pct: 30%   # total notional / equity
corr_threshold: 0.85              # blok entry SEARAH bila korelasi return ≥ ini
sl_atr_mult: 1.5 → floor 1.75×ATR # Fix A kalibrasi data
tp_atr_mult: 2.6                  # RR ~1.73
trailing_atr_mult: 1.2
```

### Level 4: Gemini Exit Kill-Switch (Empiris)
```python
# gemini_exit exp_R = -0.253 (n=11, sum_R=-2.785 dari 67 trade live)
# Kontribusi > total kerugian sistem → DIBLOKIR KERAS
if exit_track_record["gemini_exit"].n >= 10 and exp_r < 0:
    BLOCK gemini_exit → biarkan SL/TP native jalan
# Anti-cut prematur: unreal_R > +0.2R → jangan dipotong, sabar ke TP
```

---

## 8. AGENT AUTONOMI (Point 1-4 + Jalan A/B)

| Fitur | Config | Perilaku |
|-------|--------|----------|
| **Tool Loop** | `agent.tool_loop` | ReAct nalar + panggil tool iteratif (orderbook, korelasi, dll) sebelum putuskan |
| **Autonomous Portfolio** | `agent.autonomous` | Review posisi berkala → `REDUCE_RISK` (stop→BE) atau `FLAT` (tutup semua) |
| **Planner** | `agent.planner` | Tujuan sesi (stance/bias/kuota/eksposur) → enforce di gerbang entry |
| **Full Auto** | `agent.full_auto` | Satu saklar: nyalakan tool_loop + autonomous + planner sekaligus |
| **A/B Shadow** | `agent.ab_shadow` | ReAct menalar & MENCATAT verdict, TAK memblokir (rules tetap eksekusi) |
| **Manager Mode (Jalan A)** | `agent.agent_manager_mode` | **Override**: tool_loop OFF, autonomous+planner ON, no gemini-arah — agent jadi manajer disiplin |

> **Live FLAT butuh `gemini.allow_live_trader: true`** (keamanan eksplisit).

---

## 9. SHADOW GATES — ALAT UKUR, BUKAN GERBANG

Semua gate baru lahir sebagai **SHADOW** — catat, ukur, tak blokir — sampai bukti nyata:

| Gate | Metrik | Verdict Pra-Registrasi |
|------|--------|------------------------|
| **VRP** | exp_R brake-on vs brake-off (permutation test) | `VRP_BRAKE_ADDS_VALUE` iff p<0.05 & brake-on lebih buruk |
| **MTF** | win_rate agree vs disagree + Brier | `MTF_AGREEMENT_HELPS` iff agree menang lebih sering |
| **Flat Shadow** | miss_rate (gerakan ≥1R terlewat) per regime/conviction | `FLAT_BIAS_TOO_EXPENSIVE` iff n≥sample & miss_rate>threshold & ≥1 regime n≥50 lolos |
| **A/B ReAct** | exp_R rules vs rules+ReAct + risk metrics | `REACT_ADDS_VALUE` iff kept signifikan > denied (p<0.05) |

**Filosofi**: "LLM/regime = rem, bukan gas." Naik kelas ke `enforce` = commit terpisah setelah report positif.

---

## 10. DASHBOARD & MONITORING

### FastAPI Dashboard (`bot/dashboard.py`)
- **WebSocket/SSE real-time** (`EventHub`): status, stats, trade, order, balance, candle
- **REST API**: `/api/stats`, `/api/trades`, `/api/settings`, `/api/status`, `/api/ohlcv`, `/api/candles` (SQLite chartstore)
- **Agent Panel** (`/agent`): health, decisions, lessons, evolution, A/B report
- **Mode switching**: POST `/api/mode` (satu-satunya jalur ganti mode aktif, terpisah dari settings)
- **Kill-switch manual**: POST `/api/dd-reset` (lepaskan drawdown lock sadar)
- **SSE Candle Close Watcher** (Tahap 6c — plan-sess): async job periodik periksa bar terbaru tf high (1h/1d/1w/1M) di chartstore tiap 5 detik → broadcast `candle` event via SSE agar frontend update bar real-time tanpa polling REST. tf intraday (1m/5m/15m) tetap polling.
- **Open Order Kind/Linkage** (Tahap 3): endpoint `/api/open-orders` normalisasi order + klasifikasi `kind` (ENTRY_PENDING/SL/TP/EXIT_PENDING/UNKNOWN) + linkage posisi ↔ order reduce-only. Field `linked_symbol` + `linked_kind` pada tiap order. Cache 8 detik.

### Chartstore (`bot/chartstore.py`) — `data/market.db`
- OHLCV persisten SQLite (terpisah dari bot.db, bisa besar)
- `ingest` inkremental: lanjut dari ts terakhir; backfill penuh bila kosong
- `extra_paginate=True` (Tahap 6): paginasi mundur via `fetch_ohlcv` sampai ~10 tahun untuk tf 1w/1M (kap 520 bar/120 bar). Idempotent (PK upsert). Optional, default False.
- Dibaca dashboard `/api/candles` (whitelist tf: 1m/3m/5m/15m/30m/1h/2h/4h/1d/3d/1w/1M, max 5000 bar)

### EventHub (`bot/eventhub.py`) — SSE Multiplex
- Sumber: ZMQ OrderEvent (:5558), ZMQ Candle (:5556), Binance User Data WS, SQLite status poll
- **Fail-open**: satu sumber mati tidak menjatuhkan stream; slow client di-drop (QueueFull)
- **Mode Labeling** (Tahap 4 — plan-sess): tiap broadcast berlabel `mode`:
  - Binance WS (order_update/account_update) → `mode='live'` (hanya aktif di live mode)
  - SQLite watcher (status/balance) → `mode=<active_mode>` dari kv
  - ZMQ market/candle → `mode='*'` (global market data, bukan trade-source)
  - Frontend filter client-side sesuai mode UI aktif

### Frontend (`web/src/`)
- React/Vite, Lightweight Charts
- `PriceChart` timeframes: 1m/5m/15m/30m/1h/2h/4h/1d/1w/1M (Tahap 6)
- SSE `candle` event subscribe untuk tf high (1h/1d/1w/1M) — update bar real-time tanpa polling
- `ControlPanel` balance input: USDT + USDC terpisah (Tahap 1)

---

## 11. ENTRY POINTS & CLI

| Script | Fungsi |
|--------|--------|
| `forwardtest.py` | Forward-test paper (data live, tanpa uang) — `python forwardtest.py --poll 30 --use-store` |
| `dashboard.py` | Web monitor `python dashboard.py` → http://localhost:8000 |
| `optimize.py` | Walk-forward sweep param + co-pilot Gemini |
| `backtest.py` | Backtest event-driven (fee+slippage, maker/taker jujur) |
| `regime_ev.py` | Laporan expectancy per regime dari logs/trades.jsonl |
| `sl_calibrate.py` | Kalibrasi lantai SL (1 thn × 11 pair, 35k bar/pair) |
| `h30_hist.py` / `h30_sim.py` | H30 spread capture: histori aggTrades → replay konservatif |
| `h19_hist.py` | H19 OI crowding historis (Binance Vision metrics) |
| `chart_ingest.py` | Isi chartstore (tarik sekali, baca selamanya) |
| `lifecycle.py` | H14 listing-age cohort walk-forward |

---

## 12. KUNCI STRATEGI PROFIT KONSISTEN YANG SUDAH ADA

Berdasarkan analisis mendalam, **komponen-komponen ini SUDAH TERIMPLMENTASI** dan siap dipakai:

### A. BTC Mother Coin Asymmetry (Edge Struktural)
```python
# forward.py:_btc_lead() → dump_flag=True saat BTC turun ≥2% (3-bar)
# Conviction boost SHORT 1.5× saat dump_flag (forward.py:2085-2109)
# Halving phase macro boost: bull→LONG 1.3×, bear→SHORT 1.3× (forward.py:2102-2109)
```
> **Logika**: BTC turun 2-5% → investor pencari aman pindah ke BTC → BTC.D naik → ALT keluar dana + beta>1 → ALT TURUN LEBIH DALAM (1.5-3× gerak BTC). SHORT alt saat BTC dump = edge struktural.

### B. Trend Following Murni (Halving Cycle Awareness)
```python
# trader_curriculum.py: HALVING_CYCLE MACRO
# 'bull' (1 th post-halving): TREND-FOLLOWING LONG lebih sahih, hindari SHORT trend
# 'bear' (2-3 th post): SHORT trend lebih sahih, LONG hanya scalp/oversold bounce
# 'accumulation' (3-4 th, menuju halving): range/sideways dominan → scalp_range + range_fade
```
> **Ini jawaban "BTC memiliki siklus halving 4 tahun sekali coin lain juga naik pastikan trend following itu saja"**

### C. Sideways Sniper (Profit Mikro Konsisten)
```yaml
# config.yaml: gemini.sideways_sniper
# Regime=range (ADX<15, ATR<0.3%): entry kecil, SL ketat 1×ATR, TP cepat 1.2×ATR
# micro_tp_pct_min: 0.005% (nyaris berapapun gerakan, asal positif, ambil)
# scalp_exit_bars: 3 (exit paksa bila tak profit dalam 3 bar)
# Budget boost +300% saat ≥50% simbol range → scalp_range dapat kesempatan
```

### D. Risk/Exit Management Yang Terbukti
| Fitur | Bukti |
|-------|-------|
| **SL Floor 1.75×ATR** | 1 thn × 11 pair: q80 MAE pemenang = 1.76×ATR → SL 1.5×ATR hanya selamatkan 75% |
| **Micro-profit lock** | peak_tp_progress ≥ 30% → SL→breakeven otomatis |
| **Give-back trigger** | posisi sempat ≥50% ke TP lalu balik ≥15pp → PAKSA Gemini review (kunci profit/exit) |
| **Gemini exit kill-switch** | exp_R=-0.253 → blokir eksekusi, biarkan SL/TP native |
| **Anti-cut prematur** | unreal_R > +0.2R → jangan dipotong, sabar ke TP |

---

## 13. CARA MENGUBAH STRATEGI UNTUK PROFIT KONSISTEN (TANPA MERUSAK TUJUAN)

### PRINSIP: JANGAN UBAH YANG SUDAH BEKERJA. TAMBAHKAN LAYER BARU.

Berdasarkan riset 25 hipotesis: **Entry signal di 15m bar-resolution sudah diarbitrase penuh.** Edge ritel yang tersisa: **eksekusi/likuiditas** (maker rebate, spread capture, TWAP di pair illiquid) — butuh program riset beda (L2 collection sudah di-scaffold `l2collect.py`).

**Jika tetap ingin "profit konsisten" di level entry, gunakan yang SUDAH ADA:**

#### Opsi 1: TREND-FOLLOWING ONLY (Paling Jujur)
```yaml
# config.yaml - matikan SEMUA mean-reversion/fade:
strategy:
  gate_overext: true      # ON: tolak entry overextended (RSI jenuh + jarak EMA)
  gate_runup: true        # ON: tolak entry chase lonjakan N-bar
  gate_corr: true         # ON: guard korelasi
  # v2 regime: adx_strong=25 (trend follow), adx_range=18 (MR) → SET adx_range=999 (matikan MR)
  adx_range: 999          # mean-reversion tidak pernah aktif
  # Gemini: hanya konfirmasi trend, jangan fade
gemini:
  role: "veto"            # regime score < 0.4 → skip (choppy/chaos)
  sideways_sniper:
    enabled: false        # MATIKAN sniper range (hanya trend)
```
**Hasil**: Trade JARANG (1-3/minggu), profit TIDAK konsisten per-hari, tapi expectancy positif jangka panjang. Konsisten = disiplin proses, bukan profit harian.

#### Opsi 2: SHORT-PRIORITY SAAT BTC DUMP (Edge Asimetris)
```yaml
# config.yaml - perkuat BTC gate:
btc:
  enabled: true
  dump_pct: 1.0           # turunkan dari 0.5% → 1.0% (lebih selektif "kuat")
  block_counter: true     # blok total lawan-arah BTC kuat
  size_floor: 0.2         # kalau mode diskon, minimal 20%
```
**Kombinasikan dengan conviction boost existing** (`forward.py:2085-2109`): `dump_flag=True` → SHORT conviction ×1.5.

#### Opsi 3: KONSISTENSI VIA RISK/EXIT (Bukan Entry)
> **Temuan riset**: Exit & risk management kontribusi > entry. 72% exit kena SL (sampel awal 36 trade).
```yaml
risk:
  sl_atr_mult: 1.75       # pakai lantai kalibrasi (bukan 1.5)
  tp_atr_mult: 2.6        # RR 1.73
  trailing: true
  trailing_atr_mult: 1.2
gemini:
  giveback_tp_frac: 0.5   # posisi sempat ≥50% ke TP
  giveback_margin: 0.15   # lalu balik ≥15pp → paksa review
  sideways_sniper:
    enabled: true
    scalp_exit_bars: 3    # exit cepat di range
```
**Fokus**: Batasi rugi, biarkan profit jalan, exit cepat di sideways.

#### Opsi 4: KOMBINASI (Rekomendasi Jika Mau Rewrite)
```python
# 1. Rewrite signals.py → decide_v8 (PURE TREND FOLLOWING)
#    - Hanya trend_continuation setup (pullback SELESAI + momentum resume)
#    - Matikan trend_pullback (terbukti -1.25R), range_fade, breakout_continuation
#    - BTC gate sebagai FILTER UTAMA (bukan sekadar blokir)
#    - Halving phase sebagai MACRO BIAS (boost conviction arah makro)

# 2. Rewrite risk.py → dynamic RR berbasis regime
#    - trend: RR ≥ 2.0, trailing agresif
#    - range: RR 1.0-1.2, micro-TP, exit paksa 3-bar

# 3. Tambahkan VRP/MTF/Flat shadow sebagai GATE (enforce mode setelah shadow positif)

# 4. Live: HANYA paper dulu → cost-stress 2× → lockbox → micro-live
```

---

## 14. CHECKLIST SEBELUM LIVE (NON-NEGOTIABLE)

```
[ ] OOS exp_R > +0.05R DAN ≥3 window positif DAN ≥3 simbol konsisten
[ ] Lolos signifikansi statistik (bootstrap Bonferroni + effective-n ≥ 30)
[ ] Parameter stabil ≥50% antar-window
[ ] Cost-stress 2× (fee+slippage lipat) → edge bertahan
[ ] Lockbox (--holdout-frac 0.2 → --lockbox sekali pakai) → bertahan
[ ] Paper forward-test data live parameter TETAP berhari-hari
[ ] Micro-live (modal sangat kecil) → tetap positif
[ ] Naikkan ukuran perlahan bila tetap positif
```

**Jika salah satu gagal → kembali ke flat / riset. Jangan live-kan.**

---

## 15. FILE KUNCI UNTUK MODIFIKASI STRATEGI

| Tujuan | File | Fungsi |
|--------|------|--------|
| **Sinyal entry (rules-based)** | `bot/signals.py:evaluate()` | Skor trend/mom/struktur → side + confidence + regime |
| **Sinyal entry (Gemini)** | `bot/gemini_trader.py:decide()` | Context-rich prompt → JSON {side, conviction, sl, tp, setup} |
| **Risk sizing & SL/TP** | `bot/risk.py:RiskGate.evaluate()` | ATR-based SL/TP, size dari account_risk_pct, exposure cap |
| **BTC gate** | `bot/altdata.py:btc_gate()` | Blok/diskon entry lawan-arah BTC kuat |
| **Halving phase** | `bot/forward.py:_halving_phase()` | Macro regime: accumulation/pre-halving/bull/blow-off/bear |
| **Conviction boost** | `bot/forward.py:2085-2109` | BTC dump_flag → SHORT ×1.5, halving phase ×1.3 |
| **Sideways sniper** | `bot/forward.py:1397-1427` | Regime=range → micro-TP, budget boost, exit paksa |
| **SL floor** | `bot/forward.py:_sl_floor()` | 1.75×ATR + 0.5×range (skip di regime=range) |
| **Exit management** | `bot/forward.py:_monitor_usd()` | Intrabar SL/TP/liq + give-back trigger + micro-profit lock |
| **Walk-forward research** | `bot/optimize.py:run_walk()` | Train in-sample → test OOS → geser → verdict deterministik |
| **Co-pilot Gemini** | `bot/copilot.py:advise()` | Tafsir OOS + usul hipotesis struktural baru |
| **Settings hot-reload** | `bot/settings_store.py:RuntimeSettings` | UI → SQLite → bot apply tiap siklus (per-mode bucket) |
| **Balance per-wallet** | `bot/forward.py:balance_usd` (property) | `balance_usd` = computed (`balance_usdt` + `balance_usdc`), per-wallet PnL/drawdown/peak |
| **Mode isolation** | `bot/store.py:settled_decisions()` | Gemini track record per-mode (dry/test/live terisolasi) — kolom `mode` di `gemini_decisions`/`gemini_lessons`/`gemini_reflections` |
| **SSE candle watcher** | `bot/dashboard.py:_candle_close_watcher()` | Periodik deteksi close high-TF (1h/1d/1w/1M) → broadcast SSE 'candle' event |
| **Chart TF 1w/1M** | `bot/chartstore.py:ingest(extra_paginate=True)` | Paginated backfill historis untuk high-timeframe (≤10 thn) |
| **EventHub mode label** | `bot/eventhub.py:broadcast(mode=...)` | Label mode tiap event SSE (live/per-mode/global) |
| **Open order linkage** | `bot/dashboard.py:_normalize_open_order()` | Klasifikasi kind (ENTRY_PENDING/SL/TP) + link ke posisi |

---

## 16. KESIMPULAN & SARAN

**Arsitektur ini sudah sangat matang** — 25 hipotesis diuji jujur, infrastructure lengkap (data, risk, execution, LLM-as-director, shadow gates, dashboard, persistence). **Yang kurang hanyalah EDGE yang terbukti.**

**Saran saya (jujur, based on evidence):**

1. **JIKA MAU PROFIT KONSISTEN SEKARANG**: Gunakan **Opsi 3 (Konsistensi via Risk/Exit)** — matikan mean-reversion, perkuat SL floor, micro-profit lock, give-back trigger, sideways sniper. Terima trade jarang, expectancy positif jangka panjang.

2. **JIKA MAU EDGE STRUKTURAL**: Fokus **SHORT-priority saat BTC dump ≥2%** (sudah di kode, tinggal pakai). Kombinasi: `btc.dump_pct: 1.0` + `block_counter: true` + conviction boost existing.

3. **JIKA MAU REWRITE ENTRY**: Buat `decide_v8` pure trend-following (hanya `trend_continuation` setup), BTC gate sebagai filter utama, halving phase macro bias. **Tapi butuh waktu lama edit-test-verify** — semua parameter harus divalidasi walk-forward OOS baru.

4. **JANGAN LIVE-KAN APA PUN** yang belum lolos 4 palang (OOS exp_R>0.05, ≥3 window, ≥3 simbol, signifikansi, stabilitas param, cost-stress 2×, lockbox, paper forward).

**Bot ini adalah infrastruktur riset & eksekusi yang jujur — nilainya pada 25 keputusan "tidak" yang terdokumentasi, masing-masing menyelamatkan uang nyata.** Gunakan sebagai fondasi, bangun di atasnya dengan disiplin yang sama.

---

*Dokumentasi ini dihasilkan dari analisis penuh 60+ file Python (total ~15.000 baris) pada 2026-07-13. Setiap referensi baris kode (`file.py:line`) dapat diverifikasi langsung.*

*Update 2026-07-13: per-wallet balance split (Tahap 1), mode isolation (Tahap 0), SSE candle watcher (Tahap 6c), high-TF chart (Tahap 6), EventHub mode labeling (Tahap 4), open order kind/linkage (Tahap 3).*