"""Fase 4 — H24 seasonality settlement funding: flow mekanis di 00/08/16 UTC.

Hipotesis: menjelang settlement, pemegang posisi sisi-pembayar menutup posisi
untuk menghindari pembayaran funding → tekanan harga yang waktunya TERJADWAL dan
arahnya diberikan tanda funding (yang sudah diketahui sebelum settlement).

Implementasi: long-short dollar-neutral, rebalance TEPAT di bar pra-settlement
(bukan grid langkah-tetap — grid tetap bisa tak pernah menyentuh jam settlement).
Skor = −funding_level (short funding tertinggi, long funding paling negatif).
PnL = return harga + funding yang BENAR-BENAR dibebankan selama hold (via panel
cumf dari carry.align_funding — long membayar cumf, short menerimanya).

Causal: level[t] = rate 8h TERAKHIR yang terbit (ffill ≤ t); harga masuk = close
bar t; semua PnL dari data > t. Walk-forward: (offset, hold) dipilih di train.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from bot.xsectional import XSWindow, sharpe

SETTLE_EVERY_H = 8  # settlement Binance tiap 8 jam: 00/08/16 UTC


def presettle_times(index: pd.DatetimeIndex, offset_hours: int) -> np.ndarray:
    """Indeks bar 1h (open-time) yang CLOSE-nya tepat `offset_hours` jam sebelum
    settlement. offset 0 → close pas di settlement (open-hour 23/7/15)."""
    hrs = np.asarray(index.hour)
    return np.where((hrs + 1 + offset_hours) % SETTLE_EVERY_H == 0)[0]


def settlement_ls_returns(close: np.ndarray, level: np.ndarray, cumf: np.ndarray,
                          times: np.ndarray, hold: int, quantile: float,
                          cost_frac: float) -> np.ndarray:
    """Return long-short per rebalance pra-settlement. Long = funding paling
    negatif (menerima funding), short = funding tertinggi (fade crowding).
    PnL sisi long = Δharga − funding dibebankan; short = kebalikannya."""
    T = close.shape[0]
    out = []
    for t in times:
        if t + hold >= T:
            continue
        sc = -level[t]
        fwd = close[t + hold] / close[t] - 1.0
        fchg = cumf[t + hold] - cumf[t]                    # dibebankan ke LONG
        valid = np.isfinite(sc) & np.isfinite(fwd) & np.isfinite(fchg)
        n = int(valid.sum())
        if n < 4:
            continue
        sv, fv, fc = sc[valid], fwd[valid], fchg[valid]
        k = max(1, int(n * quantile))
        order = np.argsort(sv)
        lo, hi = order[:k], order[-k:]                     # hi = skor tinggi = LONG
        pnl_long = (fv[hi] - fc[hi]).mean()
        pnl_short = -(fv[lo] - fc[lo]).mean()
        out.append(pnl_long + pnl_short - cost_frac)
    return np.asarray(out, dtype=float)


def walk_forward_settlement(close: np.ndarray, level: np.ndarray, cumf: np.ndarray,
                            index: pd.DatetimeIndex, offsets: list[int], holds: list[int],
                            quantile: float, cost_frac: float, train_len: int,
                            test_len: int, min_rebalances: int = 8):
    """Walk-forward: (offset, hold) terbaik by Sharpe di train, uji di test.
    Kembalikan (windows, oos_returns). n_trials = len(offsets)×len(holds)."""
    T = close.shape[0]
    times_by_off = {o: presettle_times(index, o) for o in offsets}
    warm_rows = np.where(np.isfinite(level).sum(axis=1) >= 4)[0]
    warm = int(warm_rows[0]) if len(warm_rows) else 0
    windows: list[XSWindow] = []
    oos_all: list[float] = []
    start = warm + 1
    while start + train_len + test_len <= T:
        tr0, tr1 = start, start + train_len
        te0, te1 = tr1, tr1 + test_len
        best, best_s = None, float("-inf")
        for o in offsets:
            tt = times_by_off[o]
            tr_times = tt[(tt >= tr0) & (tt < tr1)]
            for h in holds:
                r = settlement_ls_returns(close, level, cumf, tr_times, h, quantile, cost_frac)
                if len(r) < min_rebalances:
                    continue
                s = sharpe(r)
                if s > best_s:
                    best, best_s, best_n = (o, h), s, len(r)
        if best is not None:
            tt = times_by_off[best[0]]
            te_times = tt[(tt >= te0) & (tt < te1)]
            te_r = settlement_ls_returns(close, level, cumf, te_times, best[1],
                                         quantile, cost_frac)
            oos_all.extend(te_r.tolist())
            windows.append(XSWindow((tr0, tr1), (te0, te1),
                                    {"offset": best[0], "hold": best[1]}, best_s, best_n,
                                    float(te_r.mean()) if len(te_r) else 0.0, len(te_r)))
        start += test_len
    return windows, np.asarray(oos_all, dtype=float)
