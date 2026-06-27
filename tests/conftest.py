import pandas as pd
import pytest

from bot.config import load_settings


@pytest.fixture(scope="session")
def cfg():
    return load_settings().raw


@pytest.fixture
def make_df():
    """Bangun DataFrame OHLCV dari list harga close."""
    def _make(prices, vol=1.0):
        idx = pd.date_range("2026-01-01", periods=len(prices), freq="15min", tz="UTC")
        close = pd.Series([float(p) for p in prices], index=idx)
        open_ = close.shift(1).fillna(close)
        high = pd.concat([open_, close], axis=1).max(axis=1) * 1.002
        low = pd.concat([open_, close], axis=1).min(axis=1) * 0.998
        return pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
            index=idx,
        )

    return _make
