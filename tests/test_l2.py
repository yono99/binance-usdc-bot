from bot.l2 import imbalance, micro_price, snapshot_record, spread_bps

# bids turun, asks naik (format ccxt)
BIDS = [[100.0, 3.0], [99.9, 2.0], [99.8, 1.0]]
ASKS = [[100.1, 1.0], [100.2, 2.0], [100.3, 1.0]]


def test_imbalance_buy_pressure():
    # bid qty > ask qty -> imbalance > 0.5
    assert imbalance(BIDS, ASKS, 3) > 0.5
    assert abs(imbalance([[1, 5]], [[2, 5]], 1) - 0.5) < 1e-9


def test_micro_price_between_best():
    mp = micro_price(BIDS, ASKS)
    assert BIDS[0][0] <= mp <= ASKS[0][0]
    # ask lebih tipis (1.0) vs bid (3.0) -> micro condong ke ask
    assert mp > (BIDS[0][0] + ASKS[0][0]) / 2


def test_spread_bps():
    s = spread_bps(BIDS, ASKS)
    assert abs(s - (0.1 / 100.05 * 1e4)) < 1e-6


def test_snapshot_record_shape():
    rec = snapshot_record("BTC/USDC:USDC", BIDS, ASKS, 1700000000000)
    assert rec["symbol"] == "BTC/USDC:USDC"
    assert rec["ts"] == 1700000000000
    assert abs(rec["mid"] - 100.05) < 1e-9
    assert 0 <= rec["imb5"] <= 1
    assert len(rec["bids"]) == 3 and len(rec["asks"]) == 3
