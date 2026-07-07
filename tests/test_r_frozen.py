"""R-multiple pakai risk0 BEKU saat open — SL yang di-trail (breakeven/tighten)
tidak boleh mengecilkan penyebut R (bug PLAY/USDT R=-231 dari rugi $0.01)."""
import pytest

from bot import forward as fwd
from bot.config import Settings
from bot.forward import ForwardTester, default_params


class _Client:
    def fetch_funding_rate(self, sym):
        return {"fundingRate": 0.0}


class _StubEx:
    def __init__(self, settings):
        self.settings = settings
        self.client = _Client()

    def usdc_symbols(self):
        return ["BTC/USDC:USDC"]

    def ticker(self, sym):
        return {"last": 100.0}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(fwd, "Exchange", _StubEx)


def _ft(cfg, monkeypatch):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    ft = ForwardTester(s, ["BTC/USDC:USDC"], default_params())
    monkeypatch.setattr(fwd, "journal", lambda *a, **k: None)
    monkeypatch.setattr(fwd.vrp, "log_close", lambda *a, **k: None)
    ft._react_settle = lambda *a, **k: None
    ft.balance_usd = 100.0
    ft.slippage = 0.0
    ft.fee = 0.0
    return ft


def _pos(**over):
    p = {"side": "long", "entry": 100.0, "qty": 1.0, "sl": 90.0, "tp": 120.0,
         "liq": 50.0, "bet": 10.0, "risk0": 10.0}   # 1R beku = |100-90|×1
    p.update(over)
    return p


def test_r_uses_frozen_risk0_after_sl_trailed(cfg, monkeypatch):
    ft = _ft(cfg, monkeypatch)
    p = _pos()
    p["sl"] = 99.9                    # SL sudah di-trail ke breakeven
    ft.open = {"X": p}
    ft._close_usd("X", 99.9, "sl")    # rugi kecil -$0.1
    r = ft.trades[-1].r
    assert abs(r - (-0.01)) < 1e-9    # -0.1 / 10 (risk0 asli), BUKAN -0.1/0.1 = -1


def test_r_fallback_without_risk0_stamp(cfg, monkeypatch):
    ft = _ft(cfg, monkeypatch)        # posisi lama tanpa stempel risk0
    p = _pos()
    del p["risk0"]
    ft.open = {"X": p}
    ft._close_usd("X", 110.0, "tp")   # +$10 / |100-90|×1 = +1R
    assert abs(ft.trades[-1].r - 1.0) < 1e-9
