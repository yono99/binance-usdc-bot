"""Kill-switch drawdown TOTAL (P1 tujuan compounding): kunci saat tembus ambang,
BERTAHAN melewati restart, lepas HANYA via reset manual. CB harian tak menangkap
bleed pelan lintas-hari — ini yang menangkapnya."""
import pytest

from bot import forward as fwd
from bot import store
from bot.config import Settings
from bot.forward import ForwardTester, default_params
from bot.settings_store import RuntimeSettings


class _StubEx:
    def __init__(self, settings):
        self.settings = settings

    def usdc_symbols(self):
        return ["BTC/USDC:USDC"]


class _StubNotify:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch, tmp_path):
    monkeypatch.setattr(fwd, "Exchange", _StubEx)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bot.db")


def _ft(cfg):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    ft = ForwardTester(s, ["BTC/USDC:USDC"], default_params())
    ft.notify = _StubNotify()
    return ft


def test_dd_check_math():
    check = ForwardTester._dd_check
    assert check(100.0, 79.0, 20.0) == (True, 21.0)     # 21% >= 20% → kunci
    assert check(100.0, 81.0, 20.0) == (False, 19.0)    # 19% < 20% → aman
    assert check(100.0, 50.0, 0.0) == (False, 0.0)      # 0 = nonaktif
    assert check(0.0, 50.0, 20.0) == (False, 0.0)       # belum ada puncak


def test_lock_triggers_notifies_and_blocks(cfg):
    ft = _ft(cfg)
    rs = RuntimeSettings(max_drawdown_pct=20.0)
    ft.balance_usdc = 100.0
    ft.balance_usdt = 0.0
    assert ft._update_drawdown(rs) is None              # puncak terbentuk: 100
    ft.balance_usdc = 79.0                              # −21% dari puncak
    reason = ft._update_drawdown(rs)
    assert reason and "drawdown total" in reason
    assert ft._dd_lock and ft.notify.sent                # telegram terkirim
    ft.balance_usdc = 95.0                              # pulih pun TETAP terkunci
    assert ft._update_drawdown(rs) is not None           # tanpa reset manual: kunci abadi


def test_lock_survives_restart_via_persisted_state(cfg):
    ft = _ft(cfg)
    ft.balance_usdc = 79.0
    ft.balance_usdt = 0.0
    ft._last_cfg_balance_usdc = 79.0
    ft._last_cfg_balance_usdt = 0.0
    ft._peak_balance_usdc = 100.0
    ft._peak_balance_usdt = 0.0
    ft._update_drawdown(RuntimeSettings(max_drawdown_pct=20.0))
    ft._persist_state()

    ft2 = _ft(cfg)                                       # "restart" proses
    ft2._last_cfg_balance_usdc = 79.0
    ft2._last_cfg_balance_usdt = 0.0
    ft2._restore_state()
    assert ft2._dd_lock is True                          # kunci ikut pulih
    assert ft2._peak_balance_usdc == 100.0


def test_manual_reset_unlocks_and_rebases_peak(cfg):
    ft = _ft(cfg)
    rs = RuntimeSettings(max_drawdown_pct=20.0)
    ft.balance_usdc = 100.0
    ft.balance_usdt = 0.0
    ft._update_drawdown(rs)
    ft.balance_usdc = 75.0
    assert ft._update_drawdown(rs) is not None           # terkunci

    store.set_kv("dd_reset_dry", {"ts": "now"})          # = POST /api/dd-reset
    assert ft._update_drawdown(rs) is None               # lepas
    assert ft._peak_balance_usdc == 75.0                 # puncak mulai ulang dari saldo kini
    assert not store.get_kv("dd_reset_dry")              # permintaan habis pakai
