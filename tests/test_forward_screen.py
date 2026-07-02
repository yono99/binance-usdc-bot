"""Layer 2 di jalur forward: _screened() menyaring universe, cache, fail-open."""
import pytest

from bot import forward as fwd
from bot import screener
from bot.config import Settings
from bot.forward import ForwardTester, default_params


class _StubEx:
    def __init__(self, settings):
        self.settings = settings

    def usdc_symbols(self):
        return ["BTC/USDC:USDC", "CRV/USDC:USDC"]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(fwd, "Exchange", _StubEx)


def _ft(cfg):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    return ForwardTester(s, ["BTC/USDC:USDC"], default_params())


def test_screened_filters_and_caches(cfg, monkeypatch):
    ft = _ft(cfg)
    calls = []

    def fake_screen(ex, symbols, cfg_, tf):
        calls.append(list(symbols))
        return ["BTC/USDC:USDC"]                     # CRV gugur (spread lebar)

    monkeypatch.setattr(screener, "screen", fake_screen)
    base = ["BTC/USDC:USDC", "CRV/USDC:USDC"]
    assert ft._screened(base) == ["BTC/USDC:USDC"]
    assert ft._screened(base) == ["BTC/USDC:USDC"]   # kedua: dari cache
    assert len(calls) == 1                            # screen() cuma dipanggil sekali


def test_screened_fail_open_on_infra_error(cfg, monkeypatch):
    ft = _ft(cfg)

    def boom(*a, **k):
        raise RuntimeError("api down")

    monkeypatch.setattr(screener, "screen", boom)
    base = ["BTC/USDC:USDC", "CRV/USDC:USDC"]
    assert ft._screened(base) == base                 # infra gagal → tanpa saring, bot jalan


def test_screened_empty_respected_not_long_cached(cfg, monkeypatch):
    ft = _ft(cfg)
    monkeypatch.setattr(screener, "screen", lambda *a, **k: [])
    assert ft._screened(["BTC/USDC:USDC"]) == []      # pasar sepi → idle (hormati ambang)
    assert ft._screen_cache[3] == 180                 # cache pendek → cepat pulih
