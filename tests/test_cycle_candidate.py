"""Tests for candidate-edge stance (owner cycle knowledge path)."""
from __future__ import annotations

import json
from pathlib import Path

from bot.cycle_candidate import (
    apply_size,
    cfg_from,
    evaluate,
    live_enforce_ok,
    load_live_state,
    record_live_close_r,
    reset_live_stop,
    should_log,
    stamp,
)


def test_cfg_defaults():
    f = cfg_from({})
    assert f["mode"] == "off"
    assert f["allow_live"] is False and f["risk_ack"] is False
    assert f["stop_loss_r_live"] == -5.0


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
        "stop_loss_r_live": -99,
    }}}
    v = evaluate(
        side=1, cfg=cfg, live=True, dump_flag=True, cycle_context={},
        live_state={"cum_r": 0.0, "stopped": False},
    )
    assert v.applied is True
    assert v.size_mult == 0.5


def test_live_stop_rule_blocks_enforce(tmp_path: Path):
    cfg = {"agent": {"cycle_candidate": {
        "mode": "size", "allow_live": True, "risk_ack": True,
        "stop_loss_r_live": -2.0, "long_size_on_dump": 0.5,
    }}}
    state_path = tmp_path / "ce_live.json"
    # Drive cum_r under stop
    st = record_live_close_r(-1.2, cfg, symbol="A", path=state_path)
    st = record_live_close_r(-1.0, cfg, symbol="B", path=state_path)
    assert st["stopped"] is True
    assert st["cum_r"] <= -2.0
    ok, why = live_enforce_ok(cfg, state=st)
    assert ok is False and "stop" in why
    v = evaluate(
        side=1, cfg=cfg, live=True, dump_flag=True, cycle_context={},
        live_state=st,
    )
    assert v.applied is False
    assert v.tags.get("live_blocked_stop") is True
    # Reset
    st2 = reset_live_stop(state_path)
    assert st2["stopped"] is False


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


def test_ce_report_smoke():
    from bot.ce_report import analyze_mode, analyze_both
    r = analyze_mode("dry", min_n=5)
    assert "verdict" in r and "n_shadow_events" in r
    b = analyze_both(min_n=5)
    assert "dual_verdict" in b
