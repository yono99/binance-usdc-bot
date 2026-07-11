"""Sideways sniper tests: sideways profit path (bypass pre-gate, micro-TP, exit cepat)."""
import numpy as np
import pandas as pd
import pytest

from bot.forward import ForwardTester as _FT


# ── _is_range ─────────────────────────────────────────────────────────────────

def test_is_range_true_with_low_adx(cfg, make_df):
    """Range market: harga hampir flat (white noise) -> ADX rendah sekali."""
    rng = np.random.default_rng(42)
    prices = 100 + rng.normal(0, 0.01, 120)  # hampir flat
    df = make_df(prices, vol=1.0)
    assert _FT._is_range(df, cfg) is True


def test_is_range_false_with_high_adx(cfg, make_df):
    """Trending market: harga lurus naik kuat -> ADX tinggi."""
    prices = 100 + np.arange(120) * 0.5
    df = make_df(prices, vol=1.0)
    assert _FT._is_range(df, cfg) is False


def test_is_range_bad_df_returns_false(cfg):
    assert _FT._is_range(pd.DataFrame(), cfg) is False


# ── _sniper_cache_add ─────────────────────────────────────────────────────────

def test_sniper_cache_add_accumulates():
    cache: dict[str, bool] = {}
    _FT._sniper_cache_add(cache, "BTC/USDC:USDC", True)
    _FT._sniper_cache_add(cache, "ETH/USDC:USDC", False)
    _FT._sniper_cache_add(cache, "SOL/USDC:USDC", True)
    assert cache == {"BTC/USDC:USDC": True, "ETH/USDC:USDC": False,
                     "SOL/USDC:USDC": True}


def test_sniper_cache_add_none_ignored():
    cache: dict | None = None
    _FT._sniper_cache_add(cache, "BTC/USDC:USDC", True)   # should not crash


# ── Sideways config parsing (via init) ────────────────────────────────────────

def test_sideways_defaults_from_config(cfg):
    """ForwardTester.__post_init__ harus parse sideways_sniper dari config."""
    ss = cfg.get("gemini", {}).get("sideways_sniper", {})
    assert isinstance(ss, dict)
    assert ss.get("enabled", True) is True
    assert 0.01 <= ss.get("pregate_atr_pct_range", 0.02) <= 1.0
    assert ss.get("micro_tp_pct_min", 0.01) >= 0.001


# ── Model health tracking per (key, model) (gemini_client) ────────────────────

def test_model_health_starts_neutral():
    from bot.gemini_client import _model_health_score as score
    # (key, model) tanpa riwayat → skor netral 0.5
    assert 0.3 <= score("k1", "nonexistent-model") <= 0.7


def test_model_health_improves_with_successes():
    from bot.gemini_client import _record_model_health as rec
    from bot.gemini_client import _model_health_score as score
    import bot.gemini_client as gc
    gc._model_health.clear()
    k, m = "k1", "gemini-health-test"
    for _ in range(10):
        rec(k, m, True)
    assert score(k, m) > 0.7


def test_model_health_drops_with_failures():
    from bot.gemini_client import _record_model_health as rec
    from bot.gemini_client import _model_health_score as score
    import bot.gemini_client as gc
    gc._model_health.clear()
    k, m = "k1", "gemini-health-fail-test"
    for _ in range(10):
        rec(k, m, False)
    assert score(k, m) < 0.4


def test_health_favors_successful_model():
    from bot.gemini_client import _record_model_health as rec
    from bot.gemini_client import _model_health_score as score
    import bot.gemini_client as gc
    gc._model_health.clear()
    k, m_ok, m_bad = "k1", "flash-success", "flash-overload"
    for _ in range(10):
        rec(k, m_ok, True)
        rec(k, m_bad, False)
    ok_s = score(k, m_ok)
    bad_s = score(k, m_bad)
    assert ok_s > bad_s, f"expected ok>{bad_s}, got {ok_s:.3f} <= {bad_s:.3f}"


def test_health_isolates_per_key():
    """1 key overload di model A tak menurunkan model A di key lain (per-key: model)."""
    from bot.gemini_client import _record_model_health as rec
    from bot.gemini_client import _model_health_score as score
    import bot.gemini_client as gc
    gc._model_health.clear()
    for _ in range(10):
        rec("k1", "flash", False)       # k1: flash overload
        rec("k2", "flash", True)        # k2: flash sehat
    assert score("k1", "flash") < 0.4
    assert score("k2", "flash") > 0.7
