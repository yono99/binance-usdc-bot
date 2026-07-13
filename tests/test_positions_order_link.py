"""Tahap 3 (plan-sess) — Positions ↔ Open Orders relasi + linkage di /api/open-orders
& /api/positions."""
from __future__ import annotations


def test_normalize_kind_classifies_sl_tp_entry_pending():
    """`_normalize_open_order` memetakan tipe order ke kind (ENTRY_PENDING/SL/TP/UNKNOWN)."""
    from bot.dashboard import _normalize_open_order
    sl = _normalize_open_order({"type": "STOP_MARKET", "symbol": "BTC/USDC:USDC",
                                "reduceOnly": True, "stopPrice": 50000.0,
                                "amount": 0.001, "side": "sell", "status": "open"})
    assert sl["kind"] == "SL"
    tp = _normalize_open_order({"type": "TAKE_PROFIT_MARKET", "symbol": "BTC/USDC:USDC",
                                "reduceOnly": True, "stopPrice": 55000.0,
                                "amount": 0.001, "side": "sell", "status": "open"})
    assert tp["kind"] == "TP"
    entry = _normalize_open_order({"type": "LIMIT", "symbol": "BTC/USDC:USDC",
                                   "reduceOnly": False, "price": 50050.0,
                                   "amount": 0.001, "side": "buy", "status": "open"})
    assert entry["kind"] == "ENTRY_PENDING"
    unk = _normalize_open_order({"type": "TRAILING_STOP_MARKET", "symbol": "BTC/USDC:USDC",
                                  "reduceOnly": True, "amount": 0.001, "side": "sell",
                                  "status": "open"})
    assert unk["kind"] == "UNKNOWN"


def test_link_orders_to_positions_tags_link():
    """_link_orders_to_positions menandai linked_symbol + linked_kind."""
    from bot.dashboard import _link_orders_to_positions
    orders = [
        {"symbol": "BTC/USDC:USDC", "kind": "SL", "side": "SELL"},
        {"symbol": "BTC/USDC:USDC", "kind": "TP", "side": "SELL"},
        {"symbol": "BTC/USDC:USDC", "kind": "ENTRY_PENDING", "side": "BUY"},
    ]
    _link_orders_to_positions(orders)
    for o in orders:
        assert o["linked_symbol"] == "BTC/USDC:USDC"
    assert orders[0]["linked_kind"] == "SL"
    assert orders[1]["linked_kind"] == "TP"
    assert orders[2]["linked_kind"] == "ENTRY_PENDING"
