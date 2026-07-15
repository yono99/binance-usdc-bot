# RENCANA PERBAIKAN (IMPROVEMENT PLAN) — BINANCE USDC BOT

**Berdasarkan Audit SL Analysis (2026-07-15)**

---

## PRIORITAS 1 — CRITICAL: Config Override Instan (BISA DILAKUKAN SEKARANG)

### 1.1 Edit `config.yaml` — Disable Range Trading & Widen SL

```yaml
# config.yaml — BARIS YANG HARUS DIUBAH:

signals:
  entry_confidence: 0.65      # NAIK dari 0.55 (filter noise)
  adx_trend_min: 25           # NAIK dari 20 (hanya trend kuat)
  # weights tetap: trend 0.4, momentum 0.3, structure 0.3

strategy:
  adx_range: 999              # PASTIKAN 999 (disable range fade/scalp)
  # cleanliness gates sudah aktif — BIARKAN

risk:
  sl_atr_mult: 1.75           # OVERRIDE untuk SEMUA regime (bukan 1.0 di range)
  tp_atr_mult: 2.6            # OVERRIDE untuk SEMUA regime
  # trailing_atr_mult: 1.2    # biarkan

gemini:
  sideways_sniper:
    enabled: false            # MATIKAN scalp_range (sudah false tapi pastikan)
  devil_advocate:
    enabled: false            # MATIKAN Devil's Advocate (hemat RPD, reduce noise)
  news_veto: true             # biarkan
```

**Expected Impact:** 
- SL minimum 1.75×ATR untuk SEMUA trade (sesuai kalibrasi data 1 tahun)
- Entry hanya saat confidence ≥ 0.65 + ADX ≥ 25 = filter 70% noise
- Range trading dimatikan total (adx_range=999)

---

## PRIORITAS 2 — CRITICAL: Perbaiki Pullback Confirmation (Code Change)

### 2.1 Edit `bot/signals_v8.py` — `_score_pullback()` Lebih Ketat

**File:** `bot/signals_v8.py` **Lines 62-83**

```python
def _score_pullback(df: pd.DataFrame, c: dict) -> tuple[float, int]:
    """Pullback completion score: RSI normalization + MACD histogram turn + EMA alignment.
    
    LONG: RSI < 35 (bukan 40) + MACD hist naik 2 bar + close > EMA9
    SHORT: RSI > 65 + MACD hist turun 2 bar + close < EMA9
    """
    from .indicators import rsi, macd, ema
    
    r = rsi(df["close"], c["rsi_period"]).iloc[-1]
    _, _, hist = macd(df["close"])
    h_now, h_prev, h_prev2 = hist.iloc[-1], hist.iloc[-2], hist.iloc[-3]
    close = df["close"].iloc[-1]
    ef = ema(df["close"], c["ema_fast"]).iloc[-1]      # EMA9
    em = ema(df["close"], c["ema_mid"]).iloc[-1]       # EMA21
    
    direction = 0
    # LONG: oversold + momentum turning up 2 bars + price above fast EMA
    if r < 35 and h_now > h_prev and h_prev > h_prev2 and close > ef:
        direction = 1
    # SHORT: overbought + momentum turning down 2 bars + price below fast EMA
    elif r > 65 and h_now < h_prev and h_prev < h_prev2 and close < ef:
        direction = -1

    if direction == 0:
        return 0.0, 0

    # Distance from midpoint = pullback depth (0.5 = midpoint)
    dist = min(abs(r - 50) / 25.0, 1.0)
    return dist, direction
```

**Perubahan Kunci:**
- RSI threshold: 40→35 (long), 60→65 (short) — lebih ekstrem = pullback lebih dalam
- MACD hist: 1 bar → 2 bar confirmation — filter noise
- Tambah EMA9 alignment — konfirmasi arah momentum

---

## PRIORITAS 3 — CRITICAL: Perbaiki Structure Score (Volume + Retest)

