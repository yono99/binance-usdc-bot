"""Item 4 — Open Orders live dari Binance + perbaikan bug limit-order resting.
Tes:
1. _normalize_open_order: standarisasi field order Binance.
2. /api/open-orders: dry → baca pending_orders dari status kv.
3. /api/cancel-order: tolak di dry; live → cancel & invalidate cache.
4. _live_open: LIMIT resting → kembalikan pending; market → fill + SL/TP.
5. _live_reconcile: pending filled → self.open + SL/TP; canceled → dibuang; timeout → cancel.
"""
import json
import types

import pandas as pd

from bot import dashboard
from bot import config as bot_config


def _body(resp):
    return json.loads(resp.body)


# ---------- 1. _normalize_open_order ----------
def test_normalize_limit_resting_order():
    o = {"symbol": "BTC/USDC:USDC", "id": "abc123", "type": "limit", "side": "buy",
         "price": "50000.0", "amount": "0.001", "filled": "0.0", "status": "open",
         "info": {"reduceOnly": False}}
    n = dashboard._normalize_open_order(o)
    assert n["symbol"] == "BTC/USDC:USDC"
    assert n["order_id"] == "abc123"
    assert n["type"] == "LIMIT"
    assert n["side"] == "BUY"
    assert n["price"] == 50000.0
    assert n["qty"] == 0.001
    assert n["filled"] == 0.0
    assert n["status"] == "open"
    assert n["reduce_only"] is False


def test_normalize_stop_market_reduce_only():
    o = {"symbol": "ETH/USDC:USDC", "id": "x", "type": "stop_market", "side": "sell",
         "stopPrice": "3000.0", "amount": "0.5", "filled": "0.0", "status": "new",
         "reduceOnly": True}
    n = dashboard._normalize_open_order(o)
    assert n["type"] == "STOP_MARKET"
    assert n["price"] == 3000.0
    assert n["reduce_only"] is True


def test_normalize_malformed_does_not_raise():
    n = dashboard._normalize_open_order({"symbol": "FOO", "id": "1"})
    assert n["symbol"] == "FOO"
    assert n["price"] == 0.0
    assert n["reduce_only"] is False


# ---------- 2. /api/open-orders (dry path via status kv) ----------
def test_api_open_orders_dry_reads_pending_from_status(monkeypatch):
    """Dry: tak ada exchange → baca pending_orders dari status kv."""
    dashboard._open_orders_cache["ts"] = 0.0
    # Patch load_settings di modul asal (karena endpoint import lokal: from .config import ...)
    _fake_settings = types.SimpleNamespace(is_live=False)
    monkeypatch.setattr(bot_config, "load_settings", lambda: _fake_settings)
    monkeypatch.setattr(dashboard, "get_active_mode", lambda *a, **k: "dry")
    monkeypatch.setattr(dashboard.store, "get_kv", lambda key: {
        "pending_orders": [{"symbol": "BTC/USDC:USDC", "side": "buy", "type": "LIMIT",
                             "price": 50000.0, "qty": 0.001, "order_id": "abc",
                             "opened_ts": "2026-07-09T00:00:00+00:00"}]
    } if key == "status:dry" else None)
    b = _body(dashboard.api_open_orders())
    assert b["paper"] is True
    assert len(b["orders"]) == 1
    assert b["orders"][0]["symbol"] == "BTC/USDC:USDC"


def test_api_open_orders_dry_empty_when_no_pending(monkeypatch):
    dashboard._open_orders_cache["ts"] = 0.0
    monkeypatch.setattr(bot_config, "load_settings",
                        lambda: types.SimpleNamespace(is_live=False))
    monkeypatch.setattr(dashboard.store, "get_kv", lambda key: {})
    b = _body(dashboard.api_open_orders())
    assert b["orders"] == []


# ---------- 3. /api/cancel-order ----------
def test_cancel_order_rejected_in_dry(monkeypatch):
    monkeypatch.setattr(bot_config, "load_settings",
                        lambda: types.SimpleNamespace(is_live=False))
    b = _body(dashboard.api_cancel_order({"symbol": "BTC/USDC:USDC", "order_id": "abc"}))
    assert b["ok"] is False
    assert "live" in b["error"]


def test_cancel_order_missing_fields():
    b = _body(dashboard.api_cancel_order({}))
    assert b["ok"] is False
    assert "wajib" in b["error"]


