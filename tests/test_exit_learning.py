"""Gemini BELAJAR dari cara-keluar (SL/CL/TP/gemini_exit): exit_stats() agregat per exit_reason
(dihitung KODE), disuntik ke konteks + diajarkan di kurikulum. Tanpa jaringan."""
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
    return GeminiTrader(Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False), cfg)


def _df(n=120, seed=0):
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + rng.normal(0, 1, n).cumsum(), index=idx)
    return pd.DataFrame({"open": close, "high": close * 1.002, "low": close * 0.998,
                         "close": close, "volume": rng.uniform(1, 5, n)}, index=idx)


def test_exit_stats_kosong_aman(db):
    assert store.exit_stats() == []


def test_exit_stats_agregasi_per_reason(db):
    for r, reason in [(1.0, "tp"), (0.8, "tp"), (-0.7, "gemini_exit"),
                      (-0.5, "gemini_exit"), (-1.0, "sl")]:
        i = db.record_decision("ETH/USDC:USDC", "trend_pullback", "long", 0.6, "", {})
        db.settle_decision(i, r, exit_reason=reason)
    stats = {s["reason"]: s for s in store.exit_stats()}
    assert stats["tp"]["n"] == 2 and stats["tp"]["exp_r"] > 0
    assert stats["gemini_exit"]["n"] == 2 and stats["gemini_exit"]["exp_r"] < 0
    assert stats["sl"]["n"] == 1
    rows = store.exit_stats()                       # diurut exp_r NAIK → paling -EV duluan
    assert rows[0]["exp_r"] <= rows[-1]["exp_r"]


def test_exit_track_record_masuk_konteks(db, trader):
    i = db.record_decision("ETH/USDC:USDC", "trend_pullback", "long", 0.6, "", {})
    db.settle_decision(i, -0.7, exit_reason="gemini_exit")
    ctx = trader.build_context("ETH/USDC:USDC", _df())
    rec = {s["reason"]: s for s in ctx["exit_track_record"]}
    assert "gemini_exit" in rec and rec["gemini_exit"]["exp_r"] < 0


def test_kurikulum_ajarkan_exit_track_record():
    p = curriculum_prompt(modules=DECISION_MODULES)
    assert "exit_track_record" in p and "gemini_exit" in p
