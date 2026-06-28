"""Lapisan signifikansi statistik — pertahanan inti melawan FALSE POSITIVE.

Gerbang lama menerima 'exp_R > 0.05' berdasarkan TANDA saja — persis yang dieksploitasi
multiple-testing: uji cukup banyak ide/parameter, satu lolos karena kebetulan. Modul ini
menambah bukti statistik yang benar:

1. block_bootstrap_pvalue — uji satu-sisi H0: E[R] ≤ 0, dengan MOVING BLOCK bootstrap
   agar autokorelasi (trade berkorelasi dalam satu regime) tidak memalsukan signifikansi.
2. effective_sample_size — 'n' efektif setelah memperhitungkan autokorelasi; melawan
   n palsu dari sinyal level-triggered (mis. funding yang konstan sepanjang regime).
3. bonferroni_significant — koreksi p-value dengan JUMLAH TRIAL kumulatif. Makin banyak
   hipotesis/parameter diuji sepanjang program, makin ketat ambangnya.

Semua deterministik (seed tetap) → hasil reproducible.
"""
from __future__ import annotations

import numpy as np


def effective_sample_size(rs) -> float:
    """n efektif = n / (1 + 2 Σ ρ_k), dipotong di autokorelasi non-positif pertama."""
    rs = np.asarray(rs, dtype=float)
    n = len(rs)
    if n < 2:
        return float(n)
    x = rs - rs.mean()
    var = float(np.dot(x, x) / n)
    if var <= 0:
        return float(n)
    s = 0.0
    for k in range(1, n):
        rho = float(np.dot(x[:-k], x[k:]) / (n * var))
        if rho <= 0:
            break
        s += (1.0 - k / n) * rho
    return n / (1.0 + 2.0 * s)


def block_bootstrap_pvalue(rs, n_boot: int = 5000, block: int = 10, seed: int = 12345) -> float:
    """p-value satu-sisi untuk H0: E[R] ≤ 0 (alternatif: edge positif).

    Moving block bootstrap menjaga struktur autokorelasi. p = fraksi rata-rata
    bootstrap yang ≤ 0; makin kecil p makin kuat bukti mean R > 0."""
    rs = np.asarray(rs, dtype=float)
    n = len(rs)
    if n < 2:
        return 1.0
    if rs.mean() <= 0:
        return 1.0  # tak mungkin signifikan positif
    block = max(1, min(block, n))
    n_blocks = int(np.ceil(n / block))
    max_start = n - block
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot)
    for i in range(n_boot):
        starts = rng.integers(0, max_start + 1, size=n_blocks)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n]
        means[i] = rs[idx].mean()
    # estimator (b+1)/(B+1): p tak pernah 0 (floor ~1/B), agar koreksi Bonferroni bermakna.
    return float((np.sum(means <= 0.0) + 1) / (n_boot + 1))


def bonferroni_significant(p_value: float, n_trials: int, alpha: float = 0.05) -> bool:
    """Signifikan setelah koreksi Bonferroni atas jumlah trial kumulatif."""
    return p_value * max(int(n_trials), 1) < alpha


def mean_ci(rs, alpha: float = 0.05, n_boot: int = 5000, block: int = 10,
            seed: int = 12345) -> tuple[float, float]:
    """Interval kepercayaan (percentile, block bootstrap) untuk mean R."""
    rs = np.asarray(rs, dtype=float)
    n = len(rs)
    if n < 2:
        return (float("nan"), float("nan"))
    block = max(1, min(block, n))
    n_blocks = int(np.ceil(n / block))
    max_start = n - block
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot)
    for i in range(n_boot):
        starts = rng.integers(0, max_start + 1, size=n_blocks)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n]
        means[i] = rs[idx].mean()
    lo = float(np.percentile(means, 100 * alpha / 2))
    hi = float(np.percentile(means, 100 * (1 - alpha / 2)))
    return (lo, hi)


def significance_report(rs, n_trials: int = 1, alpha: float = 0.05) -> dict:
    """Ringkasan signifikansi siap dipakai gerbang verdict."""
    rs = np.asarray(rs, dtype=float)
    if len(rs) < 2:
        return {"n": len(rs), "eff_n": float(len(rs)), "mean_r": float(rs.mean()) if len(rs) else 0.0,
                "p_value": 1.0, "p_adj": 1.0, "ci": (float("nan"), float("nan")),
                "significant": False, "n_trials": n_trials}
    p = block_bootstrap_pvalue(rs)
    eff_n = effective_sample_size(rs)
    p_adj = min(1.0, p * max(int(n_trials), 1))
    return {
        "n": int(len(rs)),
        "eff_n": round(eff_n, 1),
        "mean_r": float(rs.mean()),
        "p_value": round(p, 4),
        "p_adj": round(p_adj, 4),
        "ci": mean_ci(rs, alpha=alpha),
        "significant": bool(p_adj < alpha),
        "n_trials": int(n_trials),
    }
