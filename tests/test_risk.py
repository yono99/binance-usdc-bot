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


def test_daily_circuit_breaker(cfg):
    g = RiskGate(cfg)
    assert not g.breaker_tripped(1000.0)
    loss = cfg["risk"]["daily_max_loss_pct"] / 100 * 1000 + 1
    g.record_close(-loss)
    assert g.breaker_tripped(1000.0)
