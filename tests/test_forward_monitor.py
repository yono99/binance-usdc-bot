"""Fix intrabar: _monitor_usd menutup SL/TP saat WICK candle menyentuh level,
walau 'last' sesaat belum — agar tak terlewat antar-poll (mode paper/dry)."""
import types

import pandas as pd

from bot.forward import ForwardTester


def _ft(last, pos):
    ft = ForwardTester.__new__(ForwardTester)
    ft.live = False
    ft.buffers = {}
    ft.open = {"X": pos}
    ft.ex = types.SimpleNamespace(ticker=lambda s: {"last": last})
    ft._closed = []
    ft._close_usd = lambda sym, price, reason: ft._closed.append((reason, round(price, 4)))
    return ft


def _buf(highs, lows):
    return pd.DataFrame({"high": highs, "low": lows, "close": lows})


def test_short_tp_via_wick_even_if_last_above():
    # SHORT: tp=98 di bawah entry. last=99 (belum), tapi low candle 97.8 menyentuh tp.
    ft = _ft(99.0, {"side": "short", "entry": 100.0, "sl": 103.0, "tp": 98.0, "liq": 106.0})
    ft._monitor_usd("X", _buf([99.5, 99.2], [99.0, 97.8]))
    assert ft._closed == [("tp", 98.0)]


def test_long_tp_via_wick():
    # LONG: tp=102 di atas. last=101, tapi high candle 102.4 menyentuh tp.
    ft = _ft(101.0, {"side": "long", "entry": 100.0, "sl": 97.0, "tp": 102.0, "liq": 94.0})
    ft._monitor_usd("X", _buf([101.0, 102.4], [100.8, 100.9]))
    assert ft._closed == [("tp", 102.0)]


def test_no_close_when_wick_misses():
    ft = _ft(99.5, {"side": "short", "entry": 100.0, "sl": 103.0, "tp": 98.0, "liq": 106.0})
    ft._monitor_usd("X", _buf([99.8, 99.6], [99.2, 98.5]))   # low 98.5 > tp 98
    assert ft._closed == []


def test_sl_takes_priority_over_tp_same_bar():
    # SHORT: satu bar menyentuh SL (atas) DAN TP (bawah) → ambil SL (konservatif/merugikan).
    ft = _ft(100.0, {"side": "short", "entry": 100.0, "sl": 103.0, "tp": 98.0, "liq": 106.0})
    ft._monitor_usd("X", _buf([103.5], [97.5]))
    assert ft._closed == [("sl", 103.0)]


def test_liq_first():
    ft = _ft(100.0, {"side": "short", "entry": 100.0, "sl": 103.0, "tp": 98.0, "liq": 106.0})
    ft._monitor_usd("X", _buf([106.5], [97.5]))
    assert ft._closed == [("liq", 106.0)]


def test_fallback_to_last_when_no_buffer():
    # Tanpa buffer → pakai 'last' saja (perilaku lama tetap jalan).
    ft = _ft(97.9, {"side": "short", "entry": 100.0, "sl": 103.0, "tp": 98.0, "liq": 106.0})
    ft._monitor_usd("X", None)
    assert ft._closed == [("tp", 98.0)]
