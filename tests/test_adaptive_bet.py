"""Sizing adaptif: %saldo (auto-scale $10→naik) + cap margin bebas (akun kecil tak diam)."""
import pytest

from bot.forward import ForwardTester
from bot.settings_store import RuntimeSettings, _from_dict

bet = ForwardTester._adaptive_bet


# ---------- mode fixed vs adaptif ----------

def test_fixed_bet_when_pct_zero():
    assert bet(100.0, 12.0, 0.0, {}) == 12.0


def test_pct_sizing_scales_with_balance():
    assert bet(20.0, 0.0, 10.0, {}) == 2.0        # $10 kecil → margin $2
    assert bet(200.0, 0.0, 10.0, {}) == 20.0      # tumbuh → margin ikut naik (auto-scale)


# ---------- guard modal-minim ----------

def test_caps_to_balance_when_bet_exceeds():
    # akun $10 dgn bet_usd default 12 → dulu DIAM TOTAL; kini dipakai $10 (bisa trade)
    assert bet(10.0, 12.0, 0.0, {}) == 10.0


def test_caps_to_free_margin_with_open_positions():
    assert bet(10.0, 12.0, 0.0, {"A": {"bet": 8}}) == 2.0    # avail = 10-8


def test_zero_when_no_free_margin():
    assert bet(10.0, 5.0, 0.0, {"A": {"bet": 10}}) == 0.0    # margin habis → skip


# ---------- skala conviction Gemini ----------

def test_gem_conviction_scaling_with_floor():
    assert bet(100.0, 10.0, 0.0, {}, gem_conv=0.5) == 5.0    # 10 × 0.5
    assert bet(100.0, 10.0, 0.0, {}, gem_conv=0.1) == 2.0    # lantai 20% → 10 × 0.2


# ---------- settings ----------

def test_bet_pct_field_and_clamp():
    assert RuntimeSettings().bet_pct == 0.0
    s = _from_dict({"bet_pct": 250})
    assert s.bet_pct == 100.0                     # clamp ≤ 100
    assert _from_dict({"bet_pct": -5}).bet_pct == 0.0
