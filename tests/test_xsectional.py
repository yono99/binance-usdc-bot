"""Fase 2 — engine cross-sectional momentum. Validasi: kontrol POSITIF (ada struktur
relatif → engine menangkap) & NEGATIF (random-walk → engine TIDAK menipu diri)."""
import numpy as np
import pandas as pd

from bot import xsectional as xs


def _panel(mus, T=3000, seed=7, noise=0.01):
    """Panel harga: tiap pair punya drift mu_i persisten + noise. Rank return masa-lalu
    memulihkan urutan mu → cross-sectional momentum harusnya profit bila ada struktur."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=T, freq="1h", tz="UTC")
    data = {}
    for i, mu in enumerate(mus):
        r = mu + rng.normal(0, noise, T)
        data[f"P{i}"] = pd.Series(100 * np.exp(np.cumsum(r)), index=idx)
    return pd.DataFrame(data)


# ---------- alignment ----------

def test_align_panel_drops_incomplete_rows():
    a = pd.DataFrame({"close": [1, 2, 3, 4]},
                     index=pd.date_range("2026-01-01", periods=4, freq="1h", tz="UTC"))
    b = pd.DataFrame({"close": [np.nan, 5, 6, 7]},
                     index=pd.date_range("2026-01-01", periods=4, freq="1h", tz="UTC"))
    panel = xs.align_close_panel({"A": a, "B": b}, min_coverage=0.5)
    assert list(panel.columns) == ["A", "B"]
    assert panel["B"].isna().sum() == 0 and len(panel) == 3   # baris NaN pertama (leading) dibuang
    # coverage ketat (default 0.9): B (75% terisi) di-drop → hanya A tersisa
    strict = xs.align_close_panel({"A": a, "B": b})
    assert list(strict.columns) == ["A"]


# ---------- no-lookahead / mekanik ----------

def test_xs_returns_sign_matches_forward():
    # 4 pair; pair 3 momentum tertinggi & forward positif → long; pair 0 terendah → short
    T = 60
    close = np.ones((T, 4))
    close[:, 0] = np.linspace(100, 80, T)     # turun (terlemah)
    close[:, 1] = np.linspace(100, 95, T)
    close[:, 2] = np.linspace(100, 105, T)
    close[:, 3] = np.linspace(100, 130, T)    # naik (terkuat)
    times = xs._rebalance_times(0, T, lookback=10, hold=5)
    r = xs.xs_returns(close, times, lookback=10, hold=5, quantile=0.25, cost_frac=0.0)
    assert len(r) > 0 and r.mean() > 0        # long-kuat − short-lemah → positif


# ---------- kontrol POSITIF: struktur nyata harus tertangkap ----------

def test_positive_control_finds_edge():
    mus = np.linspace(-0.0025, 0.0025, 10)    # 10 pair, drift menyebar (struktur kuat)
    close = _panel(mus).to_numpy()
    grid = xs.build_grid([24, 48], [6, 12])   # 4 trial saja (low-DOF)
    _, oos = xs.walk_forward_xs(close, grid, quantile=0.3, cost_frac=0.0,
                                train_len=800, test_len=300)
    v = xs.verdict(oos, n_trials=len(grid))
    assert v["mean"] > 0 and v["ok"], v["reason"]   # harus LOLOS


# ---------- kontrol NEGATIF: random-walk TIDAK boleh 'lolos' ----------

def test_negative_control_finds_nothing():
    close = _panel(np.zeros(10), seed=3).to_numpy()   # tanpa drift → tak ada struktur
    grid = xs.build_grid([24, 48], [6, 12])
    _, oos = xs.walk_forward_xs(close, grid, quantile=0.3, cost_frac=0.0,
                                train_len=800, test_len=300)
    v = xs.verdict(oos, n_trials=len(grid))
    assert not v["ok"], f"random-walk seharusnya ditolak: {v}"


# ---------- verdict: koreksi multiple-testing ----------

def test_verdict_penalizes_many_trials():
    rng = np.random.default_rng(1)
    oos = rng.normal(0.001, 0.02, 200)        # edge tipis marginal
    few = xs.verdict(oos, n_trials=1)
    many = xs.verdict(oos, n_trials=500)      # koreksi banyak trial → p_adj naik
    assert many["p_adj"] >= few["p_adj"]


# ---------- regime-conditional (threshold dispersi dipelajari di train) ----------

def test_xs_step_returns_aligned_dispersion():
    close = _panel(np.linspace(-0.002, 0.002, 8), T=500).to_numpy()
    times = xs._rebalance_times(0, 500, lookback=48, hold=12)
    rets, disps = xs._xs_step(close, times, 48, 12, 0.3, 0.0, False)
    assert len(rets) == len(disps) and (disps >= 0).all()


def test_regime_filters_reduces_rebalances():
    close = _panel(np.linspace(-0.002, 0.002, 10)).to_numpy()
    grid = xs.build_grid([48], [12])
    _, oos_all = xs.walk_forward_xs(close, grid, 0.3, 0.0, 800, 300)
    _, oos_reg = xs.walk_forward_xs_regime(close, grid, 0.3, 0.0, 800, 300)
    assert len(oos_reg) < len(oos_all)        # regime hanya ambil subset (dispersi tinggi)


def test_regime_positive_control_still_passes():
    close = _panel(np.linspace(-0.0025, 0.0025, 10)).to_numpy()
    grid = xs.build_grid([24, 48], [6, 12])
    _, oos = xs.walk_forward_xs_regime(close, grid, 0.3, 0.0, 800, 300)
    assert xs.verdict(oos, len(grid))["mean"] > 0   # regime tak merusak sinyal asli


def test_regime_negative_control_rejected():
    close = _panel(np.zeros(10), seed=3).to_numpy()
    grid = xs.build_grid([24, 48], [6, 12])
    _, oos = xs.walk_forward_xs_regime(close, grid, 0.3, 0.0, 800, 300)
    assert not xs.verdict(oos, len(grid))["ok"]     # random-walk tetap ditolak
