"""Pertahanan #1: deteksi LEAKAGE / lookahead secara otomatis.

Prinsip uji: sebuah transform/sinyal yang CAUSAL hanya boleh memakai data ≤ bar
sekarang. Maka bila kita **meracuni bar masa depan** (mengganti nilainya dengan
angka gila), seluruh output di bar-bar MASA LALU **wajib tidak berubah**. Bila
berubah → ada kebocoran (mis. rolling ter-center, reindex bfill, normalisasi global,
atau salah shift) → bug paling berbahaya, ketahuan sebelum menyentuh uang.

Berlaku untuk semua sumber sinyal riset (v1 base, v5 basis, v6 cascade, v7 funding)
plus transform alt-data.
"""
import numpy as np
import pandas as pd

from bot.altdata import basis_zscore, cascade_components, funding_zscore, oi_delta
from bot.optimize import precompute
from bot.strategy_lab import decide_v6, precompute_v6


def _walk(n: int, seed: int = 7) -> pd.DataFrame:
    """OHLCV deterministik (random walk) dengan volume bervariasi."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 1, n).cumsum()
    close = 100 + steps
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    close = pd.Series(close, index=idx)
    open_ = close.shift(1).fillna(close)
    high = pd.concat([open_, close], axis=1).max(axis=1) * 1.003
    low = pd.concat([open_, close], axis=1).min(axis=1) * 0.997
    vol = pd.Series(rng.uniform(1, 10, n), index=idx)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


def _poison(df: pd.DataFrame, cut: int) -> pd.DataFrame:
    """Ganti bar [cut:] dengan nilai gila — masa depan 'beracun'."""
    p = df.copy()
    p.iloc[cut:] = p.iloc[cut:] * 1e6 + 12345.0
    return p


def _assert_past_unchanged(fn, df: pd.DataFrame, cut: int, name: str):
    full = np.asarray(fn(df), dtype=float)
    poisoned = np.asarray(fn(_poison(df, cut)), dtype=float)
    np.testing.assert_allclose(
        full[:cut], poisoned[:cut], rtol=1e-9, atol=1e-9,
        err_msg=f"LEAKAGE terdeteksi di {name}: output masa lalu berubah saat masa depan diracuni",
    )


def test_precompute_scores_causal(cfg):
    df = _walk(320)
    cut = 250
    _assert_past_unchanged(lambda d: precompute(d, cfg).long_score, df, cut, "precompute.long_score")
    _assert_past_unchanged(lambda d: precompute(d, cfg).short_score, df, cut, "precompute.short_score")
    _assert_past_unchanged(lambda d: precompute(d, cfg).atr, df, cut, "precompute.atr")


def test_cascade_components_causal(cfg):
    df = _walk(320)
    cut = 250
    atr_arr = precompute(df, cfg).atr  # atr penuh (causal sendiri, diuji di atas)
    for i, nm in enumerate(("range_atr", "vol_ratio", "close_loc")):
        _assert_past_unchanged(
            lambda d, i=i: cascade_components(d, precompute(d, cfg).atr, 20)[i], df, cut, f"cascade.{nm}")


def test_decide_v6_side_causal(cfg):
    df = _walk(320)
    cut = 250
    _assert_past_unchanged(
        lambda d: decide_v6(precompute_v6(d, cfg), {"cascade_k": 2.0}, cfg), df, cut, "decide_v6.side")


def test_funding_zscore_causal():
    idx = pd.date_range("2026-01-01", periods=120, freq="8h", tz="UTC")
    rng = np.random.default_rng(3)
    s = pd.Series(rng.normal(0.0001, 0.0005, 120), index=idx)
    cut = 90
    full = funding_zscore(s, window=30).to_numpy()
    pois = s.copy()
    pois.iloc[cut:] = pois.iloc[cut:] * 1e6
    poisoned = funding_zscore(pois, window=30).to_numpy()
    np.testing.assert_allclose(full[:cut], poisoned[:cut], rtol=1e-9, atol=1e-9,
                               err_msg="LEAKAGE di funding_zscore")


def test_basis_zscore_causal():
    idx = pd.date_range("2026-01-01", periods=300, freq="15min", tz="UTC")
    rng = np.random.default_rng(5)
    a = pd.Series(100 + rng.normal(0, 1, 300).cumsum(), index=idx)
    b = a * (1 + rng.normal(0, 0.0002, 300))   # bybit ~ binance + noise
    cut = 240
    full = basis_zscore(a, b, window=48)
    a2, b2 = a.copy(), b.copy()
    a2.iloc[cut:] *= 1e6
    b2.iloc[cut:] *= 1e6
    poisoned = basis_zscore(a2, b2, window=48)
    np.testing.assert_allclose(full[:cut], poisoned[:cut], rtol=1e-9, atol=1e-9,
                               err_msg="LEAKAGE di basis_zscore")


def test_oi_delta_causal():
    idx = pd.date_range("2026-01-01", periods=200, freq="15min", tz="UTC")
    rng = np.random.default_rng(9)
    s = pd.Series(np.abs(rng.normal(1000, 50, 200)).cumsum(), index=idx)
    cut = 150
    full = oi_delta(idx, s, lookback=4)
    s2 = s.copy()
    s2.iloc[cut:] *= 1e6
    poisoned = oi_delta(idx, s2, lookback=4)
    np.testing.assert_allclose(full[:cut], poisoned[:cut], rtol=1e-9, atol=1e-9,
                               err_msg="LEAKAGE di oi_delta")