def test_cancel_order_live_calls_cancel_and_invalidates_cache(monkeypatch):
    monkeypatch.setattr(bot_config, "load_settings",
                        lambda: types.SimpleNamespace(is_live=True))
    monkeypatch.setenv("BINANCE_LIVE_KEY", "fake_key")
    called = {}
    fake_client = types.SimpleNamespace(
        cancel_order=lambda oid, sym: called.update(oid=oid, sym=sym))
    monkeypatch.setattr(dashboard, "_get_ex",
                        lambda: types.SimpleNamespace(client=fake_client))
    dashboard._open_orders_cache["ts"] = 999.0
    b = _body(dashboard.api_cancel_order({"symbol": "BTC/USDC:USDC", "order_id": "abc"}))
    assert b["ok"] is True
    assert called == {"oid": "abc", "sym": "BTC/USDC:USDC"}
    assert dashboard._open_orders_cache["ts"] == 0.0


# ---------- 4. _live_open ----------
def _make_ft_for_live():
    """ForwardTester minimal untuk tes jalur live (tanpa __init__ penuh)."""
    from bot.forward import ForwardTester
    ft = ForwardTester.__new__(ForwardTester)
    ft.live = True
    ft.cfg = {}
    ft._pending_timeout_s = 300
    ft.ex = types.SimpleNamespace(set_leverage=lambda *a, **k: None)
    ft.notify = types.SimpleNamespace(send=lambda m: None)
    ft.vrp = types.SimpleNamespace(stamp=lambda: {})
    ft._regime_stamp = lambda buf, cfg: {}
    return ft


def test_live_open_limit_resting_returns_pending_not_fill():
    """LIMIT + GTX status 'open' → return pending, BUKAN fill. SL/TP TIDAK dipasang."""
    ft = _make_ft_for_live()
    calls = []
    ft.ex.client = types.SimpleNamespace(
        create_order=lambda sym, otype, side, qty, price=None, params=None:
            calls.append(("create", sym, otype, side)) or
            {"status": "open", "id": "ord-1", "average": None}
    )
    from bot.settings_store import RuntimeSettings
    rs = RuntimeSettings(mode="live")
    ok, fill, pending = ft._live_open("BTC/USDC:USDC", True, 0.001, 50000.0,
                                      49500.0, 51000.0, rs)
    assert ok is True
    assert fill is None
    assert pending is not None
    assert pending["order_id"] == "ord-1"
    assert pending["entry"] == 50000.0
    assert len(calls) == 1
    assert calls[0][2] == "limit"


def test_live_open_market_returns_fill_and_places_sl_tp():
    """Market order langsung terisi → fill + SL/TP (3 create_order)."""
    ft = _make_ft_for_live()
    calls = []
    ft.ex.client = types.SimpleNamespace(
        create_order=lambda sym, otype, side, qty, price=None, params=None:
            (calls.append(("create", sym, otype, side)),
             {"status": "closed", "id": "m-1", "average": 50100.0})[1]
    )
    from bot.settings_store import RuntimeSettings
    rs = RuntimeSettings(mode="live")
    ok, fill, pending = ft._live_open("BTC/USDC:USDC", True, 0.001, 50000.0,
                                      49500.0, 51000.0, rs)
    assert ok is True
    assert fill == 50100.0
    assert pending is None
    assert len(calls) == 3


def test_live_open_limit_already_filled_places_sl_tp():
    """Limit langsung terisi (status filled + average) → jalur filled normal."""
    ft = _make_ft_for_live()
    calls = []
    ft.ex.client = types.SimpleNamespace(
        create_order=lambda sym, otype, side, qty, price=None, params=None:
            (calls.append(("create", sym, otype, side)),
             {"status": "filled", "id": "f-1", "average": 49999.0})[1]
    )
    from bot.settings_store import RuntimeSettings
    rs = RuntimeSettings(mode="live")
    ok, fill, pending = ft._live_open("BTC/USDC:USDC", True, 0.001, 50000.0,
                                      49500.0, 51000.0, rs)
    assert ok is True and fill == 49999.0 and pending is None
    assert len(calls) == 3


def test_live_open_entry_failure_returns_not_ok():
    ft = _make_ft_for_live()
    ft.ex.client = types.SimpleNamespace(
        create_order=lambda *a, **k: (_ for _ in ()).throw(Exception("api down")))
    from bot.settings_store import RuntimeSettings
    rs = RuntimeSettings(mode="live")
    ok, fill, pending = ft._live_open("BTC/USDC:USDC", True, 0.001, 50000.0,
                                      49500.0, 51000.0, rs)
    assert ok is False


