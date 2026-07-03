"""Regresi: init (use_store) HARUS menyaring universe lewat screener sebelum
seed — jangan seed universe RAW (ratusan pair) lalu menyaring baru di siklus
berikutnya. Whitelist kosong = auto-pilih dari screener, sejak startup."""
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
        return [f"S{i}/USDT:USDT" for i in range(300)]   # universe raksasa, RAW


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(fwd, "Exchange", _StubEx)
    # Isolasi dari SQLite SUNGGUHAN (mode "dry" bisa berisi posisi terbuka nyata dari bot yg
    # sedang jalan) — union posisi-terbuka (anti-orphan) tak boleh kebocoran state produksi.
    monkeypatch.setattr(fwd.ForwardTester, "_restore_state", lambda self: None)


def test_init_use_store_screens_raw_universe_before_seed(cfg, monkeypatch):
    monkeypatch.setattr(fwd, "load_settings",
                        lambda mode=None: RuntimeSettings(symbols=[], mode="dry"))
    seen = []

    def fake_screen(ex, symbols, cfg_, tf):
        seen.append(list(symbols))
        return ["BTC/USDT:USDT"]                          # hanya 1 lolos dari 300

    monkeypatch.setattr(screener, "screen", fake_screen)
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    ft = ForwardTester(s, [], default_params(), use_store=True)
    assert ft.symbols == ["BTC/USDT:USDT"]                # HASIL SARINGAN, bukan 300 raw
    assert seen and len(seen[0]) <= 60                    # prefilter (universe >80) sudah jalan duluan
    assert seen[0] != [f"S{i}/USDT:USDT" for i in range(300)]  # BUKAN universe mentah utuh


def test_init_explicit_symbols_skip_screener(cfg, monkeypatch):
    """rs.symbols eksplisit (bukan kosong) → dipakai APA ADANYA, tak disaring ulang
    (pengguna sudah memilih whitelist sendiri di UI)."""
    monkeypatch.setattr(fwd, "load_settings",
                        lambda mode=None: RuntimeSettings(symbols=["ETH/USDC:USDC"], mode="dry"))
    called = []
    monkeypatch.setattr(screener, "screen", lambda *a, **k: called.append(1) or [])
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    ft = ForwardTester(s, [], default_params(), use_store=True)
    assert ft.symbols == ["ETH/USDC:USDC"]
    assert called == []                                    # screener TAK dipanggil
