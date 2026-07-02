"""Devil's Advocate — rem adversarial (adaptasi debat Bull/Bear TradingAgents).
Menantang HANYA aksi ENTER; objection kuat → SKIP; gagal → fail-open (proceed)."""
import pytest

from bot.config import Settings
from bot.react_agent import ReactAgent
from bot.signals import Signal


@pytest.fixture
def agent(cfg, tmp_path):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    a = ReactAgent(s, cfg, log_path=tmp_path / "decision_log.jsonl")
    a.enabled = True            # paksa jalur LLM
    a.devil_enabled = True
    return a


def _sig(side="long"):
    return Signal("BTC/USDC:USDC", side, 0.7, 100.0, 2.0, "r",
                  long_score=0.7 if side == "long" else 0.1,
                  short_score=0.7 if side == "short" else 0.1, regime="trend")


def _reason_enter(monkeypatch, agent):
    monkeypatch.setattr(agent, "reason", lambda state: {
        "action": "ENTER_LONG", "confidence": 0.8, "reasoning": "tren kuat",
        "key_risks": [], "lesson_triggered": ""})


def _devil(monkeypatch, agent, text):
    monkeypatch.setattr(agent.devil_client, "generate",
                        lambda prompt, purpose="": text)


def test_veto_flips_enter_to_skip(agent, monkeypatch):
    _reason_enter(monkeypatch, agent)
    _devil(monkeypatch, agent, '{"strength":0.9,"objections":["lawan arah BTC"],"recommend":"VETO"}')
    d = agent.decide(_sig("long"))
    assert d.action == "SKIP" and not d.permits(_sig("long"))
    assert agent.devil_vetoes == 1 and "lawan arah BTC" in d.key_risks
    assert "DEVIL veto" in d.reasoning


def test_low_strength_proceeds(agent, monkeypatch):
    _reason_enter(monkeypatch, agent)
    _devil(monkeypatch, agent, '{"strength":0.2,"objections":["minor"],"recommend":"PROCEED"}')
    d = agent.decide(_sig("long"))
    assert d.action == "ENTER_LONG" and "devil cleared" in d.reasoning
    assert agent.devil_calls == 1 and agent.devil_vetoes == 0


def test_failopen_on_parse_error(agent, monkeypatch):
    _reason_enter(monkeypatch, agent)
    _devil(monkeypatch, agent, "maaf saya tidak bisa menjawab")   # bukan JSON
    d = agent.decide(_sig("long"))
    assert d.action == "ENTER_LONG"          # kritik gagal → proceed (fail-open)


def test_only_challenges_enter_not_skip(agent, monkeypatch):
    monkeypatch.setattr(agent, "reason", lambda state: {
        "action": "SKIP", "confidence": 0.9, "reasoning": "ragu",
        "key_risks": [], "lesson_triggered": ""})
    called = {"n": 0}
    monkeypatch.setattr(agent.devil_client, "generate",
                        lambda prompt, purpose="": called.__setitem__("n", called["n"] + 1) or "{}")
    d = agent.decide(_sig("long"))
    assert d.action == "SKIP" and called["n"] == 0 and agent.devil_calls == 0


def test_disabled_is_noop(agent, monkeypatch):
    agent.devil_enabled = False
    _reason_enter(monkeypatch, agent)
    _devil(monkeypatch, agent, '{"strength":0.99,"objections":["x"],"recommend":"VETO"}')
    d = agent.decide(_sig("long"))
    assert d.action == "ENTER_LONG" and agent.devil_calls == 0


def test_shadow_records_veto_but_executes_rules(agent, monkeypatch):
    _reason_enter(monkeypatch, agent)
    _devil(monkeypatch, agent, '{"strength":0.9,"objections":["chasing"],"recommend":"VETO"}')
    d = agent.decide(_sig("long"), shadow=True)
    # A/B: eksekusi dipaksa ikut rules (ENTER), tapi verdict agen (SKIP) tercatat
    assert d.action == "ENTER_LONG" and d.react_action == "SKIP"


def test_health_reports_devil_metrics(agent, monkeypatch):
    _reason_enter(monkeypatch, agent)
    _devil(monkeypatch, agent, '{"strength":0.9,"objections":["x"],"recommend":"VETO"}')
    agent.decide(_sig("long"))
    h = agent.health()
    assert h["devil_calls"] == 1 and h["devil_vetoes"] == 1 and h["devil_veto_rate"] == 1.0
