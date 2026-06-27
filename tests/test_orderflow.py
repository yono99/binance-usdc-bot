import numpy as np
import pandas as pd

from bot.orderflow import cvd_from_series


def _series(vals):
    idx = pd.date_range("2026-01-01", periods=len(vals), freq="15min", tz="UTC")
    return pd.Series([float(v) for v in vals], index=idx)


def test_full_buying_positive_imbalance():
    n = 30
    close = _series(range(100, 100 + n))
    volume = _series([10.0] * n)
    taker_buy = _series([10.0] * n)        # semua agresif beli
    imb, div = cvd_from_series(close, volume, taker_buy, lookback=8)
    assert imb[-1] > 0.9                    # imbalance ~ +1


def test_full_selling_negative_imbalance():
    n = 30
    close = _series(range(100, 100 + n))
    volume = _series([10.0] * n)
    taker_buy = _series([0.0] * n)         # tak ada beli agresif
    imb, _ = cvd_from_series(close, volume, taker_buy, lookback=8)
    assert imb[-1] < -0.9


def test_divergence_detected():
    n = 30
    close = _series(range(100, 100 + n))            # harga NAIK
    volume = _series([10.0] * n)
    taker_buy = _series([2.0] * n)                  # delta negatif -> CVD TURUN
    _, div = cvd_from_series(close, volume, taker_buy, lookback=8)
    assert bool(div[-1]) is True                    # arah harga != arah CVD
