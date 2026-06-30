"""Port ReAct ke ForwardTester: pemetaan gerbang entry (permit/deny) tanpa jaringan.
Logika inti ReAct/lessons/decision_log diuji terpisah; ini hanya menguji glue forward."""
import types

from bot.forward import ForwardTester


def _bare_ft():
    """ForwardTester minimal (tanpa __post_init__/Exchange) untuk uji helper murni."""
    ft = ForwardTester.__new__(ForwardTester)
    ft.balance_usd = 1000.0
    ft.risk_frac = 0.01
    ft.open = {}
    ft.max_open = 2
    ft._day_pnl = 0.0
    ft.cfg = {"signals": {}, "strategy": {}}     # _market_summary akan gagal → regime "unknown"
    ft.lessons = types.SimpleNamespace(recent=lambda n: [])

    def _raise(*a, **k):
        raise RuntimeError("no exchange in test")
    ft._alt_arrays = _raise                       # paksa alt={} (boundary)
    return ft


class _Dec:
    def __init__(self, action):
        self.action, self.reasoning = action, "stub"

    def permits(self, sig):
        if sig.side == "long":
            return self.action == "ENTER_LONG"
        if sig.side == "short":
            return self.action == "ENTER_SHORT"
        return False


def test_gate_permits_when_agent_enters():
    ft = _bare_ft()
    ft.react = types.SimpleNamespace(decide=lambda sig, **k: _Dec("ENTER_LONG"))
    permitted, action, _ = ft._react_gate("BTC/USDC:USDC", 1, 2.0, None, 100.0)
    assert permitted is True and action == "ENTER_LONG"


def test_gate_denies_when_agent_skips():
    ft = _bare_ft()
    ft.react = types.SimpleNamespace(decide=lambda sig, **k: _Dec("SKIP"))
    permitted, action, _ = ft._react_gate("BTC/USDC:USDC", 1, 2.0, None, 100.0)
    assert permitted is False and action == "SKIP"


def test_gate_denies_on_opposite_direction():
    ft = _bare_ft()
    ft.react = types.SimpleNamespace(decide=lambda sig, **k: _Dec("ENTER_SHORT"))
    # sinyal long tapi agen ENTER_SHORT → tak searah → tak buka (aman)
    permitted, action, _ = ft._react_gate("BTC/USDC:USDC", 1, 2.0, None, 100.0)
    assert permitted is False
