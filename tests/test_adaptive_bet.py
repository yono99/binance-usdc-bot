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


# ---------- pool per-quote (Tahap 1: per-wallet split eksplisit) ----------

def _ft(symbols, balance_usdc=0.0, balance_usdt=0.0):
    """pool per-wallet — wallets disetel eksplisit (split USDT/USDC)."""
    import types
    ft = ForwardTester.__new__(ForwardTester)
    ft.live = False
    ft.symbols = symbols
    ft.balance_usdc = balance_usdc
    ft.balance_usdt = balance_usdt
    ft.rs = types.SimpleNamespace()
    return ft


def test_quote_pool_uses_separate_wallets():
    """pool = self.balance_<wallet> langsung. Wallets independent."""
    ft = _ft(["D/USDC:USDC"], balance_usdc=25.0, balance_usdt=75.0)
    assert ft._quote_pool("USDC") == 25.0
    assert ft._quote_pool("USDT") == 75.0


def test_quote_pool_live_uses_exchange_balances():
    """LIVE: pool dari Exchange.balances() (dompet Binance nyata)."""
    import types
    from bot.forward import ForwardTester
    ft = ForwardTester.__new__(ForwardTester)
    ft.live = True
    ft.ex = types.SimpleNamespace(balances=lambda fb: {"USDC": 100.0, "USDT": 50.0})
    assert ft._quote_pool("USDC") == 100.0
    assert ft._quote_pool("USDT") == 50.0


# ---------- settings ----------

def test_bet_pct_field_and_clamp():
    assert RuntimeSettings().bet_pct == 0.0
    s = _from_dict({"bet_pct": 250})
    assert s.bet_pct == 100.0                     # clamp ≤ 100
    assert _from_dict({"bet_pct": -5}).bet_pct == 0.0
