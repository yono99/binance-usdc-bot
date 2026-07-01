"""Memori lintas-tick: AgentMemory + integrasi ke ReactAgent (observe/decide/tool-loop)."""
import time

import pytest

from bot.config import Settings
from bot.memory import AgentMemory
from bot.react_agent import ReactAgent
from bot.signals import Signal


# ---------- AgentMemory (unit) ----------

def test_remember_recall_filters():
    m = AgentMemory()
    m.remember("tool", "BTC", {"a": 1})
    m.remember("decision", "BTC", {"action": "SKIP"})
    m.remember("tool", "ETH", {"b": 2})
    assert len(m.recall(symbol="BTC")) == 2
    assert len(m.recall(symbol="BTC", kind="tool")) == 1
    assert m.recall(symbol="BTC")[0]["kind"] == "decision"     # terbaru dulu


def test_recall_respects_max_age():
    m = AgentMemory(max_age_s=100)
    m.notes.append({"ts": time.time() - 9999, "kind": "tool", "symbol": "BTC", "data": {}})
    assert m.recall(symbol="BTC") == []                        # kadaluarsa → tak diingat


def test_maxlen_bounded():
    m = AgentMemory(maxlen=3)
    for i in range(5):
        m.remember("tool", "BTC", {"i": i})
    assert len(m.notes) == 3 and m.recall(symbol="BTC")[0]["data"]["i"] == 4


def test_snapshot_restore_roundtrip():
    m = AgentMemory()
    m.remember("decision", "BTC", {"action": "ENTER_LONG"})
    m2 = AgentMemory()
    m2.restore(m.snapshot())
    assert m2.recall(symbol="BTC")[0]["data"]["action"] == "ENTER_LONG"


def test_summary_shape():
    m = AgentMemory()
    m.remember("tool", "BTC", {"imbalance": 0.4})
    s = m.summary("BTC")
    assert s and set(s[0]) == {"age_s", "kind", "data"}


# ---------- integrasi ReactAgent ----------

@pytest.fixture
def agent(cfg, tmp_path):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    return ReactAgent(s, cfg, log_path=tmp_path / "d.jsonl")


def _sig(side="long"):
    return Signal("BTC/USDC:USDC", side, 0.6, 100.0, 2.0, "r",
                  long_score=0.6, short_score=0.1, regime="trend")


def test_observe_injects_recent_memory(agent):
    m = AgentMemory()
    m.remember("tool", "BTC/USDC:USDC", {"imbalance": 0.4})
    state = agent.observe(_sig("long"), memory=m)
    assert state["recent_memory"] and state["recent_memory"][0]["data"] == {"imbalance": 0.4}


def test_decide_records_decision_to_memory(agent):
    m = AgentMemory()
    agent.decide(_sig("long"), memory=m)                       # LLM off → fallback ENTER
    notes = m.recall(symbol="BTC/USDC:USDC", kind="decision")
    assert notes and notes[0]["data"]["action"] == "ENTER_LONG"


class _Seq:
    def __init__(self, r):
        self.r = list(r)

    def generate(self, prompt, purpose=""):
        return self.r.pop(0) if self.r else None


def test_tool_loop_records_tool_and_decision(agent):
    agent.enabled = True
    agent.client = _Seq(['{"tool":"get_x","args":{}}',
                         '{"action":"SKIP","reasoning":"ok","confidence":0.8,"key_risks":[],"lesson_triggered":""}'])
    tools = {"get_x": {"desc": "x", "fn": lambda a: {"v": 1}}}
    m = AgentMemory()
    agent.decide_with_tools(_sig("long"), tools, max_iters=4, memory=m)
    assert m.recall(symbol="BTC/USDC:USDC", kind="tool")       # observasi tool diingat
    assert m.recall(symbol="BTC/USDC:USDC", kind="decision")   # keputusan diingat
