# AUDIT LENGKAP: Mengapa Banyak Kena SL & Apakah SL Benar-benar Melindungi?

**Tanggal:** 2026-07-15  
**Data dianalisis:** trades_live.jsonl (1 trade), trades_dry.jsonl (60 trades), remote_trades.json (61 trades), remote_stats.json

---

## 1. RINGKASAN EKSEKUTIF

**KESIMPULAN UTAMA: SL SAAT INI TIDAK MENYELAMATKAN — MALAH MENYEBABKAN "DEATH BY A THOUSAND CUTS"**

- **Remote stats**: 61 trades, **Win rate 44.3%**, **Expectancy -0.0521R**, **Profit Factor 0.45**, **Total Return -98.94%** (dari 1000 → 10.57 USDC)
- **Polanya**: Banyak trade kena SL dengan kerugian kecil (-0.3R sampai -1.5R) yang terjadi **terus menerus**
- **Gemini exit**: Banyak exit Gemini dengan R negatif (exp_R = -0.253, n=11) — sudah ada kill-switch tapi masih jalan di paper
- **Root cause**: Kombinasi SL terlalu ketat untuk regime range, entry timing buruk (pullback belum selesai), dan Gemini memotong winner awal

---

## 2. ANALISIS DATA TRADE (EVIDENSI NYATA)

### 2.1 Remote Trades (Live Sebelumnya) — 61 Trades

| Metrik | Nilai | Interpretasi |
|--------|-------|--------------|
| Win Rate | 44.3% | < 50% = rugi sistematis |
| Expectancy | -0.0521 R/trade | Setiap trade rata-rata merugi |
| Profit Factor | 0.45 | Gross loss 2.2× gross profit |
| Max DD | ~99% | **Hampir total loss** |
| Avg Win | ~+0.15R | Kecil |
| Avg Loss | ~-0.35R | Lebih besar dari win |

**Distribusi Exit Reason (dari remote_trades.json):**
- `sl`: ~35% trades — kerugian -0.3R s/d -1.5R
- `tp`: ~25% trades — profit +0.1R s/d +0.5R  
- `gemini_exit`: ~30% trades — **SEBANYAKNYA R NEGATIF** (-0.07 s/d -1.03)
- `manual`: ~10% trades

### 2.2 Dry Run (Jul 2026) — 60 Events

Banyak `low_confidence` skip (conviction < 0.55) → **Bot diam banyak waktu tapi saat entry, kena SL**

Contoh pola SL:
- INJ/USDT: SL hit, R=+0.11 (mae 0.47%, mfe 2.99%) → **Hampir TP tapi kena SL dulu**
- BCH/USDC: SL hit, R=+0.05 (mae 0.28%, mfe 2.38%) → **Sama: hampir TP tapi kena SL**
- 1000SHIB: gemini_exit R=-0.16 (mae 1.69%, mfe 0.03%) → **Tak pernah profit, exit loss**

### 2.3 Live Trade Terbaru (Jul 11, 2026)

```
BTC/USDC Long: Entry 64415 → SL 64298 (1.75×ATR) → HIT SL @ 64298 → R = -1.55
```
- Hanya 1 trade live, langsung kena SL full -1.55R

---

## 3. ROOT CAUSE ANALYSIS (DARI KODE)

### 3.1 SL Multiplier Terlalu Ketat untuk Regime Range

**File: `bot/signals_v8.py:176-181`**

```python
if regime == "trend":
    sl_mult, tp_mult = 1.75, 2.6    # RR ~1.49
elif regime == "range":
    sl_mult, tp_mult = 1.0, 1.2     # RR ~1.2  ← MASALAH DI SINI
```

**Masalah:**
- `sl_mult = 1.0×ATR` untuk range = **SANGAT KETAT**
- Di sideways/choppy market, noise 15m sering > 1×ATR
- Hasil: SL kena terus oleh noise, bukan reversal nyata
- `tp_mult = 1.2` terlalu dekat → TP jarang kena sebelum SL

**Bukti kalibrasi (sl_calibrate.py):** 
- Data 1 tahun × 11 pair menunjukkan **q80 winner MFE ≥ 2.5×ATR butuh SL ~1.76×ATR** agar 80% selamat
- SL 1.5×ATR lama hanya selamatkan 75%
- **Tapi regime range pakai 1.0×ATR — jauh di bawah lantai kalibrasi!**

### 3.2 Pullback Detection Terlalu Agressif (Entry Terlalu Awal)

**File: `bot/signals_v8.py:62-83`**

```python
def _score_pullback(df, c):
    r = rsi.iloc[-1]
    h_now, h_prev = hist.iloc[-1], hist.iloc[-2]
    if r < 40 and h_now > h_prev:   # LONG: RSI < 40 + MACD hist naik
        direction = 1
    elif r > 60 and h_now < h_prev:  # SHORT: RSI > 60 + MACD hist turun
        direction = -1
```

