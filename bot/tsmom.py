"""Fase 4 — H32 TSMOM harian per-simbol (penutup lubang formal).

Time-series momentum klasik: posisi per simbol = sign(return `lookback` hari),
equal-weight seluruh universe, rebalance non-overlap tiap `hold` hari. Ini
kategori TA per-simbol yang sudah berulang kali gugur di 15m — diuji sekali
di horizon 1d agar penolakannya formal, bukan asumsi.

Disiplin: sinyal ≤t, PnL >t, walk-forward (lookback dipilih di train), biaya
round-trip penuh per rebalance (konservatif; posisi persisten akan lebih murah,
tapi bila edge mati di asumsi murah pun tak perlu dihitung lebih teliti).
"""
from __future__ import annotations

import numpy as np

from bot.xsectional import XSWindow, sharpe


def tsmom_returns(close: np.ndarray, times: range, lookback: int, hold: int,
                  cost_frac: float) -> np.ndarray:
    """Return portofolio per rebalance: mean_i sign(mom_i) × fwd_i − biaya."""
    T = close.shape[0]
    out = []
    for t in times:
        if t - lookback < 0 or t + hold >= T:
            continue
        mom = close[t] / close[t - lookback] - 1.0
        fwd = close[t + hold] / close[t] - 1.0
        valid = np.isfinite(mom) & np.isfinite(fwd) & (mom != 0)
        if int(valid.sum()) < 4:
            continue
        out.append(float(np.mean(np.sign(mom[valid]) * fwd[valid])) - cost_frac)
    return np.asarray(out, dtype=float)


def walk_forward_tsmom(close: np.ndarray, lookbacks: list[int], hold: int,
                       cost_frac: float, train_len: int, test_len: int,
                       min_rebalances: int = 8):
    """Pilih lookback terbaik di train (Sharpe), uji OOS. n_trials = len(lookbacks)."""
    T = close.shape[0]
    warm = max(lookbacks)
    windows: list[XSWindow] = []
    oos_all: list[float] = []
    start = warm
    while start + train_len + test_len <= T:
        tr0, tr1 = start, start + train_len
        te0, te1 = tr1, tr1 + test_len
        best, best_s = None, float("-inf")
        for lb in lookbacks:
            r = tsmom_returns(close, range(tr0, tr1 - hold, hold), lb, hold, cost_frac)
            if len(r) < min_rebalances:
                continue
            s = sharpe(r)
            if s > best_s:
                best, best_s, best_n = lb, s, len(r)
        if best is not None:
            te_r = tsmom_returns(close, range(te0, te1 - hold, hold), best, hold, cost_frac)
            oos_all.extend(te_r.tolist())
            windows.append(XSWindow((tr0, tr1), (te0, te1), {"lookback": best, "hold": hold},
                                    best_s, best_n,
                                    float(te_r.mean()) if len(te_r) else 0.0, len(te_r)))
        start += test_len
    return windows, np.asarray(oos_all, dtype=float)
