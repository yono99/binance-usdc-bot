import numpy as np

from bot.backtest import Backtester
from bot.optimize import (
    build_grid,
    decide,
    precompute,
    walk_forward,
    warmup,
)
from bot.signals import evaluate


def _noisy_trend(n):
    rng = np.random.default_rng(42)
    base = np.cumsum(rng.normal(0.05, 1.0, n)) + 100
    return list(base)


def test_decide_matches_signal_engine(cfg, make_df):
    """Jalur vektor (decide) harus identik dengan bot.signals.evaluate."""
    df = make_df(_noisy_trend(300))
    f = precompute(df, cfg)
    side = decide(f, cfg["signals"]["entry_confidence"])
    m = {"long": 1, "short": -1, "skip": 0}
    warm = warmup(cfg)
    mismatch = 0
    total = 0
    for k in range(warm, len(df) - 1):
        expected = m[evaluate("X", df.iloc[: k + 1], cfg).side]
        total += 1
        if side[k] != expected:
            mismatch += 1
    assert total > 0
    assert mismatch / total <= 0.02, f"{mismatch}/{total} bar tidak cocok"


def test_build_grid_filters_bad_rr():
    grid = build_grid([0.6], [2.0], [1.0])  # tp 1.0 <= sl*0.6=1.2 -> dibuang
    assert grid == []
    grid2 = build_grid([0.6], [1.0], [2.0])
    assert len(grid2) == 1


def test_walk_forward_smoke(cfg, make_df):
    df = make_df(_noisy_trend(1500))
    bt = Backtester(cfg, fee_pct=0.04, slippage_pct=0.02)
    grid = build_grid([0.55, 0.6], [1.0, 1.5], [2.0, 2.5])
    results, oos = walk_forward(df, cfg, grid, bt, train_len=400, test_len=150, min_trades=5)
    assert isinstance(results, list)
    assert isinstance(oos, list)
    for w in results:
        assert w.train_range[1] == w.test_range[0]   # test tepat setelah train
        assert "entry_confidence" in w.params
