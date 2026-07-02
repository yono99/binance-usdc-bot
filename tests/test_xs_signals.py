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


def _shock_market(revert: bool, N=24, T=3000, seed=13):
    """H26: tiap simbol dapat 'syok likuiditas' berkala (volume /20, overshoot
    ±6%) tersebar antar simbol. revert=True → drift balik 6 hari (reversal nyata);
    revert=False → overshoot permanen (tak ada edge)."""
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.01, (T, N))
    vol = np.full((T, N), 1e6)
    for i in range(N):
        for t0 in range(100 + int(i * 30 / N), T - 12, 30):
            sign = 1 if (t0 + i) % 2 == 0 else -1
            r[t0, i] += sign * 0.06
            vol[t0:t0 + 6, i] /= 20.0
            if revert:
                r[t0 + 1:t0 + 7, i] -= sign * 0.008
    close = 100 * np.exp(np.cumsum(r, axis=0))
    return close, vol


def test_illiq_shock_positive_control():
    close, vol = _shock_market(revert=True)
    panels = {f"is{w}": xss.score_illiq_shock(close, vol, w) for w in (3, 5)}
    _, oos = xs.walk_forward_scores(close, panels, holds=[3, 5], quantile=0.3,
                                    cost_frac=0.0, train_len=800, test_len=300)
    v = xs.verdict(oos, 4)
    assert v["mean"] > 0 and v["ok"], v["reason"]


def test_illiq_shock_negative_control():
    close, vol = _shock_market(revert=False, seed=21)
    panels = {f"is{w}": xss.score_illiq_shock(close, vol, w) for w in (3, 5)}
    _, oos = xs.walk_forward_scores(close, panels, holds=[3, 5], quantile=0.3,
                                    cost_frac=0.0, train_len=800, test_len=300)
    assert not xs.verdict(oos, 4)["ok"]


def test_illiq_shock_nan_when_no_shock():
    rng = np.random.default_rng(2)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, (500, 6)), axis=0))
    vol = np.full((500, 6), 1e6)                     # likuiditas stabil → tanpa syok
    sc = xss.score_illiq_shock(close, vol, 3)
    assert np.isfinite(sc[100:]).mean() < 0.35       # mayoritas NaN (ratio ~1 < thr)


def test_downside_beta_recovers_asymmetry():
    """H31 mekanik: aset dgn β⁻=2, β⁺=0.5 → skor ≈ 1.5; simetris → ≈ 0."""
    rng = np.random.default_rng(5)
    T = 2000
    rb = rng.normal(0, 0.01, T)
    asym = np.where(rb < 0, 2.0 * rb, 0.5 * rb)
    sym = 1.0 * rb
    r = np.column_stack([rb, asym, sym]) + rng.normal(0, 5e-4, (T, 3))
    close = 100 * np.exp(np.cumsum(r, axis=0))
    sc = xss.score_downside_beta(close, 0, window=250)
    assert abs(np.nanmean(sc[500:, 1]) - 1.5) < 0.3
    assert abs(np.nanmean(sc[500:, 2])) < 0.3


def test_downside_beta_engine_controls():
    """H31 engine: drift ∝ asimetri → lolos; tanpa drift → gugur."""
    rng = np.random.default_rng(11)
    T, N = 3000, 10
    rb = rng.normal(0, 0.01, T)
    asyms = np.linspace(0.0, 1.0, N)

    def market(mu_scale):
        cols = [rb]
        comp = np.abs(rb).mean()          # beta asimetris menyeret drift −a·E|rb| → netralkan
        for a in asyms:
            beta_t = np.where(rb < 0, 1.0 + a, 1.0 - a)
            cols.append(beta_t * rb + a * comp + mu_scale * a + rng.normal(0, 0.004, T))
        return 100 * np.exp(np.cumsum(np.column_stack(cols), axis=0))

    close = market(0.003)
    panels = {"db": xss.score_downside_beta(close, 0, window=250)}
    _, oos = xs.walk_forward_scores(close, panels, holds=[5, 10], quantile=0.3,
                                    cost_frac=0.0, train_len=800, test_len=300)
    v = xs.verdict(oos, 2)
    assert v["mean"] > 0 and v["ok"], v["reason"]

    close0 = market(0.0)
    panels0 = {"db": xss.score_downside_beta(close0, 0, window=250)}
    _, oos0 = xs.walk_forward_scores(close0, panels0, holds=[5, 10], quantile=0.3,
                                     cost_frac=0.0, train_len=800, test_len=300)
    assert not xs.verdict(oos0, 2)["ok"]


