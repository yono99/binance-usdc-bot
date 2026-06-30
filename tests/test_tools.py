"""Tool registry untuk agent otonom — eksekusi & fail-soft (stub exchange)."""
import types

import pandas as pd

from bot.tools import ToolContext, build_tools


def _df(prices):
    return pd.DataFrame({"close": [float(p) for p in prices]})


def test_get_orderbook_spread_and_imbalance():
    ex = types.SimpleNamespace(client=types.SimpleNamespace(
        fetch_order_book=lambda s, limit=10: {"bids": [[100, 5], [99, 3]], "asks": [[101, 2], [102, 1]]}))
    tools = build_tools(ToolContext(ex=ex))
    ob = tools["get_orderbook"]["fn"]({"symbol": "X"})
    assert ob["bid_vol"] == 8.0 and ob["ask_vol"] == 3.0
    assert ob["imbalance"] == round((8 - 3) / 11, 3) and ob["spread_pct"] > 0


def test_get_portfolio():
    ctx = ToolContext(open_positions={"X": {"side": "long", "entry": 100, "bet": 10},
                                      "Y": {"side": "short", "entry": 50, "bet": 5}})
    pf = build_tools(ctx)["get_portfolio"]["fn"]({})
    assert pf["count"] == 2 and pf["exposure_usd"] == 15.0


def test_check_correlation_finds_open_position():
    a = _df(range(100, 140))
    b = _df(range(100, 140))                         # identik → korelasi ~1
    ctx = ToolContext(open_positions={"B": {}}, buffers={"A": a, "B": b})
    c = build_tools(ctx)["check_correlation"]["fn"]({"symbol": "A"})
    assert c["with"] == "B" and c["max_abs_corr"] > 0.9


def test_get_lessons_uses_engine():
    lessons = types.SimpleNamespace(recent=lambda n: [{"id": "l1", "lesson": "x"}])
    out = build_tools(ToolContext(lessons=lessons))["get_lessons"]["fn"]({"limit": 5})
    assert out["lessons"][0]["id"] == "l1"


def test_get_funding_computes_basis():
    ex = types.SimpleNamespace(client=types.SimpleNamespace(
        fetch_funding_rate=lambda s: {"fundingRate": 0.0001, "markPrice": 101.0,
                                      "indexPrice": 100.0, "fundingDatetime": "t"}))
    out = build_tools(ToolContext(ex=ex))["get_funding"]["fn"]({"symbol": "X"})
    assert out["funding_rate"] == 0.0001 and out["basis_pct"] == 1.0      # (101-100)/100*100


def test_get_open_interest():
    ex = types.SimpleNamespace(client=types.SimpleNamespace(
        fetch_open_interest=lambda s: {"openInterestAmount": 1234.0, "openInterestValue": 99999.0}))
    out = build_tools(ToolContext(ex=ex))["get_open_interest"]["fn"]({"symbol": "X"})
    assert out["open_interest"] == 1234.0 and out["value_usd"] == 99999.0


def test_tool_failsoft_returns_error():
    def boom(s, limit=10):
        raise RuntimeError("network down")
    ex = types.SimpleNamespace(client=types.SimpleNamespace(fetch_order_book=boom))
    ob = build_tools(ToolContext(ex=ex))["get_orderbook"]["fn"]({"symbol": "X"})
    assert "error" in ob                             # tak melempar ke loop agen
