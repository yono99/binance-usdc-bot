"""Data non-harga untuk alpha: funding rate + open interest (via ccxt).

Catatan penting:
- Funding rate Binance terbit tiap ~8 jam; histori cukup panjang.
- Open Interest histori Binance HANYA ~30 hari terakhir → backtest OI panjang
  tidak mungkin; bar lebih lama dari itu akan ber-OI 0 (fitur non-aktif di sana).
- Semua selaras causal (ffill nilai yang sudah terbit) → tanpa lookahead.
Gagal/again kosong → kembalikan Series kosong (fitur otomatis non-aktif).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .exchange import Exchange
from .logger import log


def fetch_funding(ex: Exchange, symbol: str, since_ms: int, max_points: int = 3000) -> pd.Series:
    out: list = []
    since = since_ms
    try:
        while len(out) < max_points:
            chunk = ex.client.fetch_funding_rate_history(symbol, since=since, limit=1000)
            if not chunk:
                break
            out += chunk
            since = chunk[-1]["timestamp"] + 1
            if len(chunk) < 1000:
                break
    except Exception as e:  # boundary
        log.warning(f"funding {symbol} gagal: {e}")
        return pd.Series(dtype=float)
    if not out:
        return pd.Series(dtype=float)
    ts = [c["timestamp"] for c in out]
    val = [float(c.get("fundingRate") or 0.0) for c in out]
    s = pd.Series(val, index=pd.to_datetime(ts, unit="ms", utc=True)).sort_index()
    return s[~s.index.duplicated(keep="last")]


def fetch_oi(ex: Exchange, symbol: str, timeframe: str, since_ms: int) -> pd.Series:
    try:
        raw = ex.client.fetch_open_interest_history(symbol, timeframe, since=since_ms, limit=500)
    except Exception as e:  # boundary (mis. di luar 30 hari)
        log.warning(f"open interest {symbol} gagal: {e}")
        return pd.Series(dtype=float)
    if not raw:
        return pd.Series(dtype=float)
    ts, val = [], []
    for r in raw:
        v = r.get("openInterestValue") or r.get("openInterestAmount")
        if v is None and isinstance(r.get("info"), dict):
            v = r["info"].get("sumOpenInterestValue") or r["info"].get("sumOpenInterest")
        ts.append(r["timestamp"])
        val.append(float(v or 0.0))
    s = pd.Series(val, index=pd.to_datetime(ts, unit="ms", utc=True)).sort_index()
    return s[~s.index.duplicated(keep="last")]


def funding_zscore(funding_s: pd.Series, window: int) -> pd.Series:
    if funding_s.empty:
        return funding_s
    mean = funding_s.rolling(window, min_periods=5).mean()
    std = funding_s.rolling(window, min_periods=5).std().replace(0, np.nan)
    return ((funding_s - mean) / std).fillna(0.0)


def align(index: pd.DatetimeIndex, s: pd.Series, fill: float = 0.0) -> np.ndarray:
    """Selaraskan series jarang ke index bar (ffill causal)."""
    if s.empty:
        return np.full(len(index), fill, dtype=float)
    return np.nan_to_num(s.reindex(index, method="ffill").to_numpy(), nan=fill)


def oi_delta(index: pd.DatetimeIndex, oi_s: pd.Series, lookback: int) -> np.ndarray:
    if oi_s.empty:
        return np.zeros(len(index), dtype=float)
    aligned = oi_s.reindex(index, method="ffill")
    return np.nan_to_num(aligned.pct_change(lookback).to_numpy(), nan=0.0)


# ----------------------- cross-exchange basis (Binance vs Bybit) -----------------------
# Sumber alpha STRUKTURAL berbeda: bukan turunan OHLCV Binance, melainkan selisih
# harga ANTAR-VENUE. Hipotesis: dislokasi harga lintas-bursa bersifat mean-reverting.
# Bybit linear USDT perp = venue price-discovery paling likuid → jadi acuan.
# Semua causal: basis dihitung dari close bar yang SAMA & sudah tertutup di kedua venue.

try:
    import ccxt
except Exception:  # pragma: no cover
    ccxt = None

_bybit_client = None


def _bybit():
    global _bybit_client
    if _bybit_client is None and ccxt is not None:
        _bybit_client = ccxt.bybit({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    return _bybit_client


def _bybit_symbol(binance_symbol: str) -> str:
    """Petakan simbol Binance USDC-M ke Bybit linear USDT perp (venue acuan).
    'BTC/USDC:USDC' -> 'BTC/USDT:USDT'."""
    base = binance_symbol.split("/")[0]
    return f"{base}/USDT:USDT"


def fetch_bybit_close(symbol: str, timeframe: str, since_ms: int, bars: int) -> pd.Series:
    """Close Bybit linear perp untuk simbol setara, dari REST publik (tanpa auth)."""
    by = _bybit()
    if by is None:
        return pd.Series(dtype=float)
    bsym = _bybit_symbol(symbol)
    tf_ms = by.parse_timeframe(timeframe) * 1000
    since = since_ms
    rows: list = []
    try:
        while len(rows) < bars:
            chunk = by.fetch_ohlcv(bsym, timeframe, since=since, limit=1000)
            if not chunk:
                break
            rows += chunk
            since = chunk[-1][0] + tf_ms
            if len(chunk) < 1000:
                break
    except Exception as e:  # boundary
        log.warning(f"bybit {bsym} gagal: {e}")
        return pd.Series(dtype=float)
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([r[0] for r in rows], unit="ms", utc=True)
    s = pd.Series([float(r[4]) for r in rows], index=idx).sort_index()
    return s[~s.index.duplicated(keep="last")]


# ----------------------- liquidation cascade (proxy OHLCV) -----------------------
# Tidak ada feed likuidasi historis gratis di Binance (@forceOrder hanya real-time).
# Maka cascade dideteksi dari JEJAK OHLCV-nya: bar dengan range ekstrem (k×ATR) +
# lonjakan volume + close kapitulasi (harga terhempas ke salah satu ujung bar).
# Itu tanda deleveraging paksa (likuidasi), bukan tren ber-uang-baru. STRUKTURAL beda
# dari v1-v5: ini fade event volatilitas paksa, bukan filter/tren/antar-venue.
# Semua causal: komponen dihitung dari bar yang SUDAH tertutup; entry di bar berikutnya.

def cascade_components(df: pd.DataFrame, atr_arr: np.ndarray,
                       vol_lookback: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(range_atr, vol_ratio, close_loc) per bar — independen dari ambang (untuk cache grid).
    - range_atr = (high-low)/ATR  → seberapa ekstrem bar dalam satuan ATR
    - vol_ratio = volume / SMA(volume, lookback)
    - close_loc = (close-low)/(high-low) ∈ [0,1]; 0 = tutup di dasar (kapitulasi turun),
      1 = tutup di puncak (kapitulasi naik)."""
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    rng = high - low
    atr_safe = np.where(atr_arr > 0, atr_arr, np.nan)
    range_atr = np.nan_to_num(rng / atr_safe, nan=0.0)

    vol = df["volume"]
    vol_ma = vol.rolling(vol_lookback, min_periods=max(3, vol_lookback // 2)).mean().to_numpy()
    vol_ratio = np.nan_to_num(vol.to_numpy() / np.where(vol_ma > 0, vol_ma, np.nan), nan=0.0)

    rng_safe = np.where(rng > 0, rng, np.nan)
    close_loc = np.nan_to_num((close - low) / rng_safe, nan=0.5)
    return range_atr, vol_ratio, close_loc


def basis_zscore(binance_close: pd.Series, bybit_close: pd.Series, window: int) -> np.ndarray:
    """z-score basis (bps) Binance vs Bybit, selaras causal ke index Binance.

    basis_bps = (binance - bybit)/bybit * 1e4. Offset persisten (mis. USDC/USDT)
    diserap oleh rolling-mean; hanya DEVIASI yang jadi sinyal. shift(1) opsional tidak
    diperlukan karena basis di bar t hanya pakai close t (sudah tertutup saat entry bar t+1)."""
    if bybit_close.empty:
        return np.zeros(len(binance_close), dtype=float)
    bb = bybit_close.reindex(binance_close.index, method="ffill")
    basis = (binance_close - bb) / bb * 1e4
    mean = basis.rolling(window, min_periods=max(5, window // 3)).mean()
    std = basis.rolling(window, min_periods=max(5, window // 3)).std().replace(0, np.nan)
    z = ((basis - mean) / std)
    return np.nan_to_num(z.to_numpy(), nan=0.0)


# --------------------------------------------------------------------------
# Dominansi BTC (mother coin) — gerbang direction-aware yang dipakai SEMUA teknik.
# Prinsip: BTC pemimpin pasar; alt ber-beta ikut. Saat BTC bergerak KUAT, entri
# LAWAN arah BTC berbahaya (long saat BTC dump, short saat BTC pump) → blok/diskon.
# Entri SEARAH BTC atau saat gerak BTC kecil → lolos penuh. Satu sumber kebenaran
# untuk signals.evaluate (live) maupun strategy_lab decide_v* (backtest vektor).
# --------------------------------------------------------------------------

def btc_gate(side: int, btc_ret_pct: float | None, cfg: dict) -> dict:
    """Gerbang dominansi BTC untuk SATU keputusan (skalar, jalur live).

    side: +1 long, -1 short, 0 skip. btc_ret_pct: gerak % BTC pada bar tertutup
    (mis. -1.5). Kembalikan {allow, size_factor, reason}. Hanya entri lawan-arah
    BTC saat |gerak| ≥ ambang yang terpengaruh."""
    b = cfg.get("btc", {})
    if not b.get("enabled", True) or side == 0 or btc_ret_pct is None:
        return {"allow": True, "size_factor": 1.0, "reason": ""}
    thr = float(b.get("dump_pct", 0.5))
    if abs(btc_ret_pct) < thr:
        return {"allow": True, "size_factor": 1.0, "reason": ""}
    btc_dir = 1 if btc_ret_pct > 0 else -1
    if side == btc_dir:                       # searah pemimpin → aman
        return {"allow": True, "size_factor": 1.0, "reason": ""}
    side_str = "long" if side == 1 else "short"
    if b.get("block_counter", True):          # mode blok (default)
        return {"allow": False, "size_factor": 0.0,
                "reason": f"btc_counter({btc_ret_pct:+.2f}% vs {side_str})"}
    floor = float(b.get("size_floor", 0.4))   # mode diskon size
    over = min(abs(btc_ret_pct) / (thr * 3), 1.0)
    factor = max(round(1.0 - over, 3), floor)
    return {"allow": True, "size_factor": factor,
            "reason": f"btc_counter_discount({factor:.2f} @ {btc_ret_pct:+.2f}%)"}


def btc_gate_side(side_arr: np.ndarray, btc_ret_arr: np.ndarray, cfg: dict) -> np.ndarray:
    """Versi VEKTOR untuk teknik backtest: nol-kan entri lawan-arah BTC saat BTC
    bergerak kuat. side_arr ∈ {-1,0,1}, btc_ret_arr = gerak % BTC selaras per bar.
    Mode blok saja (backtest); size-discount tak relevan di sinyal biner."""
    b = cfg.get("btc", {})
    if not b.get("enabled", True):
        return side_arr
    thr = float(b.get("dump_pct", 0.5))
    strong = np.abs(btc_ret_arr) >= thr
    btc_dir = np.sign(btc_ret_arr).astype(int)
    counter = strong & (side_arr != 0) & (side_arr != btc_dir)  # lawan arah BTC kuat
    return np.where(counter, 0, side_arr).astype(int)


def btc_ret_arr(btc_close: pd.Series | None, index: pd.DatetimeIndex,
                bars: int = 1) -> np.ndarray:
    """Deret gerak % BTC per bar, selaras causal ke `index` (untuk backtest vektor).
    ret di bar t = (close_t/close_{t-bars}-1)*100, sudah diketahui saat close t →
    entry bar t+1 tanpa lookahead. BTC kosong → nol (gerbang non-aktif)."""
    if btc_close is None or btc_close.empty:
        return np.zeros(len(index), dtype=float)
    bc = btc_close.reindex(index, method="ffill")
    ret = bc.pct_change(bars) * 100
    return np.nan_to_num(ret.to_numpy(), nan=0.0)


def btc_ret_pct(btc_df: pd.DataFrame | None, bars: int = 1) -> float | None:
    """Gerak % BTC pada bar TERTUTUP (default 1 bar). btc_df = OHLCV BTC hingga bar
    berjalan; pakai iloc[-2] sebagai bar tertutup terakhir (tanpa lookahead)."""
    if btc_df is None or len(btc_df) < bars + 2:
        return None
    c = btc_df["close"]
    last, base = float(c.iloc[-2]), float(c.iloc[-2 - bars])
    return round((last / base - 1) * 100, 3) if base else None
