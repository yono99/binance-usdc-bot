from bot.risk import RiskGate
from bot.signals import Signal


def test_long_sizing_and_levels(cfg):
    g = RiskGate(cfg)
    sig = Signal("BTCUSDC", "long", 0.7, 100.0, 2.0, "x")
    d = g.evaluate(sig, 1000.0, 0.0)
    assert d.ok
    assert d.qty > 0
    assert d.sl < 100.0 < d.tp


def test_short_inverts_levels(cfg):
    g = RiskGate(cfg)
    sig = Signal("BTCUSDC", "short", 0.7, 100.0, 2.0, "x")
    d = g.evaluate(sig, 1000.0, 0.0)
    assert d.ok
    assert d.sl > 100.0 > d.tp


def test_zero_atr_rejected(cfg):
    g = RiskGate(cfg)
    sig = Signal("BTCUSDC", "long", 0.7, 100.0, 0.0, "x")
    assert not g.evaluate(sig, 1000.0, 0.0).ok


def test_daily_loss_breaker_retired_when_zero(cfg):
    """daily_max_loss_pct=0 (default) → breaker rugi harian OFF; hanya max trades."""
    g = RiskGate(cfg)
    assert float(cfg["risk"].get("daily_max_loss_pct") or 0) == 0.0
    assert not g.breaker_tripped(1000.0)
    g.record_close(-500.0)  # rugi besar — tetap tak trip (retired)
    assert not g.breaker_tripped(1000.0)


def test_daily_loss_breaker_still_works_if_enabled(cfg):
    """Legacy path: bila daily_max_loss_pct > 0, circuit harian masih jalan."""
    cfg = {**cfg, "risk": {**cfg["risk"], "daily_max_loss_pct": 3.0}}
    g = RiskGate(cfg)
    assert not g.breaker_tripped(1000.0)
    loss = 3.0 / 100 * 1000 + 1
    g.record_close(-loss)
    assert g.breaker_tripped(1000.0)
