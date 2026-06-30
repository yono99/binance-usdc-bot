"""ReAct tool-loop: nalar→panggil tool→observasi→aksi final, dgn fallback aman."""
import pytest

from bot.config import Settings
from bot.react_agent import ReactAgent
from bot.signals import Signal


class _SeqClient:
    """Client palsu: kembalikan respons terurut; catat purpose tiap panggilan."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.purposes = []

    def generate(self, prompt, purpose=""):
        self.purposes.append(purpose)
        return self.responses.pop(0) if self.responses else None


@pytest.fixture
def agent(cfg, tmp_path):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    return ReactAgent(s, cfg, log_path=tmp_path / "d.jsonl")


def _sig(side="long"):
    return Signal("BTC/USDC:USDC", side, 0.6, 100.0, 2.0, "r",
                  long_score=0.6, short_score=0.1, regime="trend")


def _tools():
    called = []
    fn = lambda a: called.append(a) or {"count": 0}
    return {"get_portfolio": {"desc": "posisi", "fn": fn}}, called


def test_tool_then_final_action(agent):
    agent.enabled = True
    agent.client = _SeqClient([
        '{"tool":"get_portfolio","args":{}}',
        '{"action":"ENTER_LONG","reasoning":"sehat","confidence":0.7,"key_risks":[],"lesson_triggered":""}',
    ])
    tools, called = _tools()
    d = agent.decide_with_tools(_sig("long"), tools, max_iters=4)
    assert d.action == "ENTER_LONG" and d.source == "LLM_TOOL"
    assert called == [{}]                              # tool benar-benar dipanggil sekali
    assert agent.client.purposes[0] == "react_tool"


def test_unknown_tool_then_decide(agent):
    agent.enabled = True
    agent.client = _SeqClient([
        '{"tool":"does_not_exist","args":{}}',
        '{"action":"SKIP","reasoning":"ragu","confidence":0.8,"key_risks":[],"lesson_triggered":""}',
    ])
    tools, _ = _tools()
    d = agent.decide_with_tools(_sig("long"), tools, max_iters=4)
    assert d.action == "SKIP" and d.source == "LLM_TOOL"   # tool tak dikenal → lanjut, tetap memutuskan


def test_maxiters_falls_back_to_single_shot(agent):
    agent.enabled = True
    # selalu panggil tool, tak pernah aksi → maxiters → fallback decide() (client habis → LLM_UNAVAILABLE)
    agent.client = _SeqClient(['{"tool":"get_portfolio","args":{}}'] * 2)
    tools, _ = _tools()
    d = agent.decide_with_tools(_sig("long"), tools, max_iters=2)
    assert d.source == "LLM_UNAVAILABLE" and d.action == "ENTER_LONG"   # fallback ikut sinyal


def test_disabled_uses_single_shot(agent):
    agent.enabled = False                              # Gemini off
    d = agent.decide_with_tools(_sig("long"), {"x": {"desc": "", "fn": lambda a: {}}})
    assert d.source == "LLM_DISABLED" and d.action == "ENTER_LONG"


def test_no_tools_uses_single_shot(agent):
    agent.enabled = True
    agent.client = _SeqClient([
        '{"action":"SKIP","reasoning":"x","confidence":0.9,"key_risks":[],"lesson_triggered":""}'])
    d = agent.decide_with_tools(_sig("long"), {}, max_iters=3)   # tools kosong → single-shot
    assert d.source == "LLM" and d.action == "SKIP"
