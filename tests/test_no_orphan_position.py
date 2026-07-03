"""Posisi terbuka tak boleh 'orphan' — hilang dari pemantauan (SL/TP/give-back)
hanya karena simbolnya gugur dari screening. Insiden nyata: BCH/USDC:USDC
punya posisi terbuka, tapi screening berikutnya menghasilkan set tanpa BCH
(likuiditas berubah) -> self.symbols menimpa penuh -> BCH tak pernah dicek
_monitor_usd lagi walau masih live di self.open (real money exposure)."""
import pytest

from bot import forward as fwd
from bot import screener
from bot.config import Settings
from bot.forward import ForwardTester, default_params
from bot.settings_store import RuntimeSettings


class _StubEx:
    def __init__(self, settings):
        self.settings = settings

    def perp_symbols(self, settles):
        return ["ETH/USDC:USDC"]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(fwd, "Exchange", _StubEx)


def _ft(cfg):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    return ForwardTester(s, ["BTC/USDC:USDC"], default_params())   # use_store=False (rakitan penuh, murah)


def test_apply_settings_keeps_open_position_symbol_even_if_screened_out(cfg, monkeypatch):
    ft = _ft(cfg)
    ft.pin_mode = True
    ft.open = {"BCH/USDC:USDC": {"side": "long"}}     # posisi TERBUKA sungguhan
    ft.symbols = ["ETH/USDC:USDC"]        # siklus lalu: BCH SUDAH gugur (skenario insiden nyata)
    seeded = []
    monkeypatch.setattr(ft, "seed", lambda: seeded.append(list(ft.symbols)))
    # Screening TERBARU menggugurkan BCH (mis. likuiditas turun) -> hanya ETH lolos
    monkeypatch.setattr(screener, "screen", lambda ex, syms, cfg_, tf: ["ETH/USDC:USDC"])
    monkeypatch.setattr(fwd, "load_settings", lambda mode=None: RuntimeSettings(mode="dry"))

    ft._apply_settings()

    assert "BCH/USDC:USDC" in ft.symbols            # posisi terbuka TETAP dipantau
    assert "ETH/USDC:USDC" in ft.symbols            # hasil screening tetap masuk
    assert seeded and "BCH/USDC:USDC" in seeded[0]   # re-seed juga menyertakan BCH


def test_init_use_store_unions_restored_open_positions(cfg, monkeypatch):
    monkeypatch.setattr(screener, "screen", lambda ex, syms, cfg_, tf: ["ETH/USDC:USDC"])
    monkeypatch.setattr(fwd, "load_settings",
                        lambda mode=None: RuntimeSettings(symbols=[], mode="dry"))
    monkeypatch.setattr(fwd.ForwardTester, "_restore_state",
                        lambda self: self.open.update({"BCH/USDC:USDC": {"side": "long"}}))
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    ft = fwd.ForwardTester(s, [], default_params(), use_store=True)
    assert "BCH/USDC:USDC" in ft.symbols          # posisi restored tak boleh hilang sejak boot
    assert "ETH/USDC:USDC" in ft.symbols