# ---------- 5. _live_reconcile ----------
def _make_ft_reconcile(pending, has_position=False, order_status=None):
    """ForwardTester minimal untuk tes _live_reconcile."""
    from bot.forward import ForwardTester
    ft = ForwardTester.__new__(ForwardTester)
    ft.live = True
    ft.cfg = {}
    ft.open = {}
    ft.pending = pending
    ft._pending_timeout_s = 300
    ft.balance_usd = 1000.0
    ft._day_start_balance = 1000.0
    ft._day_pnl = 0.0
    ft._day_trades = 0
    ft._gem_closes = 0
    ft.settings = types.SimpleNamespace(mode="live")
    ft.gtrader = None
    ft.vrp = types.SimpleNamespace(stamp=lambda: {})
    ft._regime_stamp = lambda buf, cfg: {}
    ft.notify = types.SimpleNamespace(send=lambda m: None)

    positions = [{"symbol": "BTC/USDC:USDC", "contracts": 0.001,
                 "entryPrice": 50050.0}] if has_position else []
    if order_status is not None:
        oo = [{"id": list(pending.values())[0]["order_id"], "status": order_status}]
    elif has_position:
        oo = []  # filled → order gone
    else:
        oo = []  # canceled
    ft.ex = types.SimpleNamespace(
        positions=lambda: positions,
        open_orders=lambda: oo,
        equity_usdc=lambda fb: 1000.0,
        client=types.SimpleNamespace(cancel_all_orders=lambda s: None,
                                     cancel_order=lambda oid, sym: None))
    return ft


def test_reconcile_pending_filled_moves_to_open():
    """Pending terisi → SL/TP dipasang, pindah ke self.open."""
    pending = {"BTC/USDC:USDC": {
        "order_id": "ord-1", "qty": 0.001, "entry": 50000.0, "sl": 49500.0,
        "tp": 51000.0, "is_long": True, "bet": 10.0, "liq": 40000.0, "risk0": 0.5,
        "placed_ts": "2026-07-09T00:00:00+00:00", "entry_fee_rate": 0.0}}
    ft = _make_ft_reconcile(pending, has_position=True)
    calls = []
    ft.ex.client = types.SimpleNamespace(
        create_order=lambda sym, otype, side, qty, price=None, params=None:
            calls.append(("create", otype)),
        cancel_all_orders=lambda s: None,
        cancel_order=lambda oid, sym: None)
    ft._live_reconcile()
    assert len(calls) == 2
    assert {c[1] for c in calls} == {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
    assert "BTC/USDC:USDC" in ft.open
    assert "BTC/USDC:USDC" not in ft.pending
    assert ft.open["BTC/USDC:USDC"]["entry"] == 50050.0


def test_reconcile_pending_canceled_drops_from_pending():
    """Pending hilang dari order book & tak ada posisi → dibuang."""
    pending = {"SOL/USDC:USDC": {
        "order_id": "ord-2", "qty": 0.1, "entry": 100.0, "sl": 95.0, "tp": 110.0,
        "is_long": True, "bet": 5.0, "liq": 80.0, "risk0": 0.5,
        "placed_ts": "2026-07-09T00:00:00+00:00", "entry_fee_rate": 0.0}}
    ft = _make_ft_reconcile(pending, has_position=False)
    ft._live_reconcile()
    assert "SOL/USDC:USDC" not in ft.pending
    assert ft.open == {}


def test_reconcile_pending_still_resting_stays():
    """Pending masih resting → tetap ditelusuri."""
    pending = {"ETH/USDC:USDC": {
        "order_id": "ord-3", "qty": 0.2, "entry": 3000.0, "sl": 2950.0, "tp": 3100.0,
        "is_long": True, "bet": 10.0, "liq": 2500.0, "risk0": 10.0,
        "placed_ts": pd.Timestamp.utcnow().isoformat(), "entry_fee_rate": 0.0}}
    ft = _make_ft_reconcile(pending, has_position=False, order_status="new")
    ft._live_reconcile()
    assert "ETH/USDC:USDC" in ft.pending
    assert ft.open == {}


def test_reconcile_pending_timeout_cancels():
    """Pending melebihi timeout → cancel order & dibuang."""
    pending = {"DOGE/USDC:USDC": {
        "order_id": "ord-4", "qty": 100.0, "entry": 0.1, "sl": 0.09, "tp": 0.11,
        "is_long": True, "bet": 5.0, "liq": 0.08, "risk0": 1.0,
        "placed_ts": "2020-01-01T00:00:00+00:00", "entry_fee_rate": 0.0}}
    ft = _make_ft_reconcile(pending, has_position=False, order_status="new")
    ft._pending_timeout_s = 1
    cancels = []
    ft.ex.client.cancel_order = lambda oid, sym: cancels.append((oid, sym))
    ft._live_reconcile()
    assert cancels == [("ord-4", "DOGE/USDC:USDC")]
    assert "DOGE/USDC:USDC" not in ft.pending
