"""P3: simulasi funding di paper — akrual per posisi, tanda benar (long bayar
rate positif, short menerima), dipotong saat close, live tidak dobel-tagih."""
import pytest

from bot import forward as fwd
from bot.config import Settings
from bot.forward import ForwardTester, default_params


class _Client:
    def __init__(self, rate=0.0001):
        self.rate = rate

    def fetch_funding_rate(self, sym):
        return {"fundingRate": self.rate}


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


def _ft(cfg):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    return ForwardTester(s, ["BTC/USDC:USDC"], default_params())


def _pos(side):
    return {"side": side, "entry": 100.0, "qty": 1.0, "sl": 90.0, "tp": 120.0,
            "liq": 50.0, "bet": 10.0}


def test_accrual_sign_and_crossings(cfg):
    ft = _ft(cfg)
    ft.open = {"L": _pos("long"), "S": _pos("short")}
    ft._fund_epoch = 100                       # terakhir diproses jendela ke-100
    import time as _t
    epoch_now = int(_t.time() // 28800)
    ft._fund_epoch = epoch_now - 2             # 2 settlement terlewati
    ft._apply_funding_sim()
    # rate +0.01% × qty 1 × harga 100 × 2 crossing = $0.02
    assert abs(ft.open["L"]["funding_paid"] - 0.02) < 1e-9    # long MEMBAYAR
    assert abs(ft.open["S"]["funding_paid"] + 0.02) < 1e-9    # short MENERIMA
    ft._apply_funding_sim()                    # epoch sama → tak menagih lagi
    assert abs(ft.open["L"]["funding_paid"] - 0.02) < 1e-9


def test_first_call_never_backcharges(cfg):
    ft = _ft(cfg)
    ft.open = {"L": _pos("long")}
    assert not hasattr(ft, "_fund_epoch")
    ft._apply_funding_sim()                    # panggilan pertama: cuma set anchor
    assert "funding_paid" not in ft.open["L"]


def test_live_mode_skips_simulation(cfg):
    ft = _ft(cfg)
    ft.live = True
    ft.open = {"L": _pos("long")}
    ft._fund_epoch = 0                          # banyak crossing pun...
    ft._apply_funding_sim()
    assert "funding_paid" not in ft.open["L"]   # ...live tak disimulasikan (exchange yg motong)


def test_close_deducts_funding_from_pnl(cfg, monkeypatch):
    from bot import forward_close as fclose
    import bot.store as store
    ft = _ft(cfg)
    monkeypatch.setattr(fclose, "journal", lambda *a, **k: None)
    monkeypatch.setattr(fclose.vrp, "log_close", lambda *a, **k: None)
    monkeypatch.setattr(fclose.mtf, "log_close", lambda *a, **k: None)
    monkeypatch.setattr(store, "close_exists", lambda *a, **k: False)
    ft._react_settle = lambda *a, **k: None
    ft.balance_usdt = 100.0
    ft.balance_usdc = 0.0
    ft.slippage = 0.0
    ft.fee = 0.0
    p = _pos("long")
    p["funding_paid"] = 0.5
    ft.open = {"X": p}
    ft._close_usd("X", 110.0, "tp")             # gross +$10, funding −$0.5
    assert abs((ft.balance_usdt + ft.balance_usdc) - 109.5) < 1e-9