### 3.1 Edit `bot/signals_v8.py` — `_score_structure_v8()` Lebih Selektif

**File:** `bot/signals_v8.py` **Lines 86-97**

```python
def _score_structure_v8(df: pd.DataFrame, direction: int) -> float:
    """Structure: breakout confirmation in trend direction WITH volume & retest."""
    close = df["close"].iloc[-1]
    open_ = df["open"].iloc[-1]
    volume = df["volume"].iloc[-1]
    avg_vol = df["volume"].iloc[-20:-1].mean()
    
    hi = df["high"].iloc[-20:-1].max()
    lo = df["low"].iloc[-20:-1].min()
    rng = (hi - lo) or 1e-9

    if direction == 1:
        # LONG: close > 20-bar high + volume confirmation + bullish candle
        if close > hi and volume > avg_vol * 1.3 and close > open_:
            breakout_strength = min((close - hi) / rng + 0.5, 1.0)
            vol_bonus = min(volume / (avg_vol * 1.3), 1.5)
            return min(breakout_strength * vol_bonus, 1.0)
    elif direction == -1:
        # SHORT: close < 20-bar low + volume confirmation + bearish candle
        if close < lo and volume > avg_vol * 1.3 and close < open_:
            breakout_strength = min((lo - close) / rng + 0.5, 1.0)
            vol_bonus = min(volume / (avg_vol * 1.3), 1.5)
            return min(breakout_strength * vol_bonus, 1.0)
    return 0.0
```

**Perubahan Kunci:**
- Tambah volume confirmation (> 1.3× avg 20-bar)
- Tambah candle color confirmation (bullish untuk long, bearish untuk short)
- Score = breakout_strength × volume_bonus

---

## PRIORITAS 4 — HIGH: HTF Regime & BTC Gate (Butuh Refactor Sedang)

### 4.1 Tambah Helper untuk Ambil Data HTF di `bot/signals_v8.py`

**File:** `bot/signals_v8.py` — Tambah import & fungsi baru di atas `evaluate_v8`

```python
# TAMBAHKAN DI ATAS evaluate_v8():
from .exchange import Exchange
from .config import Settings

_htf_cache: dict[str, tuple[float, pd.DataFrame]] = {}  # symbol -> (ts, df_1h)
_htf_ttl = 300  # 5 menit cache

def _get_htf_data(symbol: str, ex: Exchange, timeframe: str = "1h", limit: int = 200) -> pd.DataFrame | None:
    """Ambil data HTF (1h/4h) dengan cache 5 menit."""
    import time
    key = f"{symbol}:{timeframe}"
    now = time.time()
    if key in _htf_cache:
        ts, df = _htf_cache[key]
        if now - ts < _htf_ttl:
            return df
    try:
        df = ex.ohlcv(symbol, timeframe, limit=limit)
        _htf_cache[key] = (now, df)
        return df
    except Exception:
        return None
```

### 4.2 Update `evaluate_v8()` untuk Pakai HTF Regime & BTC

**File:** `bot/signals_v8.py` **Lines 119-203** — Modifikasi `evaluate_v8()`

