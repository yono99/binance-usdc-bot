"""Skor Brier untuk close LIVE via reconcile — hanya saat PnL tak ambigu (tepat 1 tutup)."""
import types

import bot.forward as fwd
import bot.store as store
from bot.forward import ForwardTester


def _self(equity_after, *, open_pos, monkeypatch):
    calls, settled = [], []
    monkeypatch.setattr(store, "log_calibration",
                        lambda tid, sym, prob, out, mode: calls.append((tid, sym, prob, out, mode)))
    monkeypatch.setattr(fwd, "journal", lambda ev, data: None)
    ex = types.SimpleNamespace(
        positions=lambda: [],                                  # semua posisi sudah tutup di bursa
        equity_usdc=lambda bal: equity_after,                  # legacy balance (Tahap 0)
        balances=lambda bal: {"USDT": 50.0, "USDC": equity_after - 50.0},  # Tahap 1: per-wallet
        client=types.SimpleNamespace(cancel_all_orders=lambda s: None),
    )
    # Tahap 1: per-wallet saldo — set dummy wallet balance_up agar _live_reconcile &
    # balance-tracker seluruh method bisa baca balance_usdt/usdc.
    self = types.SimpleNamespace(
        ex=ex, open=open_pos, pending={},
        balance_usdt=50.0, balance_usdc=50.0,
        _day_pnl=0.0, _day_start_balance=100.0,
        _day_pnl_usdt=0.0, _day_pnl_usdc=0.0,
        _day_start_balance_usdt=50.0, _day_start_balance_usdc=50.0,
        _peak_balance_usdt=50.0, _peak_balance_usdc=50.0,
        _dd_lock=False, _dd_reason="",
        gtrader=types.SimpleNamespace(settle=lambda did, r: settled.append((did, r)),
                                      reflect=lambda: {"active_lessons": 0}),
        settings=types.SimpleNamespace(mode="live"),
        _gem_closes=0, _calib_drifting=False,
        notify=types.SimpleNamespace(send=lambda m: None),
        _react_link=lambda *a: None,
    )
    return self, calls, settled


def _pos():
    return {"gdecision": 7, "conviction": 0.62, "bet": 10.0, "side": "long", "entry": 100.0}


def test_profit_scores_outcome_1(monkeypatch):
    self, calls, settled = _self(105.0, open_pos={"BTC/USDC:USDC": _pos()}, monkeypatch=monkeypatch)
    ForwardTester._live_reconcile(self)
    assert calls == [(7, "BTC/USDC:USDC", 0.62, 1, "live")]    # Brier tercatat per-mode, outcome=profit
    assert settled == [(7, 0.5)]                               # r = (105-100)/10


def test_loss_scores_outcome_0(monkeypatch):
    self, calls, _ = _self(96.0, open_pos={"BTC/USDC:USDC": _pos()}, monkeypatch=monkeypatch)
    ForwardTester._live_reconcile(self)
    assert calls == [(7, "BTC/USDC:USDC", 0.62, 0, "live")]


def test_multi_close_ambiguous_no_brier(monkeypatch):
    # dua posisi Gemini tutup bersamaan → Δequity ambigu → JANGAN skor (data kotor)
    self, calls, settled = _self(105.0, monkeypatch=monkeypatch, open_pos={
        "BTC/USDC:USDC": _pos(), "ETH/USDC:USDC": {**_pos(), "gdecision": 8}})
    ForwardTester._live_reconcile(self)
    assert calls == []                                         # tak ada Brier saat ambigu
    assert settled == []


def test_no_conviction_no_brier(monkeypatch):
    p = _pos(); p.pop("conviction")
    self, calls, settled = _self(105.0, open_pos={"BTC/USDC:USDC": p}, monkeypatch=monkeypatch)
    ForwardTester._live_reconcile(self)
    assert calls == []                                         # tanpa angka confidence → tak diskor
    assert settled == [(7, 0.5)]                               # tapi tetap di-settle (belajar R)
