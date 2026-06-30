"""Mode full-auto: satu flag agent.full_auto menyalakan tool_loop + autonomous."""
import pytest

from bot import forward as fwd
from bot.config import Settings
from bot.forward import ForwardTester, default_params


class _StubEx:
    def __init__(self, settings):
        self.settings = settings


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(fwd, "Exchange", _StubEx)     # hindari ccxt/jaringan di __post_init__


def _build(cfg, agent_over):
    raw = {**cfg, "agent": {**cfg.get("agent", {}), **agent_over}}
    s = Settings(mode="dry", raw=raw, gemini_keys=[], gemini_enabled=False)
    return ForwardTester(s, ["BTC/USDC:USDC"], default_params())


def test_full_auto_enables_both(cfg):
    ft = _build(cfg, {"full_auto": True})
    assert ft.tool_loop is True and ft.autonomous is True


def test_default_off(cfg):
    ft = _build(cfg, {"full_auto": False, "tool_loop": False, "autonomous": False})
    assert ft.tool_loop is False and ft.autonomous is False


def test_individual_flags_still_work(cfg):
    ft = _build(cfg, {"full_auto": False, "tool_loop": True, "autonomous": False})
    assert ft.tool_loop is True and ft.autonomous is False
