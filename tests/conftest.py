import pandas as pd
import pytest

from bot import decision_log, logger
from bot.config import load_settings


@pytest.fixture(scope="session")
def cfg():
    return load_settings().raw


@pytest.fixture(autouse=True)
def _reset_mode_globals():
    """ForwardTester men-set journal/decision-log mode secara global saat init
    (isolasi riwayat dry/test/live). Tanpa reset ini, satu test yang membuat
    ForwardTester membocorkan mode ke test lain yang tak terkait (mis.
    test_logger menulis ke trades_dry.jsonl, bukan trades.jsonl)."""
    yield
    logger.set_journal_mode(None)
    decision_log.set_mode(None)


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
