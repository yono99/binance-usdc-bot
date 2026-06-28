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
