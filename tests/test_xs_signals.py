"""Fase 2 — builder skor cross-sectional + engine skor generik. Validasi mekanik
(beta/residual) & kontrol positif/negatif engine walk_forward_scores."""
import numpy as np
import pandas as pd

from bot import xs_signals as xss
from bot import xsectional as xs


def _market_panel(mus, T=3000, seed=11, noise=0.004):
    """BTC (kolom 0) random-walk; tiap alt = 1*btc_ret + mu_i (idio persisten) + noise.
    Beta semua=1 → komponen pasar CANCEL di long-short → tersisa alpha idio."""
    rng = np.random.default_rng(seed)
    btc_ret = rng.normal(0, 0.01, T)
    cols = {"BTC": 100 * np.exp(np.cumsum(btc_ret))}
    for i, mu in enumerate(mus):
        r = btc_ret + mu + rng.normal(0, noise, T)
        cols[f"P{i}"] = 100 * np.exp(np.cumsum(r))
    return pd.DataFrame(cols)


def test_rolling_beta_recovers_known_beta():
    rng = np.random.default_rng(0)
    rb = rng.normal(0, 0.01, 2000)
    r = np.column_stack([2.0 * rb, 0.5 * rb]) + rng.normal(0, 1e-4, (2000, 2))
    beta = xss.rolling_beta(r, rb, 200)
    assert abs(np.nanmean(beta[500:, 0]) - 2.0) < 0.1
    assert abs(np.nanmean(beta[500:, 1]) - 0.5) < 0.1


def test_residual_removes_market():
    rng = np.random.default_rng(1)
    rb = rng.normal(0, 0.01, 1500)
    r = np.column_stack([1.5 * rb, 1.5 * rb])          # murni beta, tanpa idio
    beta = xss.rolling_beta(r, rb, 200)
    resid = xss.residual_returns(r, rb, beta)
    assert np.nanstd(resid[400:]) < np.nanstd(r[400:]) * 0.2   # residual jauh lebih kecil


def test_score_engine_positive_control_residual_momentum():
    close = _market_panel(np.linspace(-0.0022, 0.0022, 9)).to_numpy()
    panels = {"rm10": xss.score_residual_momentum(close, 0, lookback=240, beta_window=240)}
    _, oos = xs.walk_forward_scores(close, panels, holds=[24, 48], quantile=0.3,
                                    cost_frac=0.0, train_len=800, test_len=300)
    v = xs.verdict(oos, 2)
    assert v["mean"] > 0 and v["ok"], v["reason"]


def test_score_engine_negative_control():
    close = _market_panel(np.zeros(9), seed=4).to_numpy()   # tak ada alpha idio
    panels = {"rm": xss.score_residual_momentum(close, 0, lookback=240, beta_window=240)}
    _, oos = xs.walk_forward_scores(close, panels, holds=[24, 48], quantile=0.3,
                                    cost_frac=0.0, train_len=800, test_len=300)
    assert not xs.verdict(oos, 2)["ok"]


def test_score_engine_reverse_flips_sign():
    close = _market_panel(np.linspace(-0.0022, 0.0022, 9)).to_numpy()
    panels = {"rm": xss.score_residual_momentum(close, 0, lookback=240, beta_window=240)}
    _, oos_norm = xs.walk_forward_scores(close, panels, [24], 0.3, 0.0, 800, 300)
    _, oos_rev = xs.walk_forward_scores(close, panels, [24], 0.3, 0.0, 800, 300, reverse=True)
    assert oos_norm.mean() > 0 and oos_rev.mean() < 0     # reverse = kebalikan
