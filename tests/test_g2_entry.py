"""Unit tests for G2 entry overlay (no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from bot import g2_entry as g2


def test_from_config_defaults():
    f = g2.from_config({})
    assert f["shadow"] is False
    assert f["block"] is False
    f2 = g2.from_config({"agent": {"g2_entry": {"shadow": True, "block": False}}})
    assert f2["shadow"] is True
    assert f2["block"] is False


def test_evaluate_with_synthetic_ranks():
    # inject cache
    ranks = pd.Series({"ETH": 0.95, "SOL": 0.10, "BNB": 0.50})
    g2._cache["ranks"] = ranks
    g2._cache["asof"] = pd.Timestamp("2026-01-01")
    g2._cache["n"] = 3
    g2._cache["ts"] = 1e18  # fresh
    cfg = {"agent": {"g2_entry": {"shadow": True, "block": False, "top_q": 0.3}}}

    v_long_top = g2.evaluate("ETH/USDT:USDT", 1, cfg)
    assert v_long_top.bucket == "top"
    assert v_long_top.aligned is True
    assert v_long_top.allow is True  # shadow never blocks

    v_long_bot = g2.evaluate("SOL/USDT:USDT", 1, cfg)
    assert v_long_bot.bucket == "bottom"
    assert v_long_bot.aligned is False
    assert v_long_bot.allow is True  # shadow

    cfg_block = {"agent": {"g2_entry": {"shadow": True, "block": True, "top_q": 0.3}}}
    v_block = g2.evaluate("SOL/USDT:USDT", 1, cfg_block)
    assert v_block.allow is False
    assert "g2_deny" in ",".join(v_block.reasons)

    v_short_bot = g2.evaluate("SOL/USDT:USDT", -1, cfg_block)
    assert v_short_bot.aligned is True
    assert v_short_bot.allow is True

    v_mid = g2.evaluate("BNB/USDT:USDT", 1, cfg)
    assert v_mid.bucket == "mid"
    assert v_mid.aligned is None


def test_stamp():
    v = g2.G2Verdict(True, True, "top", 0.9, ["ok"], {"n": 1})
    s = g2.stamp(v)
    assert s["g2_bucket"] == "top"
    assert s["g2_aligned"] is True
