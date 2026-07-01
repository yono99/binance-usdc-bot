"""A/B harness: analisis rules vs rules+ReAct + perekaman verdict mode shadow."""
import pytest

from bot import ab
from bot import decision_log as dl
from bot.config import Settings
from bot.react_agent import ReactAgent
from bot.signals import Signal


# ---------- analisis (pure) ----------

def _rows(pairs):
    # pairs: list of (react_action, outcome_r)
    return [{"action": "ENTER_LONG", "outcome_r": r, "react_action": ra} for ra, r in pairs]


def test_analyze_react_adds_value():
    # ReAct setuju (ENTER) pada winner, tolak (SKIP) pada loser → bernilai.
    rows = _rows([("ENTER_LONG", 1.0)] * 12 + [("SKIP", -1.0)] * 12)
    out = ab.analyze(rows)
    assert out["verdict"] == "REACT_ADDS_VALUE" and out["significant"] is True
    assert out["exp_r_rules_react"] > out["exp_r_rules"] and out["p_value"] < 0.05


def test_analyze_not_proven_on_noise():
    rows = _rows([("ENTER_LONG", 1.0), ("SKIP", 1.0), ("ENTER_LONG", -1.0), ("SKIP", -1.0)] * 6)
    out = ab.analyze(rows)
    assert out["verdict"] == "NOT_PROVEN" and out["significant"] is False


def test_analyze_no_data():
    assert ab.analyze([])["verdict"] == "NO_DATA"


# ---------- metrik risiko (Jalan A) ----------

def test_risk_stats_drawdown():
    r = ab._risk_stats([1.0, -1.0, 1.0, -1.0])   # cum 1,0,1,0 → dd=1
    assert r["max_drawdown_r"] == 1.0 and r["worst_r"] == -1.0 and r["n"] == 4


def test_risk_stats_empty():
    assert ab._risk_stats([])["max_drawdown_r"] is None


def test_analyze_reports_risk_reduction():
    # ReAct setuju winner, tolak loser → drawdown subset agent < drawdown rules-saja
    rows = _rows([("ENTER_LONG", 1.0)] * 10 + [("SKIP", -1.0)] * 10)
    out = ab.analyze(rows)
    assert out["risk_rules"]["max_drawdown_r"] > 0          # rules-saja punya drawdown
    assert out["risk_react"]["max_drawdown_r"] == 0.0       # subset agent (winner) tanpa drawdown
    assert out["reduces_risk"] is True


def test_analyze_insufficient_when_one_arm_empty():
    out = ab.analyze(_rows([("ENTER_LONG", 1.0)] * 5))     # tak ada yang ditolak
    assert out["verdict"] == "INSUFFICIENT"


def test_collect_filters(tmp_path):
    p = tmp_path / "d.jsonl"
    dl.append({"action": "ENTER_LONG", "outcome_r": 1.0, "react_action": "ENTER_LONG"}, path=p)
    dl.append({"action": "ENTER_LONG", "outcome_r": None, "react_action": "SKIP"}, path=p)   # blm tutup
    dl.append({"action": "SKIP", "outcome_r": -1.0, "react_action": "SKIP"}, path=p)         # bkn ENTER
    dl.append({"action": "ENTER_LONG", "outcome_r": -1.0}, path=p)                            # tak shadow
    got = ab.collect(p)
    assert len(got) == 1 and got[0]["react_action"] == "ENTER_LONG"


# ---------- perekaman verdict shadow ----------

@pytest.fixture
def agent(cfg, tmp_path):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    return ReactAgent(s, cfg, log_path=tmp_path / "d.jsonl")


def _sig(side="long"):
    return Signal("BTC/USDC:USDC", side, 0.6, 100.0, 2.0, "r",
                  long_score=0.6, short_score=0.1, regime="trend")


def test_shadow_forces_execute_but_records_verdict(agent, monkeypatch):
    agent.enabled = True
    monkeypatch.setattr(agent, "reason",
                        lambda s: {"action": "SKIP", "confidence": 0.9, "reasoning": "chaos",
                                   "key_risks": [], "lesson_triggered": ""})
    d = agent.decide(_sig("long"), shadow=True)
    assert d.action == "ENTER_LONG"          # eksekusi dipaksa ikut rules
    assert d.permits(_sig("long")) is True   # → tidak memblokir
    assert d.react_action == "SKIP"          # verdict asli ReAct tercatat utk A/B
    row = __import__("json").loads(agent.log_path.read_text(encoding="utf-8").strip())
    assert row["react_action"] == "SKIP" and row["action"] == "ENTER_LONG"


def test_non_shadow_has_empty_react_action(agent, monkeypatch):
    agent.enabled = True
    monkeypatch.setattr(agent, "reason",
                        lambda s: {"action": "ENTER_LONG", "confidence": 0.8, "reasoning": "ok",
                                   "key_risks": [], "lesson_triggered": ""})
    d = agent.decide(_sig("long"), shadow=False)
    assert d.react_action == "" and d.action == "ENTER_LONG"
