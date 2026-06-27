import bot.settings_store as ss
from bot.settings_store import RuntimeSettings, liquidation_price


def test_presets_have_required_keys():
    keys = {"entry_confidence", "sl_atr_mult", "tp_atr_mult", "use_htf", "regime",
            "use_funding", "use_oi", "use_of"}
    for name, p in ss.PRESETS.items():
        assert "timeframe" in p
        assert keys.issubset(p.keys()), name
    s = RuntimeSettings(technique="scalping")
    assert set(s.params().keys()) == keys
    assert s.timeframe() == "5m"


def test_clamp_bounds():
    s = RuntimeSettings(leverage=999, bet_usd=-5, technique="ngawur", symbols=[]).clamp()
    assert s.leverage == 125
    assert s.bet_usd >= 0.1
    assert s.technique == "auto"
    assert s.symbols == ["BTC/USDC:USDC"]
    assert RuntimeSettings(leverage=0).clamp().leverage == 1


def test_liquidation_frac_and_price():
    s100 = RuntimeSettings(leverage=100)
    assert abs(s100.liquidation_frac() - (0.01 - 0.005)) < 1e-9     # ~0.5%
    s10 = RuntimeSettings(leverage=10)
    assert abs(s10.liquidation_frac() - (0.1 - 0.005)) < 1e-9
    # long liq di bawah entry, short di atas
    assert abs(liquidation_price(100, True, 0.005) - 99.5) < 1e-9
    assert abs(liquidation_price(100, False, 0.005) - 100.5) < 1e-9


def test_x100_liquidation_is_tiny_move():
    """x100 -> gerakan ~0.5% sudah likuidasi (bukti kejujuran UI)."""
    assert RuntimeSettings(leverage=100).liquidation_frac() < 0.01


def test_default_leverage_is_100x():
    s = RuntimeSettings()
    assert s.leverage == 100
    assert s.liquidation_frac() < 0.01        # default = likuidasi pada gerakan <1%


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "STORE", tmp_path / "runtime.json")
    ss.save_settings(RuntimeSettings(enabled=True, technique="swing", leverage=20,
                                     bet_usd=12, balance_usd=12, symbols=["ETH/USDC:USDC"]))
    got = ss.load_settings()
    assert got.enabled is True
    assert got.technique == "swing"
    assert got.leverage == 20
    assert got.symbols == ["ETH/USDC:USDC"]
    assert got.timeframe() == "1h"
