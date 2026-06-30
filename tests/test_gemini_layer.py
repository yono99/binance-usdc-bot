"""GeminiLayer — veto regime. Saat Gemini nonaktif: izinkan semua (fail-open)."""
import pytest

from bot.config import Settings
from bot.gemini_layer import GeminiLayer


def _layer(cfg, enabled=False):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=enabled)
    return GeminiLayer(s, cfg)


def test_disabled_allows_everything(cfg):
    layer = _layer(cfg, enabled=False)
    assert layer.enabled is False
    assert layer.allows("BTC/USDC:USDC", {"price": 100, "atr": 1}) is True


def test_disabled_regime_score_is_one(cfg):
    layer = _layer(cfg, enabled=False)
    assert layer.regime_score("BTC/USDC:USDC", {"price": 100}) == 1.0


def test_allows_true_when_role_not_veto(cfg):
    # Salin cfg, set role != veto → allows selalu True walau (hipotetis) enabled.
    raw = dict(cfg)
    raw["gemini"] = {**cfg["gemini"], "role": "confirm"}
    layer = _layer(raw, enabled=False)
    assert layer.allows("ETH/USDC:USDC", {"price": 50}) is True
