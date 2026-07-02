"""Kalibrasi lantai SL dari DATA (Fix A + bahan bakar 1 tahun).

Pertanyaan yang dijawab: "berapa jauh trade yang AKHIRNYA menang sempat bergerak
MELAWAN dulu?" — itulah jarak SL minimum yang tidak membunuh pemenang.

Metode (entry-agnostik, vektor, tanpa lookahead utk tujuan kalibrasi):
tiap bar dianggap kandidat entry (long & short, simetris). Dalam horizon H bar:
- MFE = gerakan maksimal SEARAH (dlm kelipatan ATR14 saat entry)
- MAE = gerakan maksimal MELAWAN sebelum akhir horizon
'Pemenang' = MFE ≥ tp_mult (target TP bot, default 2.5×ATR). Kuantil MAE para
pemenang = lantai SL: SL di kuantil-80 berarti ~80% calon pemenang selamat.
Subset 'candle besar' (range candle sebelumnya ≥ 2×ATR) dianalisis terpisah —
persis kasus yang dikeluhkan: SL mepet setelah candle raksasa.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import atr as _atr


def _fwd_extrema(high: np.ndarray, low: np.ndarray, horizon: int):
    """max(high[t+1..t+H]) & min(low[t+1..t+H]) per t (NaN di ekor)."""
    n = len(high)
    fmax = np.full(n, np.nan)
    fmin = np.full(n, np.nan)
    if n <= horizon:
        return fmax, fmin
    hw = np.lib.stride_tricks.sliding_window_view(high[1:], horizon)
    lw = np.lib.stride_tricks.sliding_window_view(low[1:], horizon)
    fmax[:n - horizon] = hw.max(axis=1)
    fmin[:n - horizon] = lw.min(axis=1)
    return fmax, fmin


def mae_of_winners(df: pd.DataFrame, horizon: int = 16, tp_mult: float = 2.5,
                   atr_period: int = 14, big_candle_atr: float = 2.0) -> dict:
    """Kembalikan kuantil MAE (dlm ×ATR) para 'pemenang', total & subset
    setelah-candle-besar. PURE atas DataFrame OHLC."""
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    a = _atr(df, atr_period).to_numpy(float)
    fmax, fmin = _fwd_extrema(high, low, horizon)

    prev_range = np.concatenate([[np.nan], (high - low)[:-1]])   # candle TERTUTUP sblm entry
    out = {}
    # long & short simetris: (mfe, mae) short = kebalikan long
    mfe_l, mae_l = (fmax - close) / a, (close - fmin) / a
    mfe_s, mae_s = (close - fmin) / a, (fmax - close) / a
    mfe = np.concatenate([mfe_l, mfe_s])
    mae = np.concatenate([mae_l, mae_s])
    big = np.concatenate([prev_range >= big_candle_atr * a] * 2)
    valid = np.isfinite(mfe) & np.isfinite(mae) & (mae >= 0)
    for name, mask in (("semua", valid), ("setelah_candle_besar", valid & big)):
        w = mae[mask & (mfe >= tp_mult)]
        out[name] = ({"n_winners": int(len(w)),
                      "mae_q50": round(float(np.quantile(w, 0.50)), 3),
                      "mae_q75": round(float(np.quantile(w, 0.75)), 3),
                      "mae_q80": round(float(np.quantile(w, 0.80)), 3),
                      "mae_q90": round(float(np.quantile(w, 0.90)), 3)}
                     if len(w) >= 50 else {"n_winners": int(len(w))})
    return out
