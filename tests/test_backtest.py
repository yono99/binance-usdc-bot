import pandas as pd

from bot.backtest import Backtester, Trade, compute_metrics


def _bt(cfg):
    return Backtester(cfg, fee_pct=0.0, slippage_pct=0.0)


def test_close_r_math_long(cfg):
    bt = _bt(cfg)
    pos = {
        "symbol": "X", "side": "long", "entry": 100.0, "sl": 98.0, "tp": 105.0,
        "risk_per_unit": 2.0, "entry_time": pd.Timestamp("2026-01-01", tz="UTC"), "entry_idx": 0,
    }
    t = bt._close(pos, 104.0, pd.Timestamp("2026-01-02", tz="UTC"), 5, "tp")
    assert t.r == 2.0       # move 4 / risk 2 = 2R, tanpa fee
    assert t.bars_held == 5


def test_close_r_math_short(cfg):
    bt = _bt(cfg)
    pos = {
        "symbol": "X", "side": "short", "entry": 100.0, "sl": 102.0, "tp": 95.0,
        "risk_per_unit": 2.0, "entry_time": pd.Timestamp("2026-01-01", tz="UTC"), "entry_idx": 0,
    }
    t = bt._close(pos, 96.0, pd.Timestamp("2026-01-02", tz="UTC"), 3, "tp")
    assert t.r == 2.0       # short profit (100->96) / risk 2 = 2R


def test_fee_reduces_r(cfg):
    bt = Backtester(cfg, fee_pct=0.1, slippage_pct=0.0)
    pos = {
        "symbol": "X", "side": "long", "entry": 100.0, "sl": 98.0, "tp": 105.0,
        "risk_per_unit": 2.0, "entry_time": pd.Timestamp("2026-01-01", tz="UTC"), "entry_idx": 0,
    }
    t = bt._close(pos, 104.0, pd.Timestamp("2026-01-02", tz="UTC"), 1, "tp")
    assert t.r < 2.0        # fee menggerus R


def _mk(r, day):
    return Trade("X", "long", pd.Timestamp(f"2026-01-{day:02d}", tz="UTC"),
                 pd.Timestamp(f"2026-01-{day:02d}", tz="UTC"), 100, 101, 99, 103, r, 1, "tp")


def test_metrics_basic(cfg):
    trades = [_mk(1.0, 1), _mk(-1.0, 2), _mk(2.0, 3)]
    m = compute_metrics(trades, cfg, 1000.0)
    assert m["trades"] == 3
    assert m["wins"] == 2 and m["losses"] == 1
    assert abs(m["win_rate"] - 66.6667) < 0.01
    assert abs(m["expectancy_r"] - (2.0 / 3.0)) < 1e-9
    assert abs(m["profit_factor"] - 3.0) < 1e-9   # (1+2)/1
    assert m["return_pct"] > 0


def test_metrics_empty(cfg):
    assert compute_metrics([], cfg, 1000.0) == {"trades": 0}


def test_run_symbol_no_lookahead(cfg, make_df):
    bt = _bt(cfg)
    df = make_df([100 + i * 0.5 for i in range(200)])
    trades = bt.run_symbol("X", df)
    assert isinstance(trades, list)
    for t in trades:
        assert t.exit_time >= t.entry_time   # exit tak pernah sebelum entry
