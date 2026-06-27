import numpy as np
import pandas as pd

from bot.altdata import align, funding_zscore, oi_delta


def test_funding_zscore_flags_extreme():
    idx = pd.date_range("2026-01-01", periods=40, freq="8h", tz="UTC")
    vals = [0.0001] * 39 + [0.01]   # lonjakan ekstrem di akhir
    z = funding_zscore(pd.Series(vals, index=idx), window=30)
    assert z.iloc[-1] > 3            # terdeteksi ekstrem
    assert abs(z.iloc[10]) < 1


def test_align_ffill_to_bars():
    sparse_idx = pd.date_range("2026-01-01", periods=3, freq="8h", tz="UTC")
    s = pd.Series([1.0, 2.0, 3.0], index=sparse_idx)
    bars = pd.date_range("2026-01-01", periods=48, freq="1h", tz="UTC")
    out = align(bars, s, fill=0.0)
    assert len(out) == 48
    assert out[0] == 1.0 and out[-1] == 3.0     # ffill ke depan


def test_align_empty_returns_fill():
    bars = pd.date_range("2026-01-01", periods=10, freq="1h", tz="UTC")
    out = align(bars, pd.Series(dtype=float), fill=0.0)
    assert np.all(out == 0.0)


def test_oi_delta_sign():
    bars = pd.date_range("2026-01-01", periods=20, freq="15min", tz="UTC")
    rising = pd.Series(np.arange(1, 21, dtype=float), index=bars)
    od = oi_delta(bars, rising, lookback=4)
    assert od[-1] > 0                            # OI naik -> delta positif
    assert oi_delta(bars, pd.Series(dtype=float), 4).sum() == 0  # kosong -> 0
