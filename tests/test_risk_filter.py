"""Unit tests for bot.risk_filter — meta risk overlay (not entry alpha)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from bot.risk_filter import (
    FilterVerdict,
    avg_corr_vs_ew,
    breadth_fraction,
    evaluate_risk_filters,
    from_config,
    stamp,
)


def _synthetic_panel(n_days: int = 200, n_alts: int = 12, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n_days, freq="D", tz="UTC")
    # Random walk alts with mild common factor
    common = rng.normal(0, 0.01, size=n_days).cumsum()
    data = {}
    for i in range(n_alts):
        idio = rng.normal(0, 0.015, size=n_days).cumsum()
        data[f"ALT{i}"] = 100 * np.exp(common * 0.5 + idio)
    return pd.DataFrame(data, index=idx)


def _btc_from_panel(panel: pd.DataFrame, seed: int = 1) -> pd.Series:
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.02, size=len(panel))
    return pd.Series(100 * np.exp(np.cumsum(r)), index=panel.index, name="BTC")


def test_from_config_defaults():
    f = from_config({})
    assert f["shadow"] is False and f["block"] is False
    assert f["skip_breadth_lo"] is True and f["skip_corr_or_volhi"] is True


def test_from_config_flags():
    f = from_config({"agent": {
        "risk_filter_shadow": True,
        "risk_filter_block": False,
        "risk_filter_breadth": False,
        "risk_filter_corr_vol": True,
    }})
    assert f["shadow"] is True and f["block"] is False
    assert f["skip_breadth_lo"] is False and f["skip_corr_or_volhi"] is True


def test_breadth_fraction_range():
    panel = _synthetic_panel()
    b = breadth_fraction(panel, 50)
    assert len(b) == len(panel)
    valid = b.dropna()
    assert (valid >= 0).all() and (valid <= 1).all()


def test_avg_corr_finite():
    panel = _synthetic_panel()
    ac = avg_corr_vs_ew(panel.pct_change(), 20)
    assert np.isfinite(ac)


def test_evaluate_no_panel_allows():
    v = evaluate_risk_filters(panel_daily=None, btc_close=None)
    assert v.allow is True and v.metrics.get("note") == "no_panel"


def test_evaluate_short_history_allows():
    panel = _synthetic_panel(n_days=40)
    v = evaluate_risk_filters(panel_daily=panel, btc_close=None)
    assert v.allow is True and v.metrics.get("note") == "short_history"


def test_evaluate_returns_verdict_shape():
    panel = _synthetic_panel()
    btc = _btc_from_panel(panel)
    v = evaluate_risk_filters(panel_daily=panel, btc_close=btc)
    assert isinstance(v, FilterVerdict)
    assert isinstance(v.allow, bool)
    assert isinstance(v.reasons, list)
    assert "breadth" in v.metrics or "note" in v.metrics


def test_breadth_lo_triggers_when_all_below_sma():
    """Force last stretch of declines so breadth is in bottom quantile."""
    idx = pd.date_range("2024-01-01", periods=150, freq="D", tz="UTC")
    # First 100 days flat-up, last 50 crash hard → breadth near 0 at end
    base = np.concatenate([np.linspace(100, 120, 100), np.linspace(120, 40, 50)])
    data = {f"A{i}": base * (1 + 0.001 * i) for i in range(8)}
    panel = pd.DataFrame(data, index=idx)
    v = evaluate_risk_filters(
        panel_daily=panel, btc_close=None,
        skip_breadth_lo=True, skip_corr_or_volhi=False,
    )
    assert "breadth_lo" in v.reasons
    assert v.allow is False


def test_corr_family_only():
    panel = _synthetic_panel()
    btc = _btc_from_panel(panel)
    v = evaluate_risk_filters(
        panel_daily=panel, btc_close=btc,
        skip_breadth_lo=False, skip_corr_or_volhi=True,
    )
    assert "breadth_lo" not in v.reasons
    # allow depends on corr/vol; just ensure no crash
    assert isinstance(v.allow, bool)


def test_stamp_empty_and_full():
    assert stamp(None) == {}
    v = FilterVerdict(False, ["breadth_lo"], {"breadth": 0.12})
    s = stamp(v)
    assert s["risk_filter_allow"] is False
    assert s["risk_filter_reasons"] == ["breadth_lo"]
    assert s["risk_filter_metrics"]["breadth"] == 0.12


def test_asof_truncates():
    panel = _synthetic_panel(n_days=200)
    mid = panel.index[100]
    v_full = evaluate_risk_filters(panel_daily=panel, btc_close=None, asof=None)
    v_mid = evaluate_risk_filters(panel_daily=panel, btc_close=None, asof=mid)
    # mid has enough history; metrics may differ from full
    assert isinstance(v_mid.allow, bool)
    assert isinstance(v_full.allow, bool)
