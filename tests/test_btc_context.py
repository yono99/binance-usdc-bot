"""Dominansi BTC diajarkan ke agent: tool get_btc_context, OBSERVE btc_lead, prompt note."""
import types

import pandas as pd

from bot.config import Settings
from bot.forward import ForwardTester
from bot.react_agent import ReactAgent
from bot.signals import Signal
from bot.tools import ToolContext, build_tools


def _df(closes):
    return pd.DataFrame({"close": [float(x) for x in closes]})


# ---------- tool ----------

def test_get_btc_context_from_buffer():
    ctx = ToolContext(buffers={"BTC/USDC:USDC": _df([100, 101, 102, 103, 104])})
    out = build_tools(ctx)["get_btc_context"]["fn"]({})
    assert out["dir"] == 1 and out["ret_1bar_pct"] > 0 and out["ret_3bar_pct"] > 0


def test_get_btc_context_fetch_fallback():
    ex = types.SimpleNamespace(ohlcv=lambda s, tf, limit=5: _df([110, 108, 106, 104, 102]))
    ctx = ToolContext(ex=ex, buffers={}, cfg={"market": {"timeframe": "15m"}})
    out = build_tools(ctx)["get_btc_context"]["fn"]({})
    assert out["dir"] == -1                       # BTC turun → arah negatif


# ---------- OBSERVE + prompt ----------

def test_observe_includes_btc_lead(cfg):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    agent = ReactAgent(s, cfg)
    sig = Signal("SOL/USDC:USDC", "long", 0.6, 100.0, 2.0, "r")
    state = agent.observe(sig, btc_lead={"ret_1bar_pct": -1.5, "dir": -1})
    assert state["btc_lead"] == {"ret_1bar_pct": -1.5, "dir": -1}


def test_prompt_teaches_btc_dominance(cfg):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    agent = ReactAgent(s, cfg)
    state = agent.observe(Signal("SOL/USDC:USDC", "long", 0.6, 100.0, 2.0, "r"),
                          btc_lead={"ret_1bar_pct": -1.5, "dir": -1})
    p = ReactAgent._prompt(state)
    assert "DOMINANSI BTC" in p and "beta" in p.lower() and "BTC leader" in p


# ---------- forward helper ----------

def test_forward_btc_lead():
    ft = ForwardTester.__new__(ForwardTester)
    ft.buffers = {"BTC/USDC:USDC": _df([100, 102, 101, 103, 100])}   # bar tertutup terakhir = idx -2
    out = ft._btc_lead()
    assert "ret_1bar_pct" in out and "dir" in out


def test_forward_btc_lead_insufficient():
    ft = ForwardTester.__new__(ForwardTester)
    ft.buffers = {}
    assert ft._btc_lead() == {}
