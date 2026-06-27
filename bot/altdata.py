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
