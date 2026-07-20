"""Glue: ForwardTester risk_filter shadow vs block without network."""
from __future__ import annotations

import types

from bot.forward import ForwardTester
from bot.risk_filter import FilterVerdict


def _bare_ft(**kw):
    ft = ForwardTester.__new__(ForwardTester)
    ft.cfg = {"agent": {}, "signals": {}, "strategy": {}}
    ft.risk_filter_shadow = kw.get("shadow", False)
    ft.risk_filter_block = kw.get("block", False)
    ft._risk_filter_verdict = kw.get("verdict", None)
    return ft


def test_refresh_disabled_clears_verdict(monkeypatch):
    ft = _bare_ft(shadow=False, block=False)
    ft._risk_filter_verdict = FilterVerdict(False, ["breadth_lo"], {})
    # check should not be called when both flags off
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("should not call check when disabled")

    import bot.risk_filter as rf
    monkeypatch.setattr(rf, "check", _boom)
    monkeypatch.setattr(rf, "from_config", lambda cfg: {
        "shadow": False, "block": False,
        "skip_breadth_lo": True, "skip_corr_or_volhi": True,
    })
    ft._refresh_risk_filter()
    assert ft._risk_filter_verdict is None
    assert called["n"] == 0


def test_refresh_shadow_sets_verdict(monkeypatch):
    ft = _bare_ft(shadow=True, block=False)
    v = FilterVerdict(False, ["corr_hi"], {"avg_corr": 0.9})

    import bot.risk_filter as rf
    monkeypatch.setattr(rf, "from_config", lambda cfg: {
        "shadow": True, "block": False,
        "skip_breadth_lo": True, "skip_corr_or_volhi": True,
    })
    monkeypatch.setattr(rf, "check", lambda cfg, **k: v)
    ft._refresh_risk_filter()
    assert ft.risk_filter_shadow is True
    assert ft._risk_filter_verdict is v
    assert ft._risk_filter_verdict.allow is False


def test_refresh_fail_open(monkeypatch):
    ft = _bare_ft(shadow=True)

    import bot.risk_filter as rf
    monkeypatch.setattr(rf, "from_config", lambda cfg: {
        "shadow": True, "block": False,
        "skip_breadth_lo": True, "skip_corr_or_volhi": True,
    })

    def _raise(cfg, **k):
        raise RuntimeError("snap missing")

    monkeypatch.setattr(rf, "check", _raise)
    ft._refresh_risk_filter()
    assert ft._risk_filter_verdict is not None
    assert ft._risk_filter_verdict.allow is True
    assert "error" in (ft._risk_filter_verdict.metrics or {}).get("note", "")


def test_block_flag_requires_deny_verdict():
    """rf_block only when block=True AND allow=False — pure boolean logic used in on_cycle."""
    deny = FilterVerdict(False, ["breadth_lo"], {})
    allow = FilterVerdict(True, [], {})

    def would_block(block_flag, verdict):
        return bool(block_flag and verdict and not verdict.allow)

    assert would_block(True, deny) is True
    assert would_block(True, allow) is False
    assert would_block(False, deny) is False  # shadow only — never hard block
    assert would_block(False, None) is False
