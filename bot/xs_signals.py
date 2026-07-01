"""Fase 2 — builder SKOR cross-sectional (panel [T×N]) untuk banyak hipotesis.

Satu engine skor generik (xsectional.walk_forward_scores) menguji panel skor apa pun:
skor tinggi → LONG, skor rendah → SHORT. Modul ini merakit skor per hipotesis,
semuanya CAUSAL (skor di bar t hanya pakai data ≤t) — engine yang menambah forward
return (>t). BTC dipakai sebagai leader untuk beta/residual (kolom btc_idx).
"""
from __future__ import annotations

import numpy as np


def returns_panel(close: np.ndarray) -> np.ndarray:
    """Return sederhana per bar [T×N]; baris 0 = NaN."""
    r = np.full_like(close, np.nan, dtype=float)
    r[1:] = close[1:] / close[:-1] - 1.0
    return r


def rolling_beta(r: np.ndarray, rb: np.ndarray, window: int) -> np.ndarray:
    """Beta tiap kolom vs rb (return BTC) atas window trailing [t-window:t] (≤t-1,
    causal). [T×N], NaN sebelum cukup data."""
    T, N = r.shape
    beta = np.full((T, N), np.nan)
    for t in range(window, T):
        x = rb[t - window:t]
        xm = x - np.nanmean(x)
        denom = np.nansum(xm * xm)
        if not np.isfinite(denom) or denom <= 0:
            continue
        Y = r[t - window:t]
        Ym = Y - np.nanmean(Y, axis=0)
        beta[t] = np.nansum(xm[:, None] * Ym, axis=0) / denom
    return beta


def residual_returns(r: np.ndarray, rb: np.ndarray, beta: np.ndarray) -> np.ndarray:
    """Return idiosyncratik: r - beta*rb (buang komponen pasar/BTC). Causal:
    beta[t] pakai ≤t-1, r[t] & rb[t] diketahui saat close t."""
    return r - beta * rb[:, None]


def _roll_sum(a: np.ndarray, window: int) -> np.ndarray:
    """Jumlah trailing termasuk baris t (inklusif): out[t]=sum(a[t-window+1:t+1])."""
    T, N = a.shape
    out = np.full((T, N), np.nan)
    for t in range(window - 1, T):
        out[t] = np.nansum(a[t - window + 1:t + 1], axis=0)
    return out


def _roll_std(a: np.ndarray, window: int) -> np.ndarray:
    T, N = a.shape
    out = np.full((T, N), np.nan)
    for t in range(window - 1, T):
        out[t] = np.nanstd(a[t - window + 1:t + 1], axis=0)
    return out


def _roll_skew(a: np.ndarray, window: int) -> np.ndarray:
    T, N = a.shape
    out = np.full((T, N), np.nan)
    for t in range(window - 1, T):
        w = a[t - window + 1:t + 1]
        m = np.nanmean(w, axis=0)
        s = np.nanstd(w, axis=0)
        z = (w - m) / np.where(s > 0, s, np.nan)
        out[t] = np.nanmean(z ** 3, axis=0)
    return out


# ---------------------- BUILDER PER HIPOTESIS ----------------------

def score_residual_momentum(close, btc_idx, lookback, beta_window):
    """H3: momentum RESIDUAL (buang beta-BTC dulu). Skor = jumlah residual return
    lookback terakhir. Long residual-momentum tinggi, short rendah."""
    r = returns_panel(close)
    beta = rolling_beta(r, r[:, btc_idx], beta_window)
    resid = residual_returns(r, r[:, btc_idx], beta)
    return _roll_sum(resid, lookback)


def score_btc_leadlag(close, btc_idx, beta_window):
    """H2: BTC lead-lag. Skor = beta × return BTC bar terakhir (prediksi gerak alt
    berikutnya dari gerak BTC). Long yang diprediksi naik ikut BTC."""
    r = returns_panel(close)
    beta = rolling_beta(r, r[:, btc_idx], beta_window)
    return beta * r[:, btc_idx][:, None]


def score_ivol(close, btc_idx, ivol_window, beta_window):
    """H6: idiosyncratic-vol premium. Skor = −ivol (short high-ivol, long low-ivol).
    ivol = std residual (setelah buang beta-BTC)."""
    r = returns_panel(close)
    beta = rolling_beta(r, r[:, btc_idx], beta_window)
    resid = residual_returns(r, r[:, btc_idx], beta)
    return -_roll_std(resid, ivol_window)


def score_skew(close, window):
    """H18: skewness premium (lottery bias). Skor = −skew (short high-skew)."""
    return -_roll_skew(returns_panel(close), window)


def score_funding_accel(level, interval):
    """H15: akselerasi funding. level=[T×N] rate 8h ffilled. velocity = beda antar
    interval; accel = beda velocity. Skor: short saat crowding funding mengakselerasi
    searah funding (funding>0 & accel>0 → short); long kebalikannya."""
    T, N = level.shape
    vel = np.full((T, N), np.nan)
    vel[interval:] = level[interval:] - level[:-interval]
    accel = np.full((T, N), np.nan)
    accel[interval:] = vel[interval:] - vel[:-interval]
    crowd = accel * np.sign(level)                  # >0: crowding menguat searah funding
    return -np.sign(level) * np.maximum(crowd, 0)   # funding>0&menguat → skor<0 (short)
