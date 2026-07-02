"""Fase 3 — H14 listing-age lifecycle: mispricing pair yang baru listing.

Hipotesis: pair yang baru listing (<60–90 hari) diperdagangkan tak-efisien —
overreaction fade atau underreaction drift. Unit analisisnya BUKAN harga/waktu
kalender, tapi UMUR-SEJAK-LISTING → orthogonal dari semua hipotesis Fase 1–2.

Validasi: COHORT WALK-FORWARD. Simbol diurutkan kronologis by tanggal listing;
window umur + arah dipilih HANYA dari kohort awal (train), diuji pada kohort
yang listing BELAKANGAN (test) — simbol test tak pernah memengaruhi seleksi.
Ini analog walk-forward waktu, tapi atas sumbu listing-date (tiap simbol = 1
sampel independen; tak ada overlap kalender yang bias di uji-t antar simbol
yang listing berjauhan).

Peringatan handoff: bila tanggal listing terkumpul dalam batch sempit, variance
kohort kecil → studi gagal by construction. `dispersion_report` memeriksa ini
DULU sebelum buang waktu.

Sensor kiri: bila histori yang di-fetch mentok di batas `bars` (len == bars),
bar pertama ≠ tanggal listing → simbol dikeluarkan (`uncensored`).
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from math import sqrt

import numpy as np
import pandas as pd


def listing_dates(dfs: dict[str, pd.DataFrame]) -> pd.Series:
    """Proxy tanggal listing = timestamp bar pertama histori penuh per simbol."""
    return pd.Series({s: df.index[0] for s, df in dfs.items() if len(df)}).sort_values()


def uncensored(dfs: dict[str, pd.DataFrame], requested_bars: int) -> dict[str, pd.DataFrame]:
    """Buang simbol yang historinya terpotong batas fetch (len ≥ requested_bars):
    bar pertamanya bukan listing sungguhan (sensor kiri) → umur tak diketahui."""
    return {s: df for s, df in dfs.items() if len(df) < requested_bars}


def dispersion_report(dates: pd.Series, min_span_days: int = 365,
                      min_symbols: int = 20) -> dict:
    """Cek kelayakan studi SEBELUM uji: butuh cukup simbol + sebaran tanggal
    listing lebar (kalau batch sempit → kohort train/test tak independen)."""
    n = len(dates)
    if n == 0:
        return {"ok": False, "n": 0, "span_days": 0.0,
                "reason": "tak ada simbol dengan listing date"}
    span = float((dates.max() - dates.min()).days)
    ok = n >= min_symbols and span >= min_span_days
    reason = ("layak" if ok else
              f"n={n} (butuh ≥{min_symbols}) / span={span:.0f}hr (butuh ≥{min_span_days})"
              " — variance listing-date terlalu kecil, studi gagal by construction")
    return {"ok": ok, "n": n, "span_days": span, "reason": reason}


def age_return_panel(dfs: dict[str, pd.DataFrame], max_age: int) -> tuple[np.ndarray, list[str]]:
    """Panel return harian by UMUR [S × max_age]: baris s kolom a = return
    close(a)→close(a+1) hari ke-a sejak listing. NaN bila histori < umur itu."""
    symbols = list(dfs.keys())
    out = np.full((len(symbols), max_age), np.nan)
    for si, s in enumerate(symbols):
        c = dfs[s]["close"].to_numpy(dtype=float)
        n = min(len(c) - 1, max_age)
        if n > 0:
            out[si, :n] = c[1:n + 1] / c[:n] - 1.0
    return out, symbols


def window_return(age_rets: np.ndarray, start: int, length: int) -> np.ndarray:
    """Return majemuk per-simbol atas window umur [start, start+length). NaN bila
    ada hari hilang di window (histori simbol belum sampai umur itu)."""
    w = age_rets[:, start:start + length]
    full = np.isfinite(w).all(axis=1) & (w.shape[1] == length)
    out = np.full(age_rets.shape[0], np.nan)
    if w.shape[1] == length:
        out[full] = np.prod(1.0 + w[full], axis=1) - 1.0
    return out


def build_grid(starts: list[int], lengths: list[int]) -> list[dict]:
    return [{"start": s, "length": ln} for s, ln in product(starts, lengths)]


@dataclass
class CohortResult:
    params: dict          # window umur terbaik (dipilih di kohort train)
    direction: int        # +1 long / −1 short (tanda mean train)
    train_mean: float
    train_n: int
    test_returns: np.ndarray  # per simbol test, sudah × direction − cost
    n_trials: int         # utk koreksi Bonferroni: len(grid) × 2 arah


def cohort_walk_forward(age_rets: np.ndarray, symbols: list[str], dates: pd.Series,
                        grid: list[dict], cost_frac: float, train_frac: float = 0.6,
                        min_train: int = 10, min_test: int = 8) -> CohortResult | None:
    """Pilih (window umur, arah) TERBAIK by |t-stat| di kohort listing AWAL,
    uji sekali di kohort listing AKHIR. cost_frac = biaya round-trip 1 trade."""
    order = [symbols.index(s) for s in dates.index if s in symbols]
    cut = int(len(order) * train_frac)
    tr_idx, te_idx = order[:cut], order[cut:]
    if len(tr_idx) < min_train or len(te_idx) < min_test:
        return None

    best, best_t, best_dir, best_mean, best_n = None, 0.0, 0, 0.0, 0
    for g in grid:
        wr = window_return(age_rets, g["start"], g["length"])[tr_idx]
        wr = wr[np.isfinite(wr)]
        if len(wr) < min_train:
            continue
        sd = wr.std(ddof=1)
        if sd <= 0:
            continue
        t = float(wr.mean() / (sd / sqrt(len(wr))))
        if abs(t) > abs(best_t):
            best, best_t = g, t
            best_dir = 1 if wr.mean() > 0 else -1
            best_mean, best_n = float(wr.mean()), len(wr)
    if best is None:
        return None

    te = window_return(age_rets, best["start"], best["length"])[te_idx]
    te = te[np.isfinite(te)] * best_dir - cost_frac
    return CohortResult(best, best_dir, best_mean, best_n, te, n_trials=2 * len(grid))