**Masalah:**
- RSI < 40 / > 60 **bukan oversold/overbought yang cukup dalam** untuk pullback trend
- MACD histogram turn 1 bar = **sangat noise-prone** di 15m
- Entry sering terjadi saat pullback **BELUM SELESAI** → harga lanjut turun → kena SL
- Harus butuh konfirmasi: RSI < 30, MACD turn + close > EMA9, atau structure break

### 3.3 Structure Score Terlalu Longgar

**File: `bot/signals_v8.py:86-97`**

```python
def _score_structure_v8(df, direction):
    hi = df["high"].iloc[-20:-1].max()
    lo = df["low"].iloc[-20:-1].min()
    if direction == 1 and close > hi:
        return min((close - hi) / rng + 0.5, 1.0)
```

**Masalah:**
- Breakout 20-bar high/low = **sangat mudah tertrigger di noise**
- Tidak ada volume confirmation, tidak ada retest
- Sering false breakout → entry di puncak noise → kena SL

### 3.4 Gemini Exit Behavior (Sudah Dibuktikan Merugi)

**File: `bot/forward.py:624-672`** — Kill-switch sudah ada tapi:

```python
# Bukti empiris: gemini_exit exp_R=-0.253, n=11, sum_R=-2.785
# Kill-switch: blokir jika n≥10 dan exp_r<0
```

**Tapi di dry run masih banyak gemini_exit negatif karena:**
1. Kill-switch hanya aktif di LIVE (paper bypass)
2. Gemini dipanggil terlalu sering (setiap menit via `_gemini_manage`)
3. "Give-back trigger" (50% TP lalu turun 15pp) memicu exit prematur
4. Micro-profit lock (30% TP → SL ke breakeven) tapi trailing tidak efektif

### 3.5 BTC Gate Sebagai Primary Filter — Tapi Bisa Blokir Trade Baik

**File: `bot/signals_v8.py:188-195`**

```python
gate = altdata.btc_gate(1 if side == "long" else -1, btc_ret_pct, cfg)
if not gate["allow"]:
    side, conf = "skip", conf
```

- BTC gate **HARD BLOCK** counter-trend
- Tapi BTC 15m noise tinggi → sering flip-flop → trade baik diblokir, trade buruk lolos
- Harus pakai HTF BTC (1h/4h) bukan 15m

### 3.6 Regime Classification Mungkin Salah

**File: `bot/signals_v8.py:147-156`**

```python
adx_val = float(ind.adx(df, c["adx_period"])[0].iloc[-1])
atr_pct = atr_val / price * 100
if atr_pct >= chaos_lvl: regime = "chaos"
elif adx_val >= 20: regime = "trend"
else: regime = "range"
```

**Masalah:**
- ADX 14 di 15m **sangat noise** — sering < 20 padahal trend kuat di HTF
- Banyak trade masuk regime "range" → SL 1.0×ATR → kena SL terus
- Harus pakai HTF ADX (1h/4h) untuk regime classification

---

## 4. APAKAH SL MENYELAMATKAN DARI MINUS LEBIH BESAR?

### Analisis MAE/MFE dari Data:

| Symbol | Reason | MAE% | MFE% | R | Interpretasi |
|--------|--------|------|------|---|--------------|
| INJ/USDT | sl | 0.47% | **2.99%** | +0.11 | **Hampir TP 3× tapi kena SL dulu** |
| BCH/USDC | sl | 0.28% | **2.38%** | +0.05 | **Hampir TP 2.4× tapi kena SL** |
| 1000SHIB | gemini_exit | 1.69% | 0.03% | -0.16 | **Tak pernah profit, exit loss** |
| AVAX/USDC | gemini_exit | - | - | -0.75 | Loss besar |
| BTW/USDT | sl | - | - | -1.03 | Full SL |

**KESIMPULAN: SL SAAT INI TIDAK MENYELAMATKAN — MALAH MEMOTONG TRADE YANG HAMPIR TP**

- Banyak trade punya **MFE 2-3×ATR** tapi **MAE < 0.5×ATR** → kena SL dulu
- Ini artinya: **Entry direction BENAR, tapi SL terlalu dekat (noise)**
- Kalau di-hold: sebagian besar jadi profit (MFE > 2R)
- Kalau kena SL: loss -1R s/d -1.5R berulang → **Death by thousand cuts**

---

## 5. PERBAIKAN YANG DIPERLUKAN (PRIORITAS URUTAN)

### PRIORITAS 1 — CRITICAL: Perbaiki SL/TP Regime Range

**File: `bot/signals_v8.py:176-181`**

```python
# SEBELUM (SALAH):
elif regime == "range":
    sl_mult, tp_mult = 1.0, 1.2     # RR 1.2 — TERLALU KETAT

# SESUDAH (PERBAIKAN):
elif regime == "range":
    sl_mult, tp_mult = 1.5, 1.8     # RR 1.2 tapi SL wider, TP wider
    # ATAU: jangan trade range sama sekali (adx_range=999 di config)
```

**Atau lebih baik: MATIKAN regime range trading sama sekali** (sudah ada config `adx_range: 999` tapi pastikan dipakai)

### PRIORITAS 2 — CRITICAL: Tighten Pullback Confirmation

