"""Fase 3 — H14 listing-age lifecycle: mekanik panel umur, guard dispersi/sensor,
dan kontrol positif/negatif cohort walk-forward."""
import numpy as np
import pandas as pd

from bot import lifecycle as lc
from bot import xsectional as xs


def _df(start: str, closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(closes), freq="D", tz="UTC")
    return pd.DataFrame({"close": closes}, index=idx)


def test_age_return_panel_mechanics():
    dfs = {"A": _df("2024-01-01", [100, 110, 121]),      # +10%, +10%
           "B": _df("2024-06-01", [50, 45])}             # −10%
    panel, syms = lc.age_return_panel(dfs, max_age=3)
    a, b = syms.index("A"), syms.index("B")
    assert np.allclose(panel[a, :2], [0.10, 0.10])
    assert np.isnan(panel[a, 2])                          # histori habis
    assert np.isclose(panel[b, 0], -0.10) and np.isnan(panel[b, 1])


def test_window_return_compounds_and_requires_full_window():
    panel = np.array([[0.10, 0.10, np.nan]])
    wr = lc.window_return(panel, start=0, length=2)
    assert np.isclose(wr[0], 1.1 * 1.1 - 1)
    assert np.isnan(lc.window_return(panel, start=1, length=2)[0])  # ada NaN → gugur


def test_uncensored_drops_truncated_history():
    dfs = {"OLD": _df("2020-01-01", [1.0] * 100),         # mentok batas fetch
           "NEW": _df("2024-01-01", [1.0] * 40)}
    kept = lc.uncensored(dfs, requested_bars=100)
    assert "NEW" in kept and "OLD" not in kept


def test_dispersion_report_rejects_batched_listings():
    batched = pd.Series(pd.to_datetime(["2024-01-01", "2024-01-05", "2024-01-09"] * 10, utc=True),
                        index=[f"S{i}" for i in range(30)])
    assert not lc.dispersion_report(batched, min_span_days=365, min_symbols=20)["ok"]
    spread = pd.Series(pd.date_range("2021-01-01", periods=30, freq="60D", tz="UTC"),
                       index=[f"S{i}" for i in range(30)])
    assert lc.dispersion_report(spread, min_span_days=365, min_symbols=20)["ok"]


def _cohort_data(drift: float, S=40, A=120, seed=5, noise=0.01):
    """S simbol, listing tersebar; return noise, KECUALI umur 15–44 drift/hari."""
    rng = np.random.default_rng(seed)
    panel = rng.normal(0, noise, (S, A))
    panel[:, 15:45] += drift
    syms = [f"S{i}" for i in range(S)]
    dates = pd.Series(pd.date_range("2021-01-01", periods=S, freq="20D", tz="UTC"), index=syms)
    return panel, syms, dates


GRID = lc.build_grid([1, 8, 15, 30], [7, 14, 30])


def test_cohort_walk_forward_positive_control():
    panel, syms, dates = _cohort_data(drift=0.006)
    res = lc.cohort_walk_forward(panel, syms, dates, GRID, cost_frac=0.0)
    assert res is not None and res.direction == 1
    assert res.params["start"] in (15, 30)                # window menangkap zona drift
    v = xs.verdict(res.test_returns, res.n_trials)
    assert v["mean"] > 0 and v["ok"], v["reason"]


def test_cohort_walk_forward_negative_control():
    panel, syms, dates = _cohort_data(drift=0.0, seed=9)
    res = lc.cohort_walk_forward(panel, syms, dates, GRID, cost_frac=0.0)
    assert res is None or not xs.verdict(res.test_returns, res.n_trials)["ok"]


def test_cohort_walk_forward_detects_short_direction():
    panel, syms, dates = _cohort_data(drift=-0.006, seed=2)
    res = lc.cohort_walk_forward(panel, syms, dates, GRID, cost_frac=0.0)
    assert res is not None and res.direction == -1
    assert res.test_returns.mean() > 0                     # short fade → profit positif
