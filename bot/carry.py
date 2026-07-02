"""Fase 2 — funding carry cross-sectional (edge dari funding rate, bukan harga).

Hipotesis: funding rate = biaya carry yang persisten. SHORT pair funding-tinggi
(terima funding), LONG pair funding-rendah/negatif (terima funding dari sisi lain),
dollar-neutral. PnL = income funding REALIZED + PnL harga long-short − biaya.

Risiko klasik yang diuji jujur: funding tinggi sering karena harga lagi pump
(long crowded) → short-carry bisa kelindas harga. Kalau PnL harga menegasikan
income funding → tak ada edge, dan verdict akan menolaknya.

Disiplin identik xsectional.py: skor funding ≤t (causal), income & harga forward >t,
rebalance non-overlap, walk-forward OOS, koreksi multiple-testing di verdict.
"""
from __future__ import annotations

from itertools import product

import numpy as np
import pandas as pd

from .xsectional import XSWindow, sharpe, verdict  # reuse metrik & verdict jujur


def align_funding(funding_dfs: dict[str, pd.Series], price_index: pd.DatetimeIndex,
                  columns: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """Rakit dua panel funding selaras ke index harga (kolom = urutan `columns`):
    - level[t,i]  = rate 8h TERAKHIR yang terbit (ffill, causal) → untuk RANKING.
    - cumf[t,i]   = kumulatif funding yang benar-benar dibebankan s/d t → income
                    realized antar-bar = cumf[t+hold]-cumf[t] (tanpa lookahead)."""
    level = pd.DataFrame(index=price_index)
    charged = pd.DataFrame(index=price_index)
    for s in columns:
        f = funding_dfs.get(s)
        if f is None or len(f) == 0:
            level[s] = 0.0
            charged[s] = 0.0
            continue
        f = f[~f.index.duplicated(keep="last")].sort_index()
        level[s] = f.reindex(price_index, method="ffill").fillna(0.0)   # rate berjalan
        charged[s] = f.reindex(price_index).fillna(0.0)                 # hanya di jam funding
    cumf = charged.cumsum()
    return level.to_numpy(), cumf.to_numpy()


def carry_returns(close: np.ndarray, level: np.ndarray, cumf: np.ndarray, times: range,
                  smooth: int, hold: int, quantile: float, cost_frac: float,
                  mom: np.ndarray | None = None) -> np.ndarray:
    """Return carry per rebalance. Rank pakai mean funding `smooth` bar terakhir (≤t).
    SHORT kuantil funding-tinggi, LONG kuantil funding-rendah. PnL = income funding
    realized (cumf) + PnL harga forward − biaya.

    mom (opsional, H25): panel momentum [T×N] ≤t. Bila diberikan, hanya simbol
    yang momentum-nya BERLAWANAN arah funding yang eligible — memotong failure
    mode carry klasik (short funding-tinggi kelindas pump yang masih berjalan)."""
    out = []
    for t in times:
        sig = level[t - smooth + 1:t + 1].mean(axis=0)      # funding rata2 masa lalu (ranking)
        fwd = close[t + hold] / close[t] - 1.0              # harga forward (realized)
        inc = cumf[t + hold] - cumf[t]                      # funding dibebankan pd (t, t+hold]
        valid = np.isfinite(sig) & np.isfinite(fwd) & np.isfinite(inc)
        if mom is not None:
            valid &= np.isfinite(mom[t]) & (np.sign(mom[t]) * np.sign(sig) < 0)
        n = int(valid.sum())
        if n < 4:
            continue
        sv, fv, iv = sig[valid], fwd[valid], inc[valid]
        k = max(1, int(n * quantile))
        order = np.argsort(sv)                              # naik: bawah=funding rendah, atas=tinggi
        lo, hi = order[:k], order[-k:]                      # LONG lo (funding rendah), SHORT hi
        # income: short pair terima +funding, long pair terima −funding
        funding_income = iv[hi].mean() - iv[lo].mean()
        price_pnl = fv[lo].mean() - fv[hi].mean()           # LONG lo − SHORT hi
        out.append(funding_income + price_pnl - cost_frac)
    return np.asarray(out, dtype=float)


def build_grid(smooths, holds) -> list[dict]:
    return [{"smooth": s, "hold": h} for s, h in product(smooths, holds)]


def _rebalance_times(t0, t1, warm, hold):
    start = max(t0, warm)
    return range(start, t1 - hold, hold)


def walk_forward_carry_mom(close, level, cumf, mom_panels: dict, holds: list[int],
                           smooth: int, quantile: float, cost_frac: float,
                           train_len: int, test_len: int, min_rebalances: int = 8):
    """H25 walk-forward: grid = (lookback momentum) × holds; smooth TETAP (low-DOF).
    Pilih kombinasi terbaik di train (Sharpe), uji OOS. n_trials = len(grid)."""
    T = close.shape[0]
    warm = smooth
    for mp in mom_panels.values():
        rows = np.where(np.isfinite(mp).sum(axis=1) >= 4)[0]
        if len(rows):
            warm = max(warm, int(rows[0]))
    grid = [(name, h) for name in mom_panels for h in holds]
    windows, oos_all = [], []
    start = warm + 1
    while start + train_len + test_len <= T:
        tr0, tr1 = start, start + train_len
        te0, te1 = tr1, tr1 + test_len
        best, best_s = None, float("-inf")
        for name, h in grid:
            times = _rebalance_times(tr0, tr1, start, h)
            r = carry_returns(close, level, cumf, times, smooth, h, quantile,
                              cost_frac, mom=mom_panels[name])
            if len(r) < min_rebalances:
                continue
            s = sharpe(r)
            if s > best_s:
                best, best_s, best_n = (name, h), s, len(r)
        if best is not None:
            te_times = _rebalance_times(te0, te1, te0, best[1])
            te_r = carry_returns(close, level, cumf, te_times, smooth, best[1],
                                 quantile, cost_frac, mom=mom_panels[best[0]])
            oos_all.extend(te_r.tolist())
            windows.append(XSWindow((tr0, tr1), (te0, te1),
                                    {"mom": best[0], "hold": best[1]}, best_s, best_n,
                                    float(te_r.mean()) if len(te_r) else 0.0, len(te_r)))
        start += test_len
    return windows, np.asarray(oos_all, dtype=float)


def walk_forward_carry(close, level, cumf, grid, quantile, cost_frac,
                       train_len, test_len, min_rebalances=8):
    """Walk-forward carry. Pilih (smooth,hold) terbaik di train (Sharpe), uji OOS."""
    T = close.shape[0]
    warm = max(g["smooth"] for g in grid)
    windows, oos_all = [], []
    start = warm
    while start + train_len + test_len <= T:
        tr0, tr1 = start, start + train_len
        te0, te1 = tr1, tr1 + test_len
        best, best_s = None, float("-inf")
        for g in grid:
            times = _rebalance_times(tr0, tr1, g["smooth"], g["hold"])
            r = carry_returns(close, level, cumf, times, g["smooth"], g["hold"], quantile, cost_frac)
            if len(r) < min_rebalances:
                continue
            s = sharpe(r)
            if s > best_s:
                best, best_s, best_n = g, s, len(r)
        if best is not None:
            te_times = _rebalance_times(te0, te1, best["smooth"], best["hold"])
            te_r = carry_returns(close, level, cumf, te_times, best["smooth"], best["hold"],
                                 quantile, cost_frac)
            oos_all.extend(te_r.tolist())
            windows.append(XSWindow((tr0, tr1), (te0, te1), best, best_s, best_n,
                                    float(te_r.mean()) if len(te_r) else 0.0, len(te_r)))
        start += test_len
    return windows, np.asarray(oos_all, dtype=float)
