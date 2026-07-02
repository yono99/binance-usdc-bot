"""Universe USDC+USDT: filter settle, prefilter batch-volume, dedup anti-dobel."""
from bot import screener
from bot.exchange import Exchange


def test_perp_symbols_filters_by_settle():
    ex = object.__new__(Exchange)                  # tanpa __init__ (tanpa jaringan)
    ex.markets = {
        "BTC/USDC:USDC": {"swap": True, "settle": "USDC", "active": True},
        "BTC/USDT:USDT": {"swap": True, "settle": "USDT", "active": True,
                          "info": {"underlyingType": "COIN"}},
        "DOGE/USDT:USDT": {"swap": True, "settle": "USDT", "active": False},  # delisted
        "ETH/BUSD:BUSD": {"swap": True, "settle": "BUSD", "active": True},
        "BTC/USDT": {"swap": False, "settle": "USDT", "active": True},        # spot
        "MSTR/USDT:USDT": {"swap": True, "settle": "USDT", "active": True,
                           "info": {"underlyingType": "EQUITY"}},             # saham
        "XAU/USDT:USDT": {"swap": True, "settle": "USDT", "active": True,
                          "info": {"underlyingType": "COMMODITY"}},           # emas
    }
    assert ex.perp_symbols(("USDC",)) == ["BTC/USDC:USDC"]
    assert ex.perp_symbols(("USDC", "USDT")) == ["BTC/USDC:USDC", "BTC/USDT:USDT"]


class _Client:
    def fetch_tickers(self, symbols):
        return {s: {"quoteVolume": 1_000_000 * (i + 1)}
                for i, s in enumerate(symbols)}


class _Ex:
    client = _Client()


def test_prefilter_volume_top_n_above_threshold():
    syms = [f"S{i}/USDT:USDT" for i in range(10)]
    out = screener.prefilter_volume(_Ex(), syms, min_qv=3_500_000, top_n=3)
    assert out == ["S9/USDT:USDT", "S8/USDT:USDT", "S7/USDT:USDT"]  # top by volume


def test_prefilter_fail_open_on_batch_error():
    class _Boom:
        class client:
            @staticmethod
            def fetch_tickers(symbols):
                raise RuntimeError("api down")
    syms = [f"S{i}" for i in range(100)]
    assert screener.prefilter_volume(_Boom(), syms, 1, top_n=5) == syms[:5]


def test_dedup_prefers_usdc_twin():
    out = screener.dedup_prefer_usdc(
        ["BTC/USDT:USDT", "BTC/USDC:USDC", "FET/USDT:USDT", "SOL/USDC:USDC"])
    assert out == ["BTC/USDC:USDC", "FET/USDT:USDT", "SOL/USDC:USDC"]
    # urutan input tak berpengaruh
    out2 = screener.dedup_prefer_usdc(["BTC/USDC:USDC", "BTC/USDT:USDT"])
    assert out2 == ["BTC/USDC:USDC"]
