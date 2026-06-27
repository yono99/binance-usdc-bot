from bot.signals import evaluate
from svc.strategy import core_to_ccxt


def test_strong_uptrend_not_short(cfg, make_df):
    df = make_df([100 + i * 0.8 for i in range(120)])
    sig = evaluate("BTCUSDC", df, cfg)
    assert sig.side != "short"
    assert 0.0 <= sig.confidence <= 1.0


def test_flat_market_skips(cfg, make_df):
    df = make_df([100 + (0.1 if i % 2 else -0.1) for i in range(120)])
    sig = evaluate("BTCUSDC", df, cfg)
    assert sig.side == "skip"


def test_symbol_mapping():
    assert core_to_ccxt("BTCUSDC") == "BTC/USDC:USDC"
    assert core_to_ccxt("solusdc") == "SOL/USDC:USDC"
