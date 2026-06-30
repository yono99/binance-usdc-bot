"""Engine.tick — perilaku close→outcome (Phase 2) dgn kolaborator di-stub.
Engine.__init__ butuh Exchange (jaringan) → kita rakit instance via __new__."""
import types

import pytest

from bot import decision_log, evolve
from bot.engine import Engine


def _engine(monkeypatch, monitor_result, breaker=True):
    eng = Engine.__new__(Engine)
    eng.cfg = {"risk": {"account_risk_pct": 1.0}, "signals": {"entry_confidence": 0.5}}
    eng.ex = types.SimpleNamespace(equity_usdc=lambda: 1000.0, ticker=lambda s: {"last": 100.0})
    eng._risk0 = {"X": 50.0}
    eng.risk = types.SimpleNamespace(
        record_close=lambda pnl: None,
        breaker_tripped=lambda eq: breaker,            # True → tick berhenti setelah close loop
        daily=types.SimpleNamespace(realized_pnl=0.0))
    eng.rotator = types.SimpleNamespace(on_close=lambda s, w: None)
    eng.pm = types.SimpleNamespace(open={}, monitor=lambda price_of: monitor_result)
    eng.lessons = types.SimpleNamespace(
        record_trigger=lambda *a, **k: None,
        derive_from_trade=lambda r: None,
        score_and_retire=lambda: 0)
    # isolasi: jangan sentuh evolusi nyata / file decision_log nyata
    monkeypatch.setattr(evolve, "run", lambda *a, **k: {})
    return eng


def test_tick_records_tp_outcome_with_r(monkeypatch):
    cap = {}
    monkeypatch.setattr(decision_log, "record_outcome",
                        lambda sym, outcome, r, **k: cap.update(sym=sym, outcome=outcome, r=r) or "id1")
    monkeypatch.setattr(decision_log, "get", lambda did: {"id": did, "lesson_triggered": ""})
    eng = _engine(monkeypatch, [("X", 75.0, False)])     # profit, bukan SL
    eng.tick()
    assert cap["sym"] == "X" and cap["outcome"] == "TP_HIT"
    assert cap["r"] == pytest.approx(75.0 / 50.0)        # pnl / 1R(=50)


def test_tick_records_sl_outcome(monkeypatch):
    cap = {}
    monkeypatch.setattr(decision_log, "record_outcome",
                        lambda sym, outcome, r, **k: cap.update(outcome=outcome) or "id2")
    monkeypatch.setattr(decision_log, "get", lambda did: None)
    eng = _engine(monkeypatch, [("X", -50.0, True)])     # kena SL
    eng.tick()
    assert cap["outcome"] == "SL_HIT"


def test_tick_triggers_lesson_accuracy(monkeypatch):
    seen = {}
    monkeypatch.setattr(decision_log, "record_outcome", lambda *a, **k: "id3")
    monkeypatch.setattr(decision_log, "get",
                        lambda did: {"id": did, "lesson_triggered": "L1"})
    eng = _engine(monkeypatch, [("X", 75.0, False)])
    eng.lessons.record_trigger = lambda lid, correct: seen.update(lid=lid, correct=correct)
    eng.tick()
    assert seen == {"lid": "L1", "correct": True}        # outcome_r>0 → benar
