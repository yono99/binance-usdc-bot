"""Chart store SQLite: roundtrip, idempotensi, inkremental."""
import pandas as pd

from bot import chartstore as cs


def _df(start, n, base=100.0):
    idx = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"open": base, "high": base + 1, "low": base - 1,
                         "close": base + 0.5, "volume": 10.0}, index=idx)


def test_upsert_load_roundtrip_and_idempotent(tmp_path):
    db = tmp_path / "m.db"
    df = _df("2026-01-01", 10)
    assert cs.upsert("BTC/USDC:USDC", "15m", df, db=db) == 10
    assert cs.upsert("BTC/USDC:USDC", "15m", df, db=db) == 10   # replace, bukan duplikat
    out = cs.load("BTC/USDC:USDC", "15m", db=db)
    assert len(out) == 10 and out.index[0] == df.index[0]
    assert cs.load("ETH/USDC:USDC", "15m", db=db).empty


def test_load_limit_returns_latest_chronological(tmp_path):
    db = tmp_path / "m.db"
    cs.upsert("X", "1h", _df("2026-01-01", 50), db=db)
    out = cs.load("X", "1h", limit=5, db=db)
    assert len(out) == 5 and out.index.is_monotonic_increasing
    assert out.index[-1] == _df("2026-01-01", 50).index[-1]     # ekor terbaru


def test_ingest_incremental_from_last_ts(tmp_path):
    db = tmp_path / "m.db"
    cs.upsert("X", "15m", _df("2026-01-01", 4), db=db)
    last = cs.last_ts("X", "15m", db=db)

    class _Client:
        def parse_timeframe(self, tf):
            return 900

        def fetch_ohlcv(self, symbol, tf, since=None, limit=1000):
            assert since == last + 1                             # lanjut dari ts terakhir
            t0 = last + 900_000
            return [[t0 + i * 900_000, 1, 2, 0.5, 1.5, 9] for i in range(3)]

    class _Ex:
        client = _Client()

    assert cs.ingest(_Ex(), "X", "15m", db=db) == 3
    assert len(cs.load("X", "15m", db=db)) == 7
    cov = cs.coverage(db=db)
    assert cov[0]["n"] == 7 and cov[0]["symbol"] == "X"
