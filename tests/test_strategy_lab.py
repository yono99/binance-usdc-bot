import numpy as np

from bot.backtest import Backtester
from bot.optimize import decide, precompute, warmup
from bot.strategy_lab import (
    build_grid_v2,
    build_grid_v3,
    build_grid_v4,
    decide_v2,
    decide_v3,
    decide_v4,
    precompute_v2,
    precompute_v3,
    precompute_v4,
    walk_forward_v2,
    walk_forward_v3,
    walk_forward_v4,
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
    # gerbang overextension = filter tambahan config-level; matikan utk uji ekuivalensi inti v1
    cfg_no_gate = {**cfg, "strategy": {**cfg["strategy"], "entry_rsi_max": 100,
                                       "entry_rsi_min": 0, "entry_ext_atr": float("inf")}}
    side_v2 = decide_v2(f2, g, cfg_no_gate, sessions=None)
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


def _g(**kw):
    base = {"entry_confidence": 0.4, "sl_atr_mult": 1.5, "tp_atr_mult": 2.5,
            "use_htf": False, "regime": False, "use_funding": False, "use_oi": False,
            "use_of": False}
    base.update(kw)
    return base


def test_v3_equals_v2_without_altdata(cfg, make_df):
    df = make_df(_trend(400))
    n = len(df)
    f3 = precompute_v3(df, cfg, 4, np.zeros(n), np.zeros(n))
    g = _g()
    side_v2 = decide_v2(f3.v2, g, cfg, None)
    side_v3 = decide_v3(f3, g, cfg, None)
    assert np.array_equal(side_v2, side_v3)


def test_funding_blocks_crowded_longs(cfg, make_df):
    df = make_df(_trend(400))
    n = len(df)
    fz = np.full(n, 5.0)   # funding sangat tinggi (long crowded) di semua bar
    f3 = precompute_v3(df, cfg, 4, fz, np.zeros(n))
    side = decide_v3(f3, _g(use_funding=True), cfg, None)
    assert not np.any(side == 1)   # semua long diblokir


def test_oi_requires_rising_interest(cfg, make_df):
    df = make_df(_trend(400))
    n = len(df)
    f3 = precompute_v3(df, cfg, 4, np.zeros(n), np.full(n, -0.1))  # OI turun terus
    side = decide_v3(f3, _g(use_oi=True), cfg, None)
    assert np.all(side == 0)       # tak ada entry saat OI tak naik


def test_walk_forward_v3_smoke(cfg, make_df):
    df = make_df(_trend(1500))
    n = len(df)
    bt = Backtester(cfg, fee_pct=0.04, slippage_pct=0.02)
    grid = build_grid_v3([0.55, 0.6], [1.5], [2.5], [True], [False], [False, True], [False, True])
    rng = np.random.default_rng(1)
    results, oos = walk_forward_v3(df, cfg, grid, bt, 400, 150, 5, 4, None,
                                   rng.normal(0, 1, n), rng.normal(0, 0.05, n))
    assert isinstance(results, list) and isinstance(oos, list)


def _f4(df, cfg, n, imb, div):
    return precompute_v4(df, cfg, 4, np.zeros(n), np.zeros(n), imb, div)


def test_v4_equals_v3_without_orderflow(cfg, make_df):
    df = make_df(_trend(400))
    n = len(df)
    f4 = _f4(df, cfg, n, np.zeros(n), np.zeros(n, dtype=bool))
    g = _g()
    assert np.array_equal(decide_v3(f4.v3, g, cfg, None), decide_v4(f4, g, cfg, None))


def test_orderflow_blocks_longs_without_buying(cfg, make_df):
    df = make_df(_trend(400))
    n = len(df)
    f4 = _f4(df, cfg, n, np.full(n, -0.5), np.zeros(n, dtype=bool))  # tekanan jual
    side = decide_v4(f4, _g(use_of=True), cfg, None)
    assert not np.any(side == 1)


def test_orderflow_divergence_vetoes_all(cfg, make_df):
    df = make_df(_trend(400))
    n = len(df)
    f4 = _f4(df, cfg, n, np.full(n, 0.5), np.ones(n, dtype=bool))    # divergensi di semua bar
    side = decide_v4(f4, _g(use_of=True), cfg, None)
    assert np.all(side == 0)


def test_default_forward_params_consumable(cfg, make_df):
    from bot.forward import default_params
    df = make_df(_trend(400))
    n = len(df)
    f4 = _f4(df, cfg, n, np.full(n, 0.1), np.zeros(n, dtype=bool))
    side = decide_v4(f4, default_params(), cfg, None)
    assert len(side) == n
    assert set(np.unique(side)).issubset({-1, 0, 1})


def test_walk_forward_v4_smoke(cfg, make_df):
    df = make_df(_trend(1500))
    n = len(df)
    bt = Backtester(cfg, fee_pct=0.04, slippage_pct=0.02)
    grid = build_grid_v4([0.55, 0.6], [1.5], [2.5], [True], [False], [False], [False], [False, True])
    rng = np.random.default_rng(2)
    results, oos = walk_forward_v4(df, cfg, grid, bt, 400, 150, 5, 4, None,
                                   np.zeros(n), np.zeros(n),
                                   rng.normal(0, 0.3, n), np.zeros(n, dtype=bool))
    assert isinstance(results, list) and isinstance(oos, list)