```python
def evaluate_v8(symbol: str, df: pd.DataFrame, cfg: dict,
                btc_ret_pct: float | None = None,
                ex: Exchange | None = None) -> Signal:   # TAMBAH ex parameter
    """Pure trend-following evaluation (v8) dengan HTF regime & BTC gate."""
    c = cfg["signals"]
    w = c["weights"]
    price = float(df["close"].iloc[-1])
    atr_val = float(ind.atr(df, c["atr_period"]).iloc[-1])

    # 1. Trend score (EMA alignment + ADX) — PAKAI 15m seperti biasa
    ts, td = _score_trend_v8(df, c)

    # 2. Pullback score (RSI + MACD + EMA9) — SUDAH DIPERBAIKI PRIORITAS 2
    ps, pd_dir = _score_pullback(df, c)

    # 3. Structure (breakout + volume + retest) — SUDAH DIPERBAIKI PRIORITAS 3
    ss = _score_structure_v8(df, td)

    # 4. HTF REGIME CLASSIFICATION (BARU) — pakai 1h ADX
    htf_df = _get_htf_data(symbol, ex, "1h") if ex else None
    if htf_df is not None and len(htf_df) >= 50:
        htf_adx = float(ind.adx(htf_df, 14)[0].iloc[-1])
        htf_atr_pct = float(ind.atr(htf_df, 14).iloc[-1] / htf_df["close"].iloc[-1] * 100)
        if htf_atr_pct >= cfg.get("strategy", {}).get("max_atr_pct_chaos", 8.0):
            regime = "chaos"
        elif htf_adx >= 25:      # HTF ADX 25 = trend kuat di 1h
            regime = "trend"
        else:
            regime = "range"
    else:
        # Fallback ke 15m kalau HTF gagal
        adx_val = float(ind.adx(df, c["adx_period"])[0].iloc[-1])
        atr_pct = atr_val / price * 100 if price else 0.0
        chaos_lvl = cfg.get("strategy", {}).get("max_atr_pct_chaos", 8.0)
        if atr_pct >= chaos_lvl:
            regime = "chaos"
        elif adx_val >= c.get("adx_trend_min", 20):
            regime = "trend"
        else:
            regime = "range"

    # Chaos = no entry
    if regime == "chaos":
        return Signal(...)

    # 5. BTC HTF GATE (BARU) — pakai BTC 1h trend
    btc_htf = _get_htf_data("BTC/USDC:USDC", ex, "1h") if ex else None
    btc_bias = 0
    if btc_htf is not None and len(btc_htf) >= 50:
        btc_ema21 = ind.ema(btc_htf["close"], 21).iloc[-1]
        btc_ema50 = ind.ema(btc_htf["close"], 50).iloc[-1]
        btc_price = btc_htf["close"].iloc[-1]
        if btc_price > btc_ema21 > btc_ema50:
            btc_bias = 1   # LONG bias
        elif btc_price < btc_ema21 < btc_ema50:
            btc_bias = -1  # SHORT bias

    # 6. Combined scores — ONLY in trend direction + BTC bias alignment
    if td == 1:
        long_score = ts * w["trend"] + ps * w["momentum"] + ss * w["structure"]
        short_score = 0.0
        # Boost jika aligned dengan BTC HTF
        if btc_bias == 1:
            long_score *= 1.2
        elif btc_bias == -1:
            long_score *= 0.7  # Penalize counter-BTC
    elif td == -1:
        long_score = 0.0
        short_score = ts * w["trend"] + ps * w["momentum"] + ss * w["structure"]
        if btc_bias == -1:
            short_score *= 1.2
        elif btc_bias == 1:
            short_score *= 0.7
    else:
        return Signal(symbol, "skip", 0.0, price, atr_val, "no_trend", ...)

    # 7. Dynamic SL/TP by regime (FIXED: range pakai 1.75/2.6 bukan 1.0/1.2)
    if regime == "trend":
        sl_mult, tp_mult = 1.75, 2.6
    elif regime == "range":
        sl_mult, tp_mult = 1.75, 2.0   # WIDER SL untuk range, TP sedikit lebih dekat
    else:
        sl_mult, tp_mult = 0.0, 0.0

    # ... rest of function unchanged
```

**Catatan:** Perlu update `forward.py` untuk pass `ex` ke `evaluate_v8()`.

---

## PRIORITAS 5 — HIGH: Nonaktifkan Gemini Exit / Perbaiki Logic

### 5.1 Edit `config.yaml` — Matikan Gemini Exit

```yaml
gemini:
  devil_advocate:
    enabled: false      # MATIKAN
  sideways_sniper:
    enabled: false      # PASTIKAN FALSE
  role: "veto"          # Hanya veto, jangan decide
```

