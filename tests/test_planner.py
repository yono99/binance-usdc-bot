"""Planner tipis: rencana sesi HANYA mengetatkan; fail-safe netral; enforce deterministik."""
import types

import pytest

from bot import forward as fwd
from bot.config import Settings
from bot.planner import SessionPlanner, default_plan


# ---------- default & sanitize (clamp ≤ batas manusia) ----------

def test_default_plan_is_neutral():
    p = default_plan(20)
    assert p["stance"] == "normal" and p["bias"] == "neutral"
    assert p["max_new_trades"] == 20 and p["max_exposure_frac"] == 1.0


def test_sanitize_clamps_to_hard_cap():
    p = SessionPlanner.sanitize({"stance": "aggressive", "bias": "long",
                                 "max_new_trades": 999, "max_exposure_frac": 5.0}, hard_max_trades=10)
    assert p["max_new_trades"] == 10            # tak boleh > cap manusia
    assert p["max_exposure_frac"] == 1.0        # clamp ke 1


def test_sanitize_rejects_invalid_enum():
    p = SessionPlanner.sanitize({"stance": "yolo", "bias": "sideways"}, hard_max_trades=5)
    assert p["stance"] == "normal" and p["bias"] == "neutral"


def test_floor_prevents_choking_small_account():
    # Planner terlalu defensif (0 trade, eksposur 5%) → lantai jaga akun modal-minim tetap bisa trade.
    p = SessionPlanner.sanitize({"stance": "defensive", "max_new_trades": 0,
                                 "max_exposure_frac": 0.05}, hard_max_trades=10)
    assert p["max_new_trades"] >= 1 and p["max_exposure_frac"] >= 0.5


def test_risk_off_can_stop_fully():
    # risk_off = cara berhenti eksplisit → TIDAK di-lantai (boleh 0 trade).
    p = SessionPlanner.sanitize({"stance": "risk_off", "max_new_trades": 0,
                                 "max_exposure_frac": 0.0}, hard_max_trades=10)
    assert p["max_new_trades"] == 0


# ---------- enforce (hanya mengetatkan) ----------

def _plan(**kw):
    base = {"stance": "normal", "bias": "neutral", "max_new_trades": 10, "max_exposure_frac": 1.0}
    return {**base, **kw}


def test_enforce_risk_off_blocks_all():
    assert SessionPlanner.enforce(_plan(stance="risk_off"), "long",
                                  new_trades=0, exposure_frac=0.0) is not None


def test_enforce_bias_blocks_opposite():
    assert SessionPlanner.enforce(_plan(bias="long"), "short", new_trades=0, exposure_frac=0) is not None
    assert SessionPlanner.enforce(_plan(bias="long"), "long", new_trades=0, exposure_frac=0) is None
    assert SessionPlanner.enforce(_plan(bias="short"), "long", new_trades=0, exposure_frac=0) is not None


def test_enforce_trade_quota():
    assert SessionPlanner.enforce(_plan(max_new_trades=2), "long", new_trades=2, exposure_frac=0) is not None
    assert SessionPlanner.enforce(_plan(max_new_trades=2), "long", new_trades=1, exposure_frac=0) is None


def test_enforce_exposure_quota():
    assert SessionPlanner.enforce(_plan(max_exposure_frac=0.5), "long",
                                  new_trades=0, exposure_frac=0.5) is not None
    assert SessionPlanner.enforce(_plan(max_exposure_frac=0.5), "long",
                                  new_trades=0, exposure_frac=0.3) is None


def test_enforce_neutral_allows():
    assert SessionPlanner.enforce(_plan(), "long", new_trades=0, exposure_frac=0.2) is None


# ---------- make_plan fail-safe ----------

@pytest.fixture
def planner(cfg):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    return SessionPlanner(s, cfg)


def test_make_plan_disabled_returns_default(planner):
    p = planner.make_plan({"x": 1}, hard_max_trades=7)
    assert p["stance"] == "normal" and p["bias"] == "neutral" and p["max_new_trades"] == 7


def test_make_plan_uses_llm_when_enabled(planner):
    planner.enabled = True
    planner.client = types.SimpleNamespace(
        generate=lambda prompt, purpose="": '{"stance":"defensive","bias":"short",'
                                            '"max_new_trades":3,"max_exposure_frac":0.4,"reasoning":"drawdown"}')
    p = planner.make_plan({"day_pnl_usd": -50}, hard_max_trades=10)
    assert p["stance"] == "defensive" and p["bias"] == "short" and p["max_new_trades"] == 3


# ---------- integrasi forward ----------

def test_exposure_frac():
    ft = fwd.ForwardTester.__new__(fwd.ForwardTester)
    ft.balance_usdc = 100.0
    ft.balance_usdt = 0.0
    ft.open = {"A": {"bet": 10}, "B": {"bet": 15}}
    assert ft._exposure_frac() == 0.25


def test_refresh_plan_sets_and_resets(monkeypatch):
    ft = fwd.ForwardTester.__new__(fwd.ForwardTester)
    ft.use_planner = True
    ft.daily_max_trades = 5
    ft.balance_usdc = 1000.0
    ft.balance_usdt = 0.0
    ft._day_pnl_usdt = 0.0
    ft._day_pnl_usdc = 0.0
    ft._last_news_note = ""
    ft._plan_horizon_h = 6
    ft._plan_day = None
    ft._last_plan_ts = 0.0
    ft._session_trades = 3
    ft.open = {}
    ft.lessons = types.SimpleNamespace(recent=lambda n: [])
    ft._portfolio_view = lambda: {"count": 0}
    ft.planner = types.SimpleNamespace(enabled=False,
                                       make_plan=lambda ctx, hard_max_trades: default_plan(hard_max_trades))
    monkeypatch.setattr(fwd.decision_log, "append", lambda *a, **k: None)
    ft._refresh_plan(types.SimpleNamespace(enabled=True, bet_usd=2.0, leverage=100))
    assert ft._session_trades == 0 and ft._session_plan["max_new_trades"] == 5
