"""Snapshot reproducibility + lockbox holdout split."""
import numpy as np
import pandas as pd

from bot.dataset import df_hash, load_ohlcv, save_ohlcv, split_holdout


def _df(n=100, seed=0):
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    rng = np.random.default_rng(seed)
    close = 100 + rng.normal(0, 1, n).cumsum()
    return pd.DataFrame({"open": close, "high": close * 1.001, "low": close * 0.999,
                         "close": close, "volume": rng.uniform(1, 5, n)}, index=idx)


def test_snapshot_roundtrip_bitexact(tmp_path):
    df = _df()
    save_ohlcv(df, tmp_path, "BTC/USDC:USDC", "15m")
    loaded = load_ohlcv(tmp_path, "BTC/USDC:USDC", "15m")
    assert loaded is not None
    assert df_hash(df) == df_hash(loaded)            # bit-for-bit
    pd.testing.assert_frame_equal(df, loaded)


def test_load_missing_returns_none(tmp_path):
    assert load_ohlcv(tmp_path, "NOPE/USDC:USDC", "15m") is None


def test_hash_changes_on_edit(tmp_path):
    df = _df()
    df2 = df.copy()
    df2.iloc[50, df2.columns.get_loc("close")] += 1e-6
    assert df_hash(df) != df_hash(df2)               # perubahan kecil pun terdeteksi


def test_split_holdout_reserves_tail():
    df = _df(100)
    research, lockbox = split_holdout(df, 0.2)
    assert len(research) == 80 and len(lockbox) == 20
    assert research.index[-1] < lockbox.index[0]      # lockbox = ekor TERBARU, tak overlap
    # gabungan = utuh, urut
    assert len(research) + len(lockbox) == len(df)


def test_split_holdout_zero_is_noop():
    df = _df(50)
    research, lockbox = split_holdout(df, 0.0)
    assert len(research) == 50 and len(lockbox) == 0