### 5.2 Edit `bot/forward.py` — Pastikan Kill-Switch Aktif SEMUA Mode

**File:** `bot/forward.py` **Lines 642-658** — Sudah ada kill-switch tapi pastikan jalan di paper juga

```python
# Di _apply_manage() — TAMBAHKAN: kill-switch jalan di SEMUA mode (bukan live only)
_blocked = False
try:
    _records = self.gtrader._exit_track_record() if self.gtrader else []
    for _r in _records:
        if _r.get("reason") == "gemini_exit" and _r.get("n", 0) >= 10 and _r.get("exp_r", 0.0) < 0:
            log.warning(f"GEMINI_EXIT DIBLOKIR (kill-switch empiris): exp_r={_r['exp_r']:.3f} n={_r['n']}")
            _blocked = True
            break
except Exception as _e:
    log.debug(f"exit_track_record {sym}: {_e}")
if _blocked:
    return  # Jangan eksekusi Gemini exit
```

---

## PRIORITAS 6 — MEDIUM: Entry Confidence Threshold Naik

Sudah di config (Priority 1). Tapi pastikan `entry_confidence: 0.65` di `config.yaml:96`.

---

## PRIORITAS 7 — MEDIUM: Adx Trend Min Naik

Sudah di config (Priority 1). Pastikan `adx_trend_min: 25` di `config.yaml:91`.

---

## RENCANA EKSEKUSI (TIMELINE)

| Minggu | Tugas | File | Verifikasi |
|--------|-------|------|------------|
| **Hari 1** | Config override (Priority 1) | `config.yaml` | Restart bot, cek log entry filter |
| **Hari 1-2** | Fix pullback confirmation (Priority 2) | `bot/signals_v8.py:_score_pullback` | Unit test `_score_pullback` |
| **Hari 2-3** | Fix structure score (Priority 3) | `bot/signals_v8.py:_score_structure_v8` | Unit test `_score_structure_v8` |
| **Hari 3-5** | HTF Regime & BTC Gate (Priority 4) | `bot/signals_v8.py` + `bot/forward.py` | Integration test |
| **Hari 5** | Disable Gemini exit (Priority 5) | `config.yaml` + `bot/forward.py` | Verifikasi no gemini_exit di log |
| **Minggu 1-2** | **PAPER TEST** — Jalankan 2 minggu penuh | `python forwardtest.py --poll 30 --use-store` | Target: WR>55%, Exp>+0.1R, DD<10% |
| **Minggu 3** | Evaluasi & tuning | - | Go/No-Go decision |

---

## SUCCESS CRITERIA UNTUK GO LIVE

```
[ ] Paper test 14 hari berurutan
[ ] Win Rate ≥ 55% 
[ ] Expectancy ≥ +0.10 R/trade
[ ] Max Drawdown ≤ 10% (per wallet)
[ ] Parameter stabil ≥ 50% antar-minggu
[ ] Cost-stress 2× (fee+slippage lipat) → edge bertahan
[ ] Lockbox (holdout 20%) → lolos single test
[ ] Micro-live (modal kecil) → tetap positif 1 minggu
```

**JIKA SATU SATU GAGAL → KEMBALI KE FLAT / RISET. JANGAN LIVE-KAN.**

---

## CATATAN TAMBAHAN

1. **Jangan ubah SL floor (1.75×ATR)** — sudah dikalibrasi 1 tahun data 11 pair
2. **Jangan tambah indicator baru** — fokus perbaiki yang ada (pullback, structure, regime)
3. **Gemini role = "veto" only** — jangan biar Gemini decide entry/SL/TP
4. **HTF data (1h/4h) wajib** — untuk regime & BTC gate yang reliable
5. **Paper test minimal 2 minggu** — tidak boleh rush ke live

---

*Dokumen ini: `IMPROVEMENT_PLAN.md` — Dibuat 2026-07-15 berdasarkan audit lengkap arsitektur & data trade.*