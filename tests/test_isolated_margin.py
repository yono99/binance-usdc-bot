"""Tahap 2 (plan-sess) — Margin ISOLATED di forward (paper stempel + live wrapper)."""
from __future__ import annotations

import pytest


def test_exchange_set_margin_isolated_dry_noop(monkeypatch):
    """DRY mode: set_margin_isolated = no-op, return True."""
    # Skip Exchange() init yg butuh jaringan — pakai SimpleNamespace wrapper.
    from types import SimpleNamespace
    from bot.exchange import Exchange
    ex = SimpleNamespace(settings=SimpleNamespace(is_dry=True))
    # Pakai unbound method via instance:
    assert Exchange.set_margin_isolated(ex, "BTC/USDC:USDC") is True


def test_exchange_set_margin_isolated_handles_4046():
    """Live: error -4046 'No need to change' di-ignore (sudah isolated)."""
    from types import SimpleNamespace
    from bot.exchange import Exchange

    class _StubClient:
        def fapiPrivatePostMarginType(self, payload):
            raise Exception("-4046 No need to change margin type")

    ex = SimpleNamespace(settings=SimpleNamespace(is_dry=False),
                         client=_StubClient())
    assert Exchange.set_margin_isolated(ex, "BTC/USDC:USDC") is True


def test_exchange_set_margin_isolated_real_error_returns_false(caplog):
    """Live: error lain (rate limit, network) → log warning + return False (best-effort)."""
    import logging
    from types import SimpleNamespace
    from bot.exchange import Exchange

    class _StubClient:
        def fapiPrivatePostMarginType(self, payload):
            raise Exception("rate limited")

    ex = SimpleNamespace(settings=SimpleNamespace(is_dry=False),
                         client=_StubClient())
    with caplog.at_level(logging.WARNING):
        assert Exchange.set_margin_isolated(ex, "BTC/USDC:USDC") is False


def test_margin_sim_liq_price_isolated():
    """IsolatedSim.liq_price(): 1/lev − maint (Binance formula)."""
    from bot.margin import IsolatedSim
    # long, lev=100, maint=0.005 → frac ~= 0.005 → entry*(1-0.005)
    liq_long = IsolatedSim.liq_price(100.0, is_long=True, leverage=100, maint_frac=0.005)
    assert liq_long == pytest.approx(99.5, rel=1e-3)
    liq_short = IsolatedSim.liq_price(100.0, is_long=False, leverage=100, maint_frac=0.005)
    assert liq_short == pytest.approx(100.5, rel=1e-3)


def test_margin_sim_max_loss_equals_margin():
    """Isolated: rugi maksimum = margin (bet). Konsisten dengan _close_usd: max(..., -bet)."""
    from bot.margin import IsolatedSim
    assert IsolatedSim.max_loss(12.0) == 12.0


def test_margin_sim_annotate_sets_metadata():
    """annotate() stempel margin_type='ISOLATED' di posisi (paper)."""
    from bot.margin import IsolatedSim
    pos = {"side": "long", "bet": 12.0}
    IsolatedSim.annotate(pos)
    assert pos["margin_type"] == "ISOLATED"
    assert pos["isolated_margin"] == 12.0
