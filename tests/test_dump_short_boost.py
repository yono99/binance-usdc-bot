"""btc.dump_short_boost — SHORT conviction ×1.5 saat dump_flag (default OFF)."""
from bot.forward import ForwardTester


def _ft(cfg: dict) -> ForwardTester:
    ft = ForwardTester.__new__(ForwardTester)
    ft.cfg = cfg
    return ft


def test_dump_short_boost_default_off():
    assert _ft({})._dump_short_boost_enabled() is False
    assert _ft({"btc": {}})._dump_short_boost_enabled() is False
    assert _ft({"btc": {"dump_short_boost": False}})._dump_short_boost_enabled() is False


def test_dump_short_boost_opt_in():
    assert _ft({"btc": {"dump_short_boost": True}})._dump_short_boost_enabled() is True


def test_config_yaml_default_off():
    from bot.config import load_settings
    s = load_settings()
    assert s.raw.get("btc", {}).get("dump_short_boost", True) is False
