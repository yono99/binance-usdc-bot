"""Fase 2 — combiner multi-sinyal: gabungkan beberapa sinyal weak-positif yang
TAK berkorelasi jadi satu portfolio, uji signifikansi pada LOCKBOX (bukan tiap
sinyal). Ini cara benar mengejar ide multi-strategi.

Disiplin ANTI p-hacking:
- Semua sinyal di-rebalance pada JADWAL SAMA (hold tetap) → deret return teraligned
  → korelasi & portfolio benar.
- Seleksi (positif + sign-stability + korelasi rendah) HANYA di TRAIN.
- LOCKBOX (segmen akhir) tak pernah disentuh saat seleksi; portfolio final diuji
  di sana SEKALI. Ini penawar selection-bias.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .xsectional import sharpe, verdict


def score_series(close: np.ndarray, score: np.ndarray, times, hold: int,
                 quantile: float, cost_frac: float, reverse: bool = False) -> np.ndarray:
    """Return long-short per rebalance, TERALIGNED ke `times` (NaN bila <4 pair valid).
    Semua sinyal pakai `times` & `hold` yang sama → bisa distack jadi matriks."""
    out = np.full(len(times), np.nan)
    for k, t in enumerate(times):
        if t + hold >= close.shape[0]:
            continue
        sc = score[t]
        fwd = close[t + hold] / close[t] - 1.0
        valid = np.isfinite(sc) & np.isfinite(fwd)
        n = int(valid.sum())
        if n < 4:
            continue
        sv, fv = sc[valid], fwd[valid]
        kk = max(1, int(n * quantile))
        order = np.argsort(sv)
        low, high = fv[order[:kk]].mean(), fv[order[-kk:]].mean()
        pnl = (low - high) if reverse else (high - low)
        out[k] = pnl - cost_frac
    return out


def build_matrix(close: np.ndarray, signals: dict, times, hold: int,
                 quantile: float, cost_frac: float) -> pd.DataFrame:
    """signals: {nama: score_panel [T×N]}. Kembalikan DataFrame [rebalance × sinyal]
    return, hanya baris di mana SEMUA sinyal valid."""
    cols = {name: score_series(close, sp, times, hold, quantile, cost_frac)
            for name, sp in signals.items()}
    return pd.DataFrame(cols, index=list(times)).dropna()


def _block_stable(x: np.ndarray, min_frac: float, n_blocks: int) -> bool:
    """Sinyal 'stabil' bila mean>0 DAN mean positif di mayoritas sub-blok train
    (bukan disetir 1 periode). Penawar lebih kuat vs artefak daripada pos-frac
    per-rebalance."""
    if x.mean() <= 0:
        return False
    blocks = [b for b in np.array_split(x, n_blocks) if len(b)]
    return np.mean([b.mean() > 0 for b in blocks]) >= min_frac


def select_signals(train: pd.DataFrame, min_block_frac: float = 0.6,
                   corr_max: float = 0.3, n_blocks: int = 4) -> list[str]:
    """Pilih sinyal: (1) mean>0 & stabil-tanda per-BLOK di TRAIN, lalu (2) greedy
    tambah by Sharpe train jika |korelasi| ke yang terpilih < corr_max."""
    keep = [c for c in train.columns
            if _block_stable(train[c].to_numpy(), min_block_frac, n_blocks)]
    if not keep:
        return []
    ranked = sorted(keep, key=lambda c: sharpe(train[c].to_numpy()), reverse=True)
    corr = train[keep].corr()
    sel = [ranked[0]]
    for c in ranked[1:]:
        if all(abs(corr.loc[c, s]) < corr_max for s in sel):
            sel.append(c)
    return sel


def combine(df: pd.DataFrame, selected: list[str], weights: str = "equal") -> np.ndarray:
    """Portfolio dari sinyal terpilih. equal = rata-rata; invvol = inverse-vol."""
    if not selected:
        return np.asarray([])
    sub = df[selected]
    if weights == "invvol":
        w = 1.0 / sub.std().replace(0, np.nan)
        w = (w / w.sum()).fillna(0.0)
        return (sub * w).sum(axis=1).to_numpy()
    return sub.mean(axis=1).to_numpy()


def run_combiner(close: np.ndarray, signals: dict, hold: int, quantile: float,
                 cost_frac: float, lockbox_frac: float = 0.25, warm: int = 0,
                 weights: str = "equal") -> dict:
    """Pipeline penuh: bangun matriks → split train/lockbox → seleksi di TRAIN →
    uji portfolio di LOCKBOX. n_trials = jumlah sinyal disaring (koreksi jujur)."""
    T = close.shape[0]
    times = list(range(warm, T - hold, hold))
    mat = build_matrix(close, signals, times, hold, quantile, cost_frac)
    if len(mat) < 20:
        return {"ok": False, "reason": f"data rebalance terlalu sedikit ({len(mat)})"}
    cut = int(len(mat) * (1 - lockbox_frac))
    train, lockbox = mat.iloc[:cut], mat.iloc[cut:]
    selected = select_signals(train, corr_max=0.3)
    if not selected:
        return {"ok": False, "reason": "tak ada sinyal weak-positif stabil di train",
                "selected": [], "train_means": train.mean().round(5).to_dict()}
    combined_lb = combine(lockbox, selected, weights)
    best_single_lb = max((lockbox[s].mean() for s in selected))
    v = verdict(combined_lb, n_trials=len(signals))
    v.update({
        "selected": selected,
        "combined_mean_lockbox": round(float(np.mean(combined_lb)), 5),
        "combined_sharpe_lockbox": round(sharpe(combined_lb), 3),
        "best_single_mean_lockbox": round(float(best_single_lb), 5),
        "diversifies": bool(np.mean(combined_lb) > best_single_lb),  # gabungan > tunggal terbaik?
        "train_corr": train[selected].corr().round(2).to_dict(),
        "train_means": train.mean().round(5).to_dict(),
        "n_lockbox": len(lockbox),
    })
    return v
