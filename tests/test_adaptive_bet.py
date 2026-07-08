"""Sizing adaptif: %pool (auto-scale $10→naik) + cap margin bebas per-quote (akun kecil tak diam).
pool = margin per-quote (USDC/USDT dompet terpisah di live); locked = margin terkunci quote sama."""
import pytest

from bot.forward import ForwardTester
from bot.settings_store import RuntimeSettings, _from_dict

bet = ForwardTester._adaptive_bet


# ---------- mode fixed vs adaptif ----------

def test_fixed_bet_when_pct_zero():
    assert bet(100.0, 12.0, 0.0, 0.0) == 12.0


def test_pct_sizing_scales_with_balance():
    assert bet(20.0, 0.0, 10.0, 0.0) == 2.0        # $10 kecil → margin $2
    assert bet(200.0, 0.0, 10.0, 0.0) == 20.0      # tumbuh → margin ikut naik (auto-scale)


# ---------- guard modal-minim ----------

def test_caps_to_balance_when_bet_exceeds():
    # pool $10 dgn bet_usd default 12 → dulu DIAM TOTAL; kini dipakai $10 (bisa trade)
    assert bet(10.0, 12.0, 0.0, 0.0) == 10.0


def test_caps_to_free_margin_with_open_positions():
    assert bet(10.0, 12.0, 0.0, 8.0) == 2.0        # avail = pool 10 − locked 8


def test_zero_when_no_free_margin():
    assert bet(10.0, 5.0, 0.0, 10.0) == 0.0        # pool quote habis → skip


# ---------- skala conviction Gemini ----------

def test_gem_conviction_scaling_with_floor():
    assert bet(100.0, 10.0, 0.0, 0.0, gem_conv=0.5) == 5.0    # 10 × 0.5
    assert bet(100.0, 10.0, 0.0, 0.0, gem_conv=0.1) == 2.0    # lantai 20% → 10 × 0.2


# ---------- pool per-quote (dry split) ----------

def _ft(symbols, balance, frac=-1.0):
    import types
    ft = ForwardTester.__new__(ForwardTester)
    ft.live = False
    ft.symbols = symbols
    ft.balance_usd = balance
    ft.rs = types.SimpleNamespace(dry_quote_split_usdc=frac)
    return ft


def test_quote_pool_auto_proportional_to_pair_count():
    # 3 USDT + 1 USDC dari 4 pair → USDC dapat 1/4, USDT 3/4
    ft = _ft(["A/USDT:USDT", "B/USDT:USDT", "C/USDT:USDT", "D/USDC:USDC"], 100.0)
    assert ft._quote_pool("USDC") == 25.0
    assert ft._quote_pool("USDT") == 75.0


def test_quote_pool_explicit_frac_overrides_auto():
    ft = _ft(["A/USDT:USDT", "B/USDC:USDC"], 100.0, frac=0.3)
    assert ft._quote_pool("USDC") == 30.0
    assert ft._quote_pool("USDT") == 70.0


def test_quote_pool_out_of_range_falls_back_to_auto():
    ft = _ft(["A/USDC:USDC", "B/USDT:USDT"], 100.0, frac=2.0)   # invalid → auto 50/50
    assert ft._quote_pool("USDC") == 50.0


# ---------- settings ----------

def test_bet_pct_field_and_clamp():
    assert RuntimeSettings().bet_pct == 0.0
    s = _from_dict({"bet_pct": 250})
    assert s.bet_pct == 100.0                     # clamp ≤ 100
    assert _from_dict({"bet_pct": -5}).bet_pct == 0.0
