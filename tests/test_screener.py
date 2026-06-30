"""Layer 2 — screener: filter likuiditas/spread/volatilitas + discover (stub exchange)."""
import types

from bot.screener import discover_usdc_pairs, screen

SCFG = {"screener": {"min_quote_volume_24h": 1_000_000, "max_spread_pct": 0.1,
                     "min_atr_pct": 0.0, "max_atr_pct": 100.0}}


def _stub_ex(make_df):
    df = make_df(list(range(100, 160)))      # 60 bar → cukup utk atr & len≥30
    return types.SimpleNamespace(
        ticker=lambda s: {"quoteVolume": 100 if s == "LOWVOL" else 5_000_000},
        spread_pct=lambda s: 0.5 if s == "WIDESPREAD" else 0.02,
        ohlcv=lambda s, tf, limit=60: df,
    )


def test_screen_keeps_liquid_low_spread(make_df):
    ex = _stub_ex(make_df)
    passed = screen(ex, ["GOOD", "LOWVOL", "WIDESPREAD"], SCFG, "15m")
    assert "GOOD" in passed
    assert "LOWVOL" not in passed            # volume kecil → dibuang
    assert "WIDESPREAD" not in passed        # spread lebar → dibuang


def test_screen_atr_band_filters(make_df):
    ex = _stub_ex(make_df)
    cfg = {"screener": {**SCFG["screener"], "min_atr_pct": 50.0}}   # ambang mustahil
    assert screen(ex, ["GOOD"], cfg, "15m") == []                  # atr di luar band → buang


def test_discover_only_usdc_swap_active():
    ex = types.SimpleNamespace(markets={
        "BTC/USDC:USDC": {"symbol": "BTC/USDC:USDC", "swap": True, "quote": "USDC", "active": True},
        "ETH/USDT:USDT": {"symbol": "ETH/USDT:USDT", "swap": True, "quote": "USDT", "active": True},
        "OLD/USDC:USDC": {"symbol": "OLD/USDC:USDC", "swap": True, "quote": "USDC", "active": False},
        "SPOT/USDC": {"symbol": "SPOT/USDC", "swap": False, "quote": "USDC", "active": True},
    })
    out = discover_usdc_pairs(ex, limit=10)
    assert out == ["BTC/USDC:USDC"]          # hanya USDC + swap + active


def test_discover_respects_limit():
    mk = {f"P{i}/USDC:USDC": {"symbol": f"P{i}/USDC:USDC", "swap": True, "quote": "USDC", "active": True}
          for i in range(5)}
    ex = types.SimpleNamespace(markets=mk)
    assert len(discover_usdc_pairs(ex, limit=3)) == 3