def _basis_market(link: bool, T=2500, N=12, seed=31):
    """H27: basis AR-noise per simbol + event spike +4σ berkala. link=True →
    3 hari setelah spike harga jatuh (dislokasi bermakna); False → tak berefek."""
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.008, (T, N))
    basis = rng.normal(0, 0.0005, (T, N))
    for i in range(N):
        for t0 in range(120 + i * 3, T - 6, 40):
            basis[t0:t0 + 3, i] += 0.004                 # spike ~+4σ (3 hari)
            if link:
                r[t0 + 1:t0 + 4, i] -= 0.006             # harga turun sesudahnya
    close = 100 * np.exp(np.cumsum(r, axis=0))
    return close, basis


def test_venue_basis_engine_controls():
    close, basis = _basis_market(link=True)
    panels = {"vb": xss.score_venue_basis(basis, window=30)}
    _, oos = xs.walk_forward_scores(close, panels, holds=[2, 5], quantile=0.3,
                                    cost_frac=0.0, train_len=800, test_len=300)
    v = xs.verdict(oos, 2)
    assert v["mean"] > 0 and v["ok"], v["reason"]

    close0, basis0 = _basis_market(link=False, seed=37)
    panels0 = {"vb": xss.score_venue_basis(basis0, window=30)}
    _, oos0 = xs.walk_forward_scores(close0, panels0, holds=[2, 5], quantile=0.3,
                                     cost_frac=0.0, train_len=800, test_len=300)
    assert not xs.verdict(oos0, 2)["ok"]


def test_oi_crowding_controls():
    """H19 builder: crowding segar (funding+ & OI naik) mendahului penurunan →
    lolos; OI acak tanpa hubungan → gugur. Gerbang funding_min menonaktifkan
    simbol ber-funding tipis (NaN)."""
    rng = np.random.default_rng(19)
    T, N = 3000, 10
    lvl = np.tile(np.resize([0.002, -0.002], N), (T, 1))
    oi_up = np.cumprod(1 + rng.normal(0.001, 0.001, (T, N)), axis=0)  # OI naik
    r = rng.normal(0, 0.006, (T, N))
    # funding+ & OI naik → harga turun; funding− & OI naik → harga naik (squeeze dua arah)
    r += -np.sign(lvl) * 0.004
    close = 100 * np.exp(np.cumsum(r, axis=0))
    sc = xss.score_oi_crowding(lvl, oi_up, delta_window=72)
    _, oos = xs.walk_forward_scores(close, {"oi": sc}, holds=[24], quantile=0.3,
                                    cost_frac=0.0, train_len=900, test_len=400)
    v = xs.verdict(oos, 1)
    assert v["mean"] > 0 and v["ok"], v["reason"]

    close0 = 100 * np.exp(np.cumsum(rng.normal(0, 0.006, (T, N)), axis=0))
    sc0 = xss.score_oi_crowding(lvl, oi_up, delta_window=72)
    _, oos0 = xs.walk_forward_scores(close0, {"oi": sc0}, holds=[24], quantile=0.3,
                                     cost_frac=0.0, train_len=900, test_len=400)
    assert not xs.verdict(oos0, 1)["ok"]

    thin = xss.score_oi_crowding(np.full((T, N), 0.0001), oi_up, 72)   # funding tipis
    assert np.isnan(thin[100:]).all()
