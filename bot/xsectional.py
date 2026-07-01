"""Fase 2 — cross-sectional momentum: edge RELATIF antar-pair, bukan per-simbol.

Hipotesis: pair yang OUTPERFORM peer-nya cenderung lanjut outperform (dan sebaliknya).
Berbeda struktural dari v1–v7 (yang melihat satu simbol terisolasi): di sini kita
me-RANK seluruh universe tiap rebalance → LONG kuantil terkuat, SHORT terlemah
(dollar-neutral). Ini menangkap edge yang tak bisa dilihat framework per-simbol.

Disiplin (sama seperti optimize.py):
- TANPA lookahead: skor pakai return s/d bar t (tertutup); PnL pakai forward-return > t.
- Rebalance NON-OVERLAP (langkah = hold) → sampel independen, tak ada korelasi semu.
- Walk-forward OOS: param dipilih di train, diuji di test yang belum dilihat.
- Low-DOF: hanya lookback & hold yang disweep (kuantil tetap) → sedikit trial.
- Biaya + koreksi multiple-testing diterapkan di verdict (jujur).
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import numpy as np
import pandas as pd


def align_close_panel(dfs: dict[str, pd.DataFrame], min_coverage: float = 0.9) -> pd.DataFrame:
    """Rakit panel close [waktu × simbol] selaras. Union index → ffill dalam tiap
    simbol → buang simbol dgn coverage < min_coverage → buang baris ber-NaN.
    Hasil: hanya periode di mana SEMUA simbol tersisa punya harga (tanpa lookahead)."""
    closes = {s: df["close"] for s, df in dfs.items() if "close" in df and len(df)}
    if not closes:
        return pd.DataFrame()
    panel = pd.DataFrame(closes).sort_index()
    panel = panel.ffill()                                    # isi gap dalam-simbol (causal)
    keep = [s for s in panel.columns if panel[s].notna().mean() >= min_coverage]
    panel = panel[keep].dropna()                             # hanya baris lengkap
    return panel


def _rebalance_times(t0: int, t1: int, lookback: int, hold: int) -> range:
    """Titik rebalance non-overlap di [t0,t1): mulai setelah lookback, langkah = hold,
    sisakan hold bar ke depan untuk forward-return."""
    start = max(t0, lookback)
    return range(start, t1 - hold, hold)


def _xs_step(close: np.ndarray, times: range, lookback: int, hold: int,
             quantile: float, cost_frac: float, reverse: bool) -> tuple[np.ndarray, np.ndarray]:
    """Inti cross-sectional: kembalikan (returns, dispersions) per rebalance.
    dispersion = std skor momentum antar-pair (proxy 'pasar menyebar' → regime)."""
    rets, disps = [], []
    for t in times:
        mom = close[t] / close[t - lookback] - 1.0          # masa lalu (tanpa lookahead)
        fwd = close[t + hold] / close[t] - 1.0              # masa depan (realized)
        valid = np.isfinite(mom) & np.isfinite(fwd)
        n = int(valid.sum())
        if n < 4:                                            # butuh cukup pair utk ranking
            continue
        mv, fv = mom[valid], fwd[valid]
        k = max(1, int(n * quantile))
        order = np.argsort(mv)                               # naik: bawah=terlemah, atas=terkuat
        weak_r, strong_r = fv[order[:k]].mean(), fv[order[-k:]].mean()
        pnl = (weak_r - strong_r) if reverse else (strong_r - weak_r)
        rets.append(pnl - cost_frac)                         # dollar-neutral − biaya round-trip
        disps.append(float(mv.std()))
    return np.asarray(rets, dtype=float), np.asarray(disps, dtype=float)


def xs_returns(close: np.ndarray, times: range, lookback: int, hold: int,
               quantile: float, cost_frac: float, reverse: bool = False) -> np.ndarray:
    """Return long-short per rebalance (MOMENTUM, atau reverse=mean-reversion)."""
    return _xs_step(close, times, lookback, hold, quantile, cost_frac, reverse)[0]


def sharpe(returns: np.ndarray) -> float:
    """Sharpe per-rebalance (mean/std). Bukan tahunan — untuk RANKING param saja."""
    if len(returns) < 2:
        return float("-inf")
    sd = returns.std(ddof=1)
    return float(returns.mean() / sd) if sd > 0 else float("-inf")


def t_pvalue(returns: np.ndarray) -> float:
    """p-value satu-sisi H0: mean ≤ 0 (uji-t). Aproksimasi normal (n biasanya cukup)."""
    n = len(returns)
    if n < 2:
        return 1.0
    sd = returns.std(ddof=1)
    if sd <= 0:
        return 0.0 if returns.mean() > 0 else 1.0
    t = returns.mean() / (sd / np.sqrt(n))
    # survival normal standar via erfc (tanpa scipy)
    from math import erfc, sqrt
    return 0.5 * erfc(t / sqrt(2))


def build_grid(lookbacks, holds) -> list[dict]:
    return [{"lookback": lb, "hold": h} for lb, h in product(lookbacks, holds)]


@dataclass
class XSWindow:
    train: tuple[int, int]
    test: tuple[int, int]
    params: dict
    is_sharpe: float
    is_n: int
    oos_mean: float
    oos_n: int


def walk_forward_xs(close: np.ndarray, grid: list[dict], quantile: float, cost_frac: float,
                    train_len: int, test_len: int, min_rebalances: int = 8,
                    reverse: bool = False):
    """Walk-forward cross-sectional. Pilih (lookback,hold) TERBAIK di train (by Sharpe),
    uji di test. Kembalikan (windows, oos_returns_gabungan)."""
    T = close.shape[0]
    max_lb = max(g["lookback"] for g in grid)
    windows: list[XSWindow] = []
    oos_all: list[float] = []

    start = max_lb
    while start + train_len + test_len <= T:
        tr0, tr1 = start, start + train_len
        te0, te1 = tr1, tr1 + test_len

        best, best_s = None, float("-inf")
        for g in grid:
            times = _rebalance_times(tr0, tr1, g["lookback"], g["hold"])
            r = xs_returns(close, times, g["lookback"], g["hold"], quantile, cost_frac, reverse)
            if len(r) < min_rebalances:
                continue
            s = sharpe(r)
            if s > best_s:
                best, best_s, best_n = g, s, len(r)

        if best is not None:
            te_times = _rebalance_times(te0, te1, best["lookback"], best["hold"])
            te_r = xs_returns(close, te_times, best["lookback"], best["hold"], quantile, cost_frac, reverse)
            oos_all.extend(te_r.tolist())
            windows.append(XSWindow((tr0, tr1), (te0, te1), best, best_s, best_n,
                                    float(te_r.mean()) if len(te_r) else 0.0, len(te_r)))
        start += test_len

    return windows, np.asarray(oos_all, dtype=float)


def walk_forward_xs_regime(close: np.ndarray, grid: list[dict], quantile: float, cost_frac: float,
                           train_len: int, test_len: int, min_rebalances: int = 8,
                           reverse: bool = False):
    """Regime-conditional: HANYA trading saat dispersi antar-pair tinggi (pasar menyebar).
    Threshold = MEDIAN dispersi di TRAIN (dipelajari causal), diterapkan ke OOS — bukan
    tuning bebas. Param (lookback,hold) dipilih dari Sharpe train yang SUDAH difilter regime."""
    T = close.shape[0]
    max_lb = max(g["lookback"] for g in grid)
    windows: list[XSWindow] = []
    oos_all: list[float] = []
    start = max_lb
    while start + train_len + test_len <= T:
        tr0, tr1 = start, start + train_len
        te0, te1 = tr1, tr1 + test_len
        best, best_s, best_thr = None, float("-inf"), 0.0
        for g in grid:
            tr_r, tr_d = _xs_step(close, _rebalance_times(tr0, tr1, g["lookback"], g["hold"]),
                                  g["lookback"], g["hold"], quantile, cost_frac, reverse)
            if len(tr_r) < min_rebalances:
                continue
            thr = float(np.median(tr_d))                    # regime threshold dari TRAIN
            on = tr_r[tr_d >= thr]                           # train difilter regime
            if len(on) < min_rebalances // 2:
                continue
            s = sharpe(on)
            if s > best_s:
                best, best_s, best_thr, best_n = g, s, thr, len(on)
        if best is not None:
            te_r, te_d = _xs_step(close, _rebalance_times(te0, te1, best["lookback"], best["hold"]),
                                  best["lookback"], best["hold"], quantile, cost_frac, reverse)
            te_on = te_r[te_d >= best_thr]                   # OOS difilter threshold TRAIN
            oos_all.extend(te_on.tolist())
            windows.append(XSWindow((tr0, tr1), (te0, te1), best, best_s, best_n,
                                    float(te_on.mean()) if len(te_on) else 0.0, len(te_on)))
        start += test_len
    return windows, np.asarray(oos_all, dtype=float)


def xs_returns_score(close: np.ndarray, score: np.ndarray, times: range, hold: int,
                     quantile: float, cost_frac: float, reverse: bool = False) -> np.ndarray:
    """Return long-short per rebalance dari PANEL SKOR eksternal [T×N] (skor tinggi=long).
    Rank pakai score[t] (≤t), PnL = forward-return hold (>t)."""
    out = []
    for t in times:
        sc = score[t]
        fwd = close[t + hold] / close[t] - 1.0
        valid = np.isfinite(sc) & np.isfinite(fwd)
        n = int(valid.sum())
        if n < 4:
            continue
        sv, fv = sc[valid], fwd[valid]
        k = max(1, int(n * quantile))
        order = np.argsort(sv)
        low, high = fv[order[:k]].mean(), fv[order[-k:]].mean()
        pnl = (low - high) if reverse else (high - low)
        out.append(pnl - cost_frac)
    return np.asarray(out, dtype=float)


def walk_forward_scores(close: np.ndarray, score_panels: dict, holds: list[int],
                        quantile: float, cost_frac: float, train_len: int, test_len: int,
                        min_rebalances: int = 8, reverse: bool = False):
    """Walk-forward generik untuk hipotesis apa pun. score_panels: {nama: [T×N]}.
    Grid = (varian skor) × holds; pilih terbaik di TRAIN (Sharpe), uji OOS."""
    T = close.shape[0]
    warm = 0
    for sp in score_panels.values():
        rows = np.where(np.isfinite(sp).sum(axis=1) >= 4)[0]
        if len(rows):
            warm = max(warm, int(rows[0]))
    grid = [(name, h) for name in score_panels for h in holds]
    windows: list[XSWindow] = []
    oos_all: list[float] = []
    start = warm + 1
    while start + train_len + test_len <= T:
        tr0, tr1 = start, start + train_len
        te0, te1 = tr1, tr1 + test_len
        best, best_s = None, float("-inf")
        for name, h in grid:
            r = xs_returns_score(close, score_panels[name], range(tr0, tr1 - h, h),
                                 h, quantile, cost_frac, reverse)
            if len(r) < min_rebalances:
                continue
            s = sharpe(r)
            if s > best_s:
                best, best_s, best_n = (name, h), s, len(r)
        if best is not None:
            te_r = xs_returns_score(close, score_panels[best[0]], range(te0, te1 - best[1], best[1]),
                                    best[1], quantile, cost_frac, reverse)
            oos_all.extend(te_r.tolist())
            windows.append(XSWindow((tr0, tr1), (te0, te1),
                                    {"score": best[0], "hold": best[1]}, best_s, best_n,
                                    float(te_r.mean()) if len(te_r) else 0.0, len(te_r)))
        start += test_len
    return windows, np.asarray(oos_all, dtype=float)


def verdict(oos: np.ndarray, n_trials: int) -> dict:
    """Verdict jujur: mean OOS > 0 DAN lolos signifikansi setelah koreksi multiple-testing
    (Bonferroni atas n_trials kombinasi param yang dicoba)."""
    n = len(oos)
    if n < 8:
        return {"ok": False, "reason": f"data OOS terlalu sedikit (n={n})",
                "mean": float(oos.mean()) if n else 0.0, "n": n}
    mean = float(oos.mean())
    p = t_pvalue(oos)
    p_adj = min(p * max(1, n_trials), 1.0)
    win = float((oos > 0).mean())
    ok = mean > 0 and p_adj < 0.05
    if mean <= 0:
        reason = f"OOS mean {mean:+.4%} ≤ 0 — tak ada edge cross-sectional"
    elif p_adj >= 0.05:
        reason = (f"OOS mean {mean:+.4%} positif TAPI gagal signifikansi: "
                  f"p_adj={p_adj:.3f} atas {n_trials} trial — kemungkinan artefak")
    else:
        reason = f"OOS mean {mean:+.4%}, p_adj={p_adj:.3f} — LOLOS (kandidat)"
    return {"ok": ok, "reason": reason, "mean": mean, "n": n,
            "win_rate": round(win, 3), "p": round(p, 4), "p_adj": round(p_adj, 4),
            "sharpe": round(sharpe(oos), 3)}
