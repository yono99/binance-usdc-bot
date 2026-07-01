"""Fase 4 — H24 settlement seasonality: mekanik waktu pra-settlement, tanda
income funding, dan kontrol positif/negatif walk-forward."""
import numpy as np
import pandas as pd

from bot import settlement as st
from bot import xsectional as xs


def _hourly_index(T, start="2024-01-01"):
    return pd.date_range(start, periods=T, freq="h", tz="UTC")


def test_presettle_times_hours():
    idx = _hourly_index(48)
    t0 = st.presettle_times(idx, 0)
    t1 = st.presettle_times(idx, 1)
    assert all(idx[t].hour in (23, 7, 15) for t in t0)     # close pas settlement
    assert all(idx[t].hour in (22, 6, 14) for t in t1)     # close 1 jam sebelum
    assert len(t0) == 6 and len(t1) == 6                    # 3 settlement/hari × 2 hari


def _panels(T, mus_level, price_beta, noise, seed=0):
    """N simbol, funding level konstan per simbol. Return bar yang MENYEBERANGI
    settlement = price_beta × (−level) + noise (funding tinggi → jatuh). cumf
    dibebankan pas settlement (open-hour 0/8/16)."""
    rng = np.random.default_rng(seed)
    idx = _hourly_index(T)
    N = len(mus_level)
    level = np.tile(np.asarray(mus_level), (T, 1))
    r = rng.normal(0, noise, (T, N))
    cross = np.asarray(idx.hour) % 8 == 0                   # bar dgn open pas settlement
    r[cross] += price_beta * (-level[cross])
    close = 100 * np.exp(np.cumsum(r, axis=0))
    charge = np.zeros((T, N))
    charge[cross] = level[cross]
    cumf = np.cumsum(charge, axis=0)
    return close, level, cumf, idx


def test_funding_income_sign():
    """Tanpa sinyal harga sama sekali: short funding-positif MENERIMA funding →
    PnL long-short = income murni, harus positif."""
    close, level, cumf, idx = _panels(600, np.linspace(-0.001, 0.001, 8),
                                      price_beta=0.0, noise=0.0)
    times = st.presettle_times(idx, 0)
    r = st.settlement_ls_returns(close, level, cumf, times, hold=1, quantile=0.3,
                                 cost_frac=0.0)
    assert len(r) > 0 and r.mean() > 0


def test_walk_forward_positive_control():
    close, level, cumf, idx = _panels(6000, np.linspace(-0.002, 0.002, 10),
                                      price_beta=2.0, noise=0.004)
    windows, oos = st.walk_forward_settlement(close, level, cumf, idx, offsets=[0, 1],
                                              holds=[1, 4], quantile=0.3, cost_frac=0.0,
                                              train_len=2000, test_len=800)
    v = xs.verdict(oos, 4)
    assert v["mean"] > 0 and v["ok"], v["reason"]


def test_walk_forward_negative_control():
    """Level funding acak per-bar TANPA hubungan dgn harga & tanpa income
    (cumf nol) → tak boleh lolos."""
    rng = np.random.default_rng(7)
    T, N = 6000, 10
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, (T, N)), axis=0))
    level = rng.normal(0, 0.001, (T, N))
    cumf = np.zeros((T, N))
    idx = _hourly_index(T)
    _, oos = st.walk_forward_settlement(close, level, cumf, idx, offsets=[0, 1],
                                        holds=[1, 4], quantile=0.3, cost_frac=0.0,
                                        train_len=2000, test_len=800)
    assert not xs.verdict(oos, 4)["ok"]
