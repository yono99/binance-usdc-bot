"""Point 2 — otonomi portofolio: manage_portfolio (HOLD/REDUCE_RISK/FLAT) + eksekusi."""
import json
import types

import pandas as pd
import pytest

from bot.config import Settings
from bot.forward import ForwardTester
from bot.react_agent import ReactAgent


class _Seq:
    def __init__(self, responses):
        self.responses = list(responses)

    def generate(self, prompt, purpose=""):
        return self.responses.pop(0) if self.responses else None


@pytest.fixture
def agent(cfg, tmp_path):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    return ReactAgent(s, cfg, log_path=tmp_path / "d.jsonl")


def _df(close):
    return pd.DataFrame({"close": [float(close)]})


# ---------- agent: manage_portfolio ----------

def test_manage_portfolio_disabled_holds_and_logs(agent):
    out = agent.manage_portfolio({"count": 1}, daily_pnl_r=-1.0)
    assert out["action"] == "HOLD"
    row = json.loads(agent.log_path.read_text(encoding="utf-8").strip())
    assert row["symbol"] == "*PORTFOLIO*" and row["action"] == "HOLD"


def test_manage_portfolio_flat(agent):
    agent.enabled = True
    agent.client = _Seq(['{"action":"FLAT","reasoning":"regime chaos","confidence":0.8}'])
    out = agent.manage_portfolio({"count": 2})
    assert out["action"] == "FLAT" and "chaos" in out["reasoning"]


def test_sanitize_portfolio_rejects_invalid():
    assert ReactAgent._sanitize_portfolio({"action": "BUY_MORE"})["action"] == "HOLD"
    assert ReactAgent._sanitize_portfolio({"action": "reduce_risk"})["action"] == "REDUCE_RISK"


# ---------- forward: eksekusi ----------

def _bare():
    ft = ForwardTester.__new__(ForwardTester)
    ft.autonomous = True
    ft.use_gemini_trader = False
    ft.live = False
    ft._last_portfolio = 0.0
    ft._autonomous_interval = 0
    ft.balance_usdc = 1000.0
    ft.balance_usdt = 0.0
    ft.risk_frac = 0.01
    ft._day_pnl_usdt = 0.0
    ft._day_pnl_usdc = 0.0
    ft.lessons = types.SimpleNamespace(recent=lambda n: [])
    ft.notify = types.SimpleNamespace(send=lambda m: None)
    return ft


def test_tighten_to_breakeven_only_winners():
    ft = _bare()
    ft.buffers = {"L": _df(110), "S": _df(90), "LOSE": _df(95)}
    ft.open = {"L": {"side": "long", "entry": 100, "sl": 96},     # profit → SL ke 100
               "S": {"side": "short", "entry": 100, "sl": 104},   # profit → SL ke 100
               "LOSE": {"side": "long", "entry": 100, "sl": 96}}   # rugi → tak diubah
    assert ft._tighten_to_breakeven() == 2
    assert ft.open["L"]["sl"] == 100 and ft.open["S"]["sl"] == 100
    assert ft.open["LOSE"]["sl"] == 96


def test_portfolio_review_flat_closes_all():
    ft = _bare()
    ft.open = {"X": {"side": "long", "entry": 100, "sl": 96}}
    ft.buffers = {"X": _df(110)}
    ft._portfolio_view = lambda: {"count": 1}
    ft.react = types.SimpleNamespace(
        manage_portfolio=lambda pf, **k: {"action": "FLAT", "reasoning": "bahaya"})
    closed = []
    ft._close_usd = lambda s, p, r: closed.append((s, r))
    ft._agent_portfolio_review(types.SimpleNamespace(enabled=True))
    assert closed == [("X", "agent_flat")]


def test_portfolio_review_skipped_when_off():
    ft = _bare()
    ft.autonomous = False
    ft.open = {"X": {"side": "long", "entry": 100, "sl": 96}}
    ft.react = types.SimpleNamespace(manage_portfolio=lambda *a, **k: 1 / 0)  # tak boleh dipanggil
    ft._agent_portfolio_review(types.SimpleNamespace(enabled=True))           # no-op, tak meledak
