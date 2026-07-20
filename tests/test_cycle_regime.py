"""P2/P3 — cycle_regime labels + inject context (stance only, no hard gate)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bot.cycle_regime import (
    build_cycle_context,
    calendar_halving_phase,
    dominance_regime,
    load_unlock_calendar,
    measured_cycle_phase,
    unlock_window_for,
    years_since_halving,
)
from bot.config import Settings
from bot.react_agent import ReactAgent
from bot.signals import Signal


def _close(n: int = 400, end: float = 50_000.0, start: float = 60_000.0) -> pd.Series:
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    # gradual decline → deep DD under falling MA → markdown-ish
    px = np.linspace(start, end, n)
    return pd.Series(px, index=idx)


def test_calendar_halving_phase_known():
    # ~2.2y after 2024-04-19 → bear band
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    assert calendar_halving_phase(now) == "bear"
    y = years_since_halving(now)
    assert y is not None and 2.0 <= y < 3.0


def test_measured_phase_markdown_deep_dd():
    s = _close(400, end=40_000, start=90_000)
    m = measured_cycle_phase(s)
    assert m["phase"] in ("markdown", "accumulation")
    assert m["dd_from_ath"] < -0.3
    assert "calendar_phase" in m


def test_measured_phase_uptrend():
    idx = pd.date_range("2023-01-01", periods=400, freq="D", tz="UTC")
    px = np.linspace(20_000, 80_000, 400)
    s = pd.Series(px, index=idx)
    m = measured_cycle_phase(s)
    assert m["phase"] in ("uptrend", "distribution")
    assert m["dd_from_ath"] > -0.15


def test_measured_phase_thin_data():
    s = _close(50)
    m = measured_cycle_phase(s)
    assert m["phase"] == "unknown"


def test_dominance_regimes():
    idx = pd.date_range("2024-01-01", periods=40, freq="D", tz="UTC")
    # BTCDOM up hard → risk_off
    dom_up = pd.Series(np.linspace(100, 110, 40), index=idx)
    btc_flat = pd.Series(np.full(40, 50_000.0), index=idx)
    r = dominance_regime(dom_up, btc_flat, lookback=20)
    assert r["regime"] == "risk_off"

    dom_dn = pd.Series(np.linspace(110, 100, 40), index=idx)
    r2 = dominance_regime(dom_dn, btc_flat, lookback=20)
    assert r2["regime"] in ("alt_season", "alt_bid")


def test_unlock_window_and_calendar(tmp_path: Path):
    p = tmp_path / "u.csv"
    p.write_text(
        "symbol,unlock_date,pct_supply,note\n"
        "APT,2025-05-01,2.5,test\n",
        encoding="utf-8",
    )
    cal = load_unlock_calendar(p)
    assert len(cal) == 1
    hit = unlock_window_for("APT/USDT:USDT", pd.Timestamp("2025-05-02", tz="UTC"), cal)
    assert hit["in_window"] is True
    miss = unlock_window_for("APT/USDT:USDT", pd.Timestamp("2025-06-01", tz="UTC"), cal)
    assert miss["in_window"] is False
    none = unlock_window_for("XYZ/USDT:USDT", pd.Timestamp("2025-05-01", tz="UTC"), cal)
    assert none["in_window"] is False


def test_build_cycle_context_fail_soft():
    ctx = build_cycle_context(None, None)
    assert ctx["phase"] == "unknown"
    assert "dominance" in ctx and "unlock" in ctx

    s = _close()
    ctx2 = build_cycle_context(s, None, symbol="SOL/USDT:USDT")
    assert ctx2["phase"] != "unknown" or "phase_error" in ctx2
    assert ctx2["unlock"]["in_window"] is False


def test_react_observe_and_prompt_include_cycle(cfg):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    agent = ReactAgent(s, cfg)
    cyc = {
        "phase": "markdown",
        "calendar_phase": "bear",
        "dominance": {"regime": "neutral"},
        "unlock": {"in_window": False},
    }
    state = agent.observe(
        Signal("SOL/USDC:USDC", "long", 0.6, 100.0, 2.0, "r"),
        cycle_context=cyc,
    )
    assert state["cycle_context"]["phase"] == "markdown"
    p = ReactAgent._prompt(state)
    assert "CYCLE CONTEXT" in p
    assert "stance only" in p.lower() or "NOT auto" in p
    assert "markdown" in p
