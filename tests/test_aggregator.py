from svc.aggregator import TFAggregator, tf_to_ms


def test_tf_to_ms():
    assert tf_to_ms("15m") == 900_000
    assert tf_to_ms("1h") == 3_600_000
    assert tf_to_ms("1d") == 86_400_000


def test_aggregate_15m_bucket():
    agg = TFAggregator(tf_to_ms("15m"))
    closed = None
    for i in range(16):  # 0..15 menit; menit 15 menutup bucket pertama
        bar = {
            "symbol": "BTCUSDC",
            "open_time": i * 60_000,
            "open": 100 + i,
            "high": 110 + i,
            "low": 90 + i,
            "close": 100 + i,
            "volume": 1.0,
        }
        r = agg.update(bar)
        if r:
            closed = r
    assert closed is not None
    assert closed["open_time"] == 0
    assert closed["open"] == 100        # open bar pertama
    assert closed["high"] == 124        # max high atas bar 0..14 (110+14)
    assert closed["close"] == 114       # close bar terakhir (100+14)
    assert closed["volume"] == 15       # 15 bar 1m


def test_no_close_within_same_bucket():
    agg = TFAggregator(tf_to_ms("15m"))
    bar = {"symbol": "X", "open_time": 0, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1.0}
    assert agg.update(bar) is None
    bar2 = {**bar, "open_time": 300_000}  # masih bucket 0
    assert agg.update(bar2) is None
