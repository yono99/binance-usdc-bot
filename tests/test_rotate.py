"""Layer 3 — Rotator: ranking, slot, cooldown, blacklist (pure)."""
import time

import pytest

from bot.rotate import Rotator
from bot.signals import Signal

CFG = {"rotate": {"max_open_positions": 3, "cooldown_minutes": 10, "blacklist_after_sl": 2}}


def _sig(sym, side="long", conf=0.5):
    return Signal(sym, side, conf, 100.0, 1.0, "r")


@pytest.fixture
def rot():
    return Rotator(CFG)


def test_rank_sorts_by_confidence_desc(rot):
    sigs = [_sig("A", conf=0.3), _sig("B", conf=0.9), _sig("C", conf=0.6)]
    ranked = rot.rank(sigs, set())
    assert [s.symbol for s in ranked] == ["B", "C", "A"]


def test_rank_filters_skip_open_and_unavailable(rot):
    sigs = [_sig("A", conf=0.8), _sig("B", side="skip"), _sig("C", conf=0.7), _sig("D", conf=0.6)]
    rot.cooldown_until["C"] = time.time() + 600       # cooldown → tak tersedia
    ranked = rot.rank(sigs, open_symbols={"D"})       # D sudah punya posisi
    assert [s.symbol for s in ranked] == ["A"]        # B skip, C cooldown, D open


def test_slots_free(rot):
    assert rot.slots_free(0) == 3
    assert rot.slots_free(2) == 1
    assert rot.slots_free(5) == 0                     # tak pernah negatif


def test_on_close_sets_cooldown(rot):
    assert rot.available("A") is True
    rot.on_close("A", was_sl=False)
    assert rot.available("A") is False                # cooldown aktif


def test_blacklist_after_consecutive_sl(rot):
    rot.on_close("A", was_sl=True)                    # streak 1
    assert "A" not in rot.blacklist_until or rot.blacklist_until["A"] < time.time()
    rot.on_close("A", was_sl=True)                    # streak 2 == blacklist_after_sl
    assert rot.blacklist_until["A"] > time.time()     # masuk blacklist
    assert rot.sl_streak["A"] == 0                    # streak di-reset setelah blacklist


def test_non_sl_close_resets_streak(rot):
    rot.on_close("A", was_sl=True)
    rot.on_close("A", was_sl=False)                   # menang → reset
    assert rot.sl_streak["A"] == 0
