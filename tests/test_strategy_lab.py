import numpy as np

from bot.backtest import Backtester
from bot.optimize import decide, precompute, warmup
from bot.strategy_lab import (
    build_grid_v2,
    decide_v2,
    precompute_v2,
    walk_forward_v2,
)


def _trend(n):
    rng = np.random.default_rng(7)
    return list(np.cumsum(rng.normal(0.05, 1.0, n)) + 100)


def test_v2_equals_v1_without_enhancements(cfg, make_df):
    df = make_df(_trend(400))
    f = precompute(df, cfg)
    f2 = precompute_v2(df, cfg, htf_mult=4)
    conf = cfg["signals"]["entry_confidence"]
    g = {"entry_confidence": conf, "sl_atr_mult": 1.5, "tp_atr_mult": 2.5,
         "use_htf": False, "regime": False}
    side_v1 = decide(f, conf)
    side_v2 = decide_v2(f2, g, cfg, sessions=None)
    warm = warmup(cfg)
    assert np.array_equal(side_v1[warm:], side_v2[warm:])


def test_htf_gate_blocks_counter_trend(cfg, make_df):
    df = make_df(_trend(400))
    f2 = precompute_v2(df, cfg, htf_mult=4)
    g = {"entry_confidence": 0.4, "sl_atr_mult": 1.5, "tp_atr_mult": 2.5,
         "use_htf": True, "regime": False}
    side = decide_v2(f2, g, cfg, sessions=None)
    # tidak boleh ada LONG saat HTF turun, atau SHORT saat HTF naik
    assert not np.any((side == 1) & (f2.htf_dir < 0))
    assert not np.any((side == -1) & (f2.htf_dir > 0))


def test_session_mask_zeroes_outside_hours(cfg, make_df):
    df = make_df(_trend(400))
    f2 = precompute_v2(df, cfg, htf_mult=4)
    g = {"entry_confidence": 0.4, "sl_atr_mult": 1.5, "tp_atr_mult": 2.5,
         "use_htf": False, "regime": False}
    allowed = {0, 1, 2}
    side = decide_v2(f2, g, cfg, sessions=allowed)
    outside = ~np.isin(f2.hour, list(allowed))
    assert np.all(side[outside] == 0)


def test_walk_forward_v2_smoke(cfg, make_df):
    df = make_df(_trend(1500))
    bt = Backtester(cfg, fee_pct=0.04, slippage_pct=0.02)
    grid = build_grid_v2([0.55, 0.6], [1.5], [2.5], [False, True], [False, True])
    results, oos = walk_forward_v2(df, cfg, grid, bt, 400, 150, 5, htf_mult=4, sessions=None)
    assert isinstance(results, list) and isinstance(oos, list)
