"""Phase 1 — ReactAgent: gerbang entry dgn fallback deterministik & decision log.
HARD CONSTRAINT diuji: kegagalan/ketiadaan LLM TAK PERNAH memblokir trading."""
import json

import pytest

from bot.config import Settings
from bot.react_agent import ACTIONS, Decision, ReactAgent
from bot.signals import Signal


@pytest.fixture
def agent(cfg, tmp_path):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    return ReactAgent(s, cfg, log_path=tmp_path / "decision_log.jsonl")


def _sig(side="long", conf=0.7):
    return Signal("BTC/USDC:USDC", side, conf, 100.0, 2.0, "r",
                  long_score=0.7 if side == "long" else 0.1,
                  short_score=0.7 if side == "short" else 0.1, regime="trend")


# ---------- permits: arah & aksi ----------

def test_permits_direction_logic():
    base = dict(id="x", ts="t", symbol="S", reasoning="", confidence=0.5)
    assert Decision(action="ENTER_LONG", **base).permits(_sig("long")) is True
    assert Decision(action="ENTER_SHORT", **base).permits(_sig("long")) is False  # arah beda
    assert Decision(action="SKIP", **base).permits(_sig("long")) is False
    assert Decision(action="FLAT", **base).permits(_sig("short")) is False
    assert Decision(action="ENTER_SHORT", **base).permits(_sig("short")) is True


# ---------- fallback: LLM nonaktif TIDAK boleh memblokir entry ----------

def test_disabled_llm_falls_back_to_signal(agent):
    d = agent.decide(_sig("long"), n_positions=0, max_positions=2)
    assert d.source == "LLM_DISABLED"
    assert d.action == "ENTER_LONG" and d.permits(_sig("long"))   # tetap boleh entry


def test_unavailable_llm_logs_and_falls_back(agent, monkeypatch):
    agent.enabled = True                       # paksa jalur LLM
    monkeypatch.setattr(agent, "reason", lambda state: None)   # simulasi Gemini timeout
    d = agent.decide(_sig("short"))
    assert d.source == "LLM_UNAVAILABLE" and d.action == "ENTER_SHORT"


# ---------- low-confidence SKIP → veto deterministik lama ----------

def test_low_conf_skip_defers_to_veto(agent, monkeypatch):
    agent.enabled = True
    monkeypatch.setattr(agent, "reason",
                        lambda state: {"action": "SKIP", "confidence": 0.1,
                                       "reasoning": "ragu", "key_risks": [], "lesson_triggered": ""})
    monkeypatch.setattr(agent, "_veto_allows", lambda sig: True)   # veto lama izinkan
    d = agent.decide(_sig("long"))
    assert d.source == "VETO_FALLBACK" and d.action == "ENTER_LONG"


def test_high_conf_skip_is_respected(agent, monkeypatch):
    agent.enabled = True
    monkeypatch.setattr(agent, "reason",
                        lambda state: {"action": "SKIP", "confidence": 0.9,
                                       "reasoning": "regime chaos", "key_risks": [], "lesson_triggered": ""})
    d = agent.decide(_sig("long"))
    assert d.source == "LLM" and d.action == "SKIP" and not d.permits(_sig("long"))


# ---------- sanitize ----------

def test_sanitize_rejects_bad_action(agent):
    assert agent._sanitize({"action": "MOON"}) is None
    assert agent._sanitize({"action": "enter_long", "confidence": 2}) == {
        "action": "ENTER_LONG", "confidence": 1.0, "reasoning": "",
        "key_risks": [], "lesson_triggered": ""}


# ---------- decision log ----------

def test_decision_written_to_log(agent):
    agent.decide(_sig("long"))
    rows = agent.log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    for k in ("ts", "id", "symbol", "action", "reasoning", "confidence", "key_risks",
              "lesson_triggered", "source", "signal_scores", "market_state",
              "outcome", "outcome_r", "filled_at_close"):
        assert k in row
    assert row["outcome"] is None and row["filled_at_close"] is False
    assert row["signal_scores"]["long"] == 0.7


def test_health_tracks_fallback_rate(agent):
    agent.decide(_sig("long"))                 # LLM nonaktif → fallback
    h = agent.health()
    assert h["fallbacks"] == 1 and h["fallback_rate"] == 1.0 and h["enabled"] is False


def test_actions_constant_complete():
    assert set(ACTIONS) == {"ENTER_LONG", "ENTER_SHORT", "SKIP", "REDUCE_RISK", "FLAT"}
