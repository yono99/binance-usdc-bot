"""Settings & load_settings — mode, kredensial, validasi."""
import pytest

from bot.config import Settings, load_settings


def _s(mode):
    return Settings(mode=mode, raw={"x": {"y": 1}})


def test_is_dry_for_dry_and_test():
    assert _s("dry").is_dry is True
    assert _s("test").is_dry is True       # testnet deprecated → diperlakukan paper
    assert _s("live").is_dry is False


def test_is_live():
    assert _s("live").is_live is True
    assert _s("dry").is_live is False


def test_credentials_empty_for_paper(monkeypatch):
    monkeypatch.setenv("BINANCE_LIVE_KEY", "K")
    monkeypatch.setenv("BINANCE_LIVE_SECRET", "S")
    assert _s("dry").credentials() == ("", "")        # paper tak pakai kredensial
    assert _s("live").credentials() == ("K", "S")     # live baca env


def test_getitem_proxies_raw():
    assert _s("dry")["x"] == {"y": 1}


def test_load_settings_rejects_invalid_mode(monkeypatch):
    monkeypatch.setenv("MODE", "bogus")
    with pytest.raises(ValueError):
        load_settings()


def test_load_settings_dry(monkeypatch, tmp_path):
    # Create a temporary .env file without GEMINI_ENABLED
    env_content = ""
    env_file = tmp_path / ".env"
    env_file.write_text(env_content)
    
    monkeypatch.setenv("MODE", "dry")
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    monkeypatch.setenv("GEMINI_API_KEYS", "")
    monkeypatch.chdir(tmp_path)
    
    s = load_settings()
    assert s.mode == "dry" and isinstance(s.raw, dict) and s.gemini_enabled is False
