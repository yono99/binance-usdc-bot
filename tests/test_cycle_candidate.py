"""Tests for candidate-edge stance (owner cycle knowledge path)."""
from __future__ import annotations

from bot.cycle_candidate import apply_size, cfg_from, evaluate, should_log, stamp


def test_cfg_defaults():
    f = cfg_from({})
    assert f["mode"] == "off"
    assert f["allow_live"] is False and f["risk_ack"] is False


def test_shadow_long_dump_logs_not_applied():
    cfg = {"agent": {"cycle_candidate": {"mode": "shadow", "long_size_on_dump": 0.5}}}
    v = evaluate(side=1, cfg=cfg, live=False, dump_flag=True, cycle_context={"phase": "uptrend"})
    assert "dump_flag" in v.reasons
    assert v.size_mult == 0.5
    assert v.applied is False
    assert v.skip is False
    assert should_log(v) is True


def test_size_mode_applies_on_dry():
    cfg = {"agent": {"cycle_candidate": {
        "mode": "size", "long_size_on_dump": 0.5, "long_size_on_markdown": 0.7,
    }}}
    v = evaluate(
        side="long", cfg=cfg, live=False, dump_flag=True,
        cycle_context={"phase": "markdown", "unlock": {"in_window": False}},
    )
    assert v.applied is True
    assert abs(v.size_mult - 0.5 * 0.7) < 1e-9
    assert apply_size(1.0, v) == v.size_mult


def test_live_without_ack_not_applied():
    cfg = {"agent": {"cycle_candidate": {
        "mode": "size", "allow_live": False, "risk_ack": False, "long_size_on_dump": 0.5,
    }}}
    v = evaluate(side=1, cfg=cfg, live=True, dump_flag=True, cycle_context={})
    assert v.reasons  # still computed
    assert v.applied is False
    assert v.tags.get("live_blocked_no_ack") is True


def test_live_with_ack_applies():
    cfg = {"agent": {"cycle_candidate": {
        "mode": "size", "allow_live": True, "risk_ack": True, "long_size_on_dump": 0.5,
    }}}
    v = evaluate(side=1, cfg=cfg, live=True, dump_flag=True, cycle_context={})
    assert v.applied is True
    assert v.size_mult == 0.5


def test_soft_block_long_on_dump():
    cfg = {"agent": {"cycle_candidate": {
        "mode": "soft_block", "soft_block_long_on_dump": True,
    }}}
    v = evaluate(side=1, cfg=cfg, live=False, dump_flag=True, cycle_context={})
    assert v.skip is True and v.applied is True


def test_short_passthrough_no_auto_short():
    cfg = {"agent": {"cycle_candidate": {"mode": "size", "long_size_on_dump": 0.5}}}
    v = evaluate(side=-1, cfg=cfg, live=False, dump_flag=True, cycle_context={"phase": "markdown"})
    assert v.size_mult == 1.0
    assert v.skip is False
    assert v.reasons == []


def test_unlock_window_sizes_long():
    cfg = {"agent": {"cycle_candidate": {"mode": "size", "long_size_on_unlock": 0.5}}}
    v = evaluate(
        side=1, cfg=cfg, live=False, dump_flag=False,
        cycle_context={"phase": "uptrend", "unlock": {"in_window": True}},
    )
    assert "unlock_window" in v.reasons
    assert v.size_mult == 0.5


def test_stamp_and_off():
    assert stamp(None) == {}
    v = evaluate(side=1, cfg={"agent": {"cycle_candidate": {"mode": "off"}}}, dump_flag=True)
    assert v.mode == "off" and not should_log(v)