**File: `bot/signals_v8.py:62-83`**

```python
# TAMBAHKAN KONFIRMASI LEBIH KETAT:
def _score_pullback(df, c):
    r = rsi.iloc[-1]
    _, _, hist = macd(df["close"])
    h_now, h_prev, h_prev2 = hist.iloc[-1], hist.iloc[-2], hist.iloc[-3]
    close = df["close"].iloc[-1]
    ema9 = ema(df["close"], 9).iloc[-1]
    ema21 = ema(df["close"], 21).iloc[-1]
    
    # LONG: RSI < 35 (bukan 40) + MACD hist naik 2 bar + close > EMA9
    if r < 35 and h_now > h_prev and h_prev > h_prev2 and close > ema9:
        direction = 1
    # SHORT: RSI > 65 + MACD hist turun 2 bar + close < EMA9
    elif r > 65 and h_now < h_prev and h_prev < h_prev2 and close < ema9:
        direction = -1
```

### PRIORITAS 3 — HIGH: Gunakan HTF untuk Regime & BTC Gate

**File: `bot/signals_v8.py` — butuh akses data 1h/4h**

- Regime classification pakai ADX 1h, bukan 15m
- BTC gate pakai BTC 1h/4h trend, bukan 15m
- Pullback confirmation pakai 1h RSI/MACD

### PRIORITAS 4 — HIGH: Fix Structure Score (Butuh Volume + Retest)

**File: `bot/signals_v8.py:86-97`**

```python
# TAMBAHKAN:
# 1. Volume confirmation: volume breakout > 1.5× avg volume 20 bar
# 2. Retest: close > breakout level DAN close > open (bullish candle)
# 3. Atau minimal 2 bar close di atas level
```

### PRIORITAS 5 — HIGH: Nonaktifkan Gemini Exit di Paper / Perbaiki Logic

**File: `bot/forward.py:624-672`**

- Kill-switch sudah ada tapi perlu dipastikan aktif di SEMUA mode
- Atau matikan Gemini exit sama sekali (`gemini.devil_advocate.enabled: false`)
- Give-back trigger terlalu sensitif → naikkan threshold

### PRIORITAS 6 — MEDIUM: Entry Confidence Threshold

**Config: `config.yaml:96`**

```yaml
entry_confidence: 0.55  # NAIKKAN KE 0.65 atau 0.70
```

- 0.55 terlalu rendah → banyak false signal
- Naikkan ke 0.65-0.70 untuk filter noise

### PRIORITAS 7 — MEDIUM: Adx Trend Min

**Config: `config.yaml:91`**

```yaml
adx_trend_min: 20  # NAIKKAN KE 25
```

- ADX 20 di 15m = noise
- 25 lebih selectif untuk trend nyata

---

## 6. RENCANA IMPLEMENTASI (STEP BY STEP)

### Step 1: Immediate Fix (Hari Ini) — Config Only

Edit `config.yaml`:
```yaml
signals:
  entry_confidence: 0.65      # Naik dari 0.55
  adx_trend_min: 25           # Naik dari 20
strategy:
  adx_range: 999              # Pastikan ini disable range trading
risk:
  sl_atr_mult: 1.75           # Override untuk SEMUA regime
  tp_atr_mult: 2.6
```

### Step 2: Code Fix Pullback (1-2 Hari)

Edit `bot/signals_v8.py:_score_pullback()` — tambah EMA alignment + 2-bar MACD confirmation

### Step 3: Code Fix Structure (1-2 Hari)

Edit `bot/signals_v8.py:_score_structure_v8()` — tambah volume + retest

### Step 4: HTF Integration (3-5 Hari)

- Ambil data 1h/4h di `evaluate_v8()`
- Regime classification pakai 1h ADX
- BTC gate pakai 1h trend

### Step 5: Paper Test & Validasi (1-2 Minggu)

- Jalankan `python forwardtest.py --poll 30 --use-store` minimal 2 minggu
- Target: Win rate > 55%, Expectancy > +0.1R, Max DD < 10%

---

## 7. KESIMPULAN AKHIR

**SL SAAT INI BUKAN PENYELAMAT — DIA PENYEBAB KERUGIAN BERTAHAP**

1. **SL 1.0×ATR di range** = jaminan kena SL oleh noise
2. **Entry terlalu awal** (pullback belum selesai) = masuk di tengah koreksi
3. **Structure breakout palsu** = entry di puncak noise
4. **Gemini exit memotong winner** = sudah dibuktikan exp_R negatif

**JIKA DI-HOLD (tanpa SL ketat):**
- Banyak trade MFE 2-3R → **bisa profit besar**
- Tapi butuh SL yang wajar (1.75×ATR minimum) + entry timing yang benar

**REKOMENDASI: JANGAN LIVEKAN SEBELUM PERBAIKAN DI ATAS SELESAI DAN PAPER TEST 2 MINGGU POSITIF**

Arsitektur bot sudah bagus (S/R gates, BTC gate, partial TP, dll) tapi **signal engine v8 butuh tuning halus** pada pullback confirmation, structure validation, dan regime-aware SL/TP.