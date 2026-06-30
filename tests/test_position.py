"""Layer 7 — PositionManager: deteksi exit SL/TP & trailing stop (stub exchange)."""
import types

import pytest

from bot.position import Position, PositionManager

CFG = {"risk": {"trailing": False, "trailing_atr_mult": 1.0, "sl_atr_mult": 2.0}}


def _pm(cfg=CFG):
    ex = types.SimpleNamespace(settings=types.SimpleNamespace(is_dry=True))
    ex_exec = types.SimpleNamespace(close_position=lambda *a, **k: None)
    return PositionManager(ex, ex_exec, cfg)


def _const_price(p):
    return lambda sym: p


def test_long_take_profit():
    pm = _pm()
    pm.add(Position("X", "long", 1.0, 100.0, 97.0, 103.0, peak=100.0))
    closed = pm.monitor(_const_price(104.0))
    assert closed == [("X", pytest.approx(4.0), False)]   # was_sl False
    assert "X" not in pm.open


def test_long_stop_loss():
    pm = _pm()
    pm.add(Position("X", "long", 1.0, 100.0, 97.0, 103.0, peak=100.0))
    closed = pm.monitor(_const_price(96.0))
    assert closed[0][2] is True and closed[0][1] == pytest.approx(-4.0)


def test_short_take_profit():
    pm = _pm()
    pm.add(Position("S", "short", 2.0, 100.0, 103.0, 97.0, peak=100.0))
    closed = pm.monitor(_const_price(96.0))                # price <= tp 97 → TP
    assert closed[0][2] is False and closed[0][1] == pytest.approx(8.0)   # (100-96)*2


def test_no_exit_keeps_position_open():
    pm = _pm()
    pm.add(Position("X", "long", 1.0, 100.0, 97.0, 103.0, peak=100.0))
    assert pm.monitor(_const_price(100.5)) == []
    assert "X" in pm.open


def test_trailing_moves_stop_up_for_long():
    pm = _pm({"risk": {"trailing": True, "trailing_atr_mult": 1.0, "sl_atr_mult": 2.0}})
    pm.add(Position("X", "long", 1.0, 100.0, 96.0, 200.0, peak=100.0))
    closed = pm.monitor(_const_price(110.0))               # naik → trailing kunci stop
    assert closed == []                                   # belum kena
    # dist = |100-96| * (1.0/2.0) = 2 → new_sl = 110-2 = 108 (> 96)
    assert pm.open["X"].sl == pytest.approx(108.0)
    assert pm.open["X"].peak == pytest.approx(110.0)


def test_notional_and_symbols():
    pm = _pm()
    pm.add(Position("A", "long", 2.0, 50.0, 48.0, 55.0, peak=50.0))
    assert pm.notional == pytest.approx(100.0)
    assert pm.symbols == {"A"}
