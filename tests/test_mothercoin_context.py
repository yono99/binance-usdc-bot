"""(a) MOTHERCOIN: btc_lead disuntik ke konteks keputusan Gemini + diajarkan di kurikulum.
Tanpa jaringan (gemini_enabled=False). Pakai fixture cfg dari conftest, pola sama
dengan test_gemini_trader.py."""
import numpy as np
import pandas as pd
import pytest

from bot import store
from bot.config import Settings
from bot.gemini_trader import GeminiTrader
from bot.trader_curriculum import curriculum_prompt, DECISION_MODULES


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init_db()
    return store


@pytest.fixture
def trader(cfg):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    return GeminiTrader(s, cfg)


def _df(n=120, seed=0):
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + rng.normal(0, 1, n).cumsum(), index=idx)
    return pd.DataFrame({"open": close, "high": close * 1.002, "low": close * 0.998,
                         "close": close, "volume": rng.uniform(1, 5, n)}, index=idx)


def test_btc_lead_masuk_konteks(db, trader):
    lead = {"ret_1bar_pct": -2.3, "ret_3bar_pct": -5.1, "dir": -1}
    ctx = trader.build_context("ETH/USDC:USDC", _df(), btc_lead=lead)
    assert ctx["btc_lead"] == lead


def test_btc_lead_default_kosong(db, trader):
    ctx = trader.build_context("ETH/USDC:USDC", _df())
    assert ctx["btc_lead"] == {}


def test_kurikulum_ajarkan_mothercoin():
    # aturan mothercoin harus ADA dan modulnya benar-benar disuntik ke prompt keputusan
    assert "market_structure" in DECISION_MODULES
    p = curriculum_prompt(modules=DECISION_MODULES)
    assert "btc_lead" in p and "MOTHERCOIN" in p
