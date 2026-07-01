"""Fase 2 / H21 — statistical arbitrage: pairs trading spread stasioner.

STRUKTURAL BEDA dari xsectional (rank universe) & carry: di sini kita cari PASANGAN
(i,j) yang spread log-harganya MEAN-REVERTING, lalu fade deviasi z-score dari
ekuilibrium. Long satu, short lainnya (dollar-neutral). Ini stat-arb klasik.

Deteksi mean-reversion via half-life OU (tanpa statsmodels): regresi Δspread pada
spread_lag → koef b; half_life = -ln2/b (valid hanya bila b<0 = mean-reverting).
Disiplin: beta & mu/sd spread dari TRAIN (causal), trading di OOS. Walk-forward,
biaya, koreksi multiple-testing di verdict (reuse xsectional).
"""
from __future__ import annotations

from itertools import combinations, product

import numpy as np

from .xsectional import sharpe, verdict  # reuse metrik & verdict jujur


def hedge_ratio(y: np.ndarray, x: np.ndarray) -> tuple[float, float]:
    """OLS y = a + b*x → (a, b). b = rasio hedge."""
    xm, ym = x.mean(), y.mean()
    denom = ((x - xm) ** 2).sum()
    if denom <= 0:
        return 0.0, 0.0
    b = ((x - xm) * (y - ym)).sum() / denom
    return ym - b * xm, b


def half_life(spread: np.ndarray) -> float:
    """Half-life mean-reversion OU. Δs_t = a + b*s_{t-1}; hl = -ln2/b bila b<0."""
    s = spread[:-1]
    ds = np.diff(spread)
    _, b = hedge_ratio(ds, s)
    if b >= 0:
        return np.inf
    return float(-np.log(2) / b)


def select_pairs(logp: np.ndarray, hl_max: float, min_std: float = 1e-4) -> list:
    """Pilih pasangan (i,j) yang spread-nya mean-reverting di TRAIN. Kembalikan
    daftar (i, j, a, b, mu, sd). logp: [T×N] log-harga TRAIN."""
    N = logp.shape[1]
    out = []
    for i, j in combinations(range(N), 2):
        a, b = hedge_ratio(logp[:, i], logp[:, j])
        if b <= 0:                              # hedge negatif → bukan pasangan wajar
            continue
        spread = logp[:, i] - (a + b * logp[:, j])
        sd = spread.std()
        if sd < min_std:
            continue
        hl = half_life(spread)
        if 1.0 <= hl <= hl_max:                 # mean-revert dalam horizon wajar
            out.append((i, j, a, b, spread.mean(), sd))
    return out


def trade_spread(logp: np.ndarray, pair: tuple, entry_z: float, exit_z: float,
                 cost: float, stop_z: float = np.inf) -> list[float]:
    """Simulasi trading spread pasangan di jendela OOS (logp = log-harga OOS).
    beta & mu/sd dari TRAIN (dalam `pair`). Fade z ekstrem; exit saat balik ke mean
    ATAU stop-loss bila z MELEBAR melewati stop_z (cointegration breakdown → cut rugi).
    Kembalikan return per-trade (unit log-spread ≈ fraksi PnL notional) − biaya."""
    i, j, a, b, mu, sd = pair
    spread = logp[:, i] - (a + b * logp[:, j])
    z = (spread - mu) / sd
    trades, pos, entry_s = [], 0, 0.0
    for t in range(len(z)):
        if pos == 0:
            if z[t] >= entry_z:
                pos, entry_s = -1, spread[t]     # short spread (harap turun)
            elif z[t] <= -entry_z:
                pos, entry_s = 1, spread[t]      # long spread (harap naik)
        else:
            revert = (pos == -1 and z[t] <= exit_z) or (pos == 1 and z[t] >= -exit_z)
            stop = (pos == -1 and z[t] >= stop_z) or (pos == 1 and z[t] <= -stop_z)
            if revert or stop:
                pnl = (entry_s - spread[t]) if pos == -1 else (spread[t] - entry_s)
                trades.append(pnl - cost)
                pos = 0
    if pos != 0:                                 # tutup di akhir jendela
        pnl = (entry_s - spread[-1]) if pos == -1 else (spread[-1] - entry_s)
        trades.append(pnl - cost)
    return trades


def build_grid(entry_list, hl_list) -> list[dict]:
    return [{"entry_z": e, "hl_max": h} for e, h in product(entry_list, hl_list)]


def walk_forward_statarb(logp: np.ndarray, grid: list[dict], cost: float,
                         train_len: int, test_len: int, exit_z: float = 0.0,
                         stop_z: float = np.inf, min_trades: int = 10):
    """Walk-forward stat-arb. Per window: pilih pasangan mean-revert di TRAIN (per
    hl_max), trading OOS dgn entry_z. Pilih grid terbaik by Sharpe train, agregasi OOS.
    stop_z = stop-loss level spread (cut saat cointegration breakdown)."""
    T = logp.shape[0]
    windows, oos_all = [], []
    start = 0
    while start + train_len + test_len <= T:
        tr = logp[start:start + train_len]
        te = logp[start + train_len:start + train_len + test_len]
        pair_cache: dict = {}
        best, best_s, best_meta = None, float("-inf"), None
        for g in grid:
            pairs = pair_cache.setdefault(g["hl_max"], select_pairs(tr, g["hl_max"]))
            if not pairs:
                continue
            tr_trades = []
            for p in pairs:
                tr_trades += trade_spread(tr, p, g["entry_z"], exit_z, cost, stop_z)
            if len(tr_trades) < min_trades:
                continue
            s = sharpe(np.asarray(tr_trades))
            if s > best_s:
                best, best_s, best_meta = g, s, pairs
        if best is not None:
            te_trades = []
            for p in best_meta:
                te_trades += trade_spread(te, p, best["entry_z"], exit_z, cost, stop_z)
            oos_all += te_trades
            windows.append({"train": (start, start + train_len), "params": best,
                            "n_pairs": len(best_meta), "is_sharpe": round(best_s, 3),
                            "oos_mean": float(np.mean(te_trades)) if te_trades else 0.0,
                            "oos_n": len(te_trades)})
        start += test_len
    return windows, np.asarray(oos_all, dtype=float)
