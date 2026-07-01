"""Fase 2 — combiner multi-sinyal. Validasi seleksi (positif + sign-stability +
korelasi rendah), portfolio, dan pipeline lockbox (kontrol positif/negatif)."""
import numpy as np
import pandas as pd

from bot import combiner as cb
from bot import xs_signals as xss


def _panel(mus, T=3000, seed=11, noise=0.004):
    rng = np.random.default_rng(seed)
    btc = rng.normal(0, 0.01, T)
    cols = {"BTC": 100 * np.exp(np.cumsum(btc))}
    for i, mu in enumerate(mus):
        cols[f"P{i}"] = 100 * np.exp(np.cumsum(btc + mu + rng.normal(0, noise, T)))
    return np.column_stack(list(cols.values()))


# ---------- seleksi ----------

def test_select_drops_negative_and_correlated():
    rng = np.random.default_rng(0)
    n = 400
    good1 = rng.normal(0.002, 0.01, n)
    good2 = rng.normal(0.0015, 0.01, n)          # independen dari good1
    dup = good1 * 0.9 + rng.normal(0, 0.001, n)  # ~korelasi dgn good1 → dibuang
    bad = rng.normal(-0.002, 0.01, n)            # negatif → dibuang
    df = pd.DataFrame({"good1": good1, "good2": good2, "dup": dup, "bad": bad})
    sel = cb.select_signals(df, corr_max=0.3)
    assert "bad" not in sel                       # mean negatif dibuang
    assert "good1" in sel and "good2" in sel      # positif & tak korelasi disimpan
    assert "dup" not in sel                        # korelasi tinggi ke good1 dibuang


def test_combine_equal_weight_is_mean():
    df = pd.DataFrame({"a": [0.1, 0.2], "b": [0.3, 0.4]})
    out = cb.combine(df, ["a", "b"])
    assert np.allclose(out, [0.2, 0.3])


def test_score_series_aligned_length():
    close = _panel(np.linspace(-0.002, 0.002, 8))
    score = xss.score_residual_momentum(close, 0, 240, 240)
    times = list(range(300, 1000, 24))
    s = cb.score_series(close, score, times, hold=24, quantile=0.3, cost_frac=0.0)
    assert len(s) == len(times)                    # teraligned ke times (NaN diizinkan)


# ---------- pipeline lockbox ----------

def test_run_combiner_selects_real_signal():
    close = _panel(np.linspace(-0.0025, 0.0025, 10))
    rng = np.random.default_rng(2)
    signals = {
        "resid_mom": xss.score_residual_momentum(close, 0, 240, 240),   # ada edge
        "noise": rng.normal(0, 1, close.shape),                         # tak ada
    }
    out = cb.run_combiner(close, signals, hold=24, quantile=0.3, cost_frac=0.0,
                          lockbox_frac=0.3, warm=260)
    # Jaminan pipeline: sinyal NYATA terpilih & portfolio positif di LOCKBOX.
    # (Noise bisa lolos seleksi in-sample karena kebetulan — itu justru alasan
    #  lockbox jadi hakim; lihat kontrol-negatif noise-murni di bawah.)
    assert "resid_mom" in out["selected"]
    assert out["combined_mean_lockbox"] > 0


def test_run_combiner_pure_noise_rejected():
    close = _panel(np.zeros(10), seed=5)
    rng = np.random.default_rng(7)
    signals = {"n1": rng.normal(0, 1, close.shape), "n2": rng.normal(0, 1, close.shape)}
    out = cb.run_combiner(close, signals, hold=24, quantile=0.3, cost_frac=0.0,
                          lockbox_frac=0.3, warm=50)
    assert not out["ok"]                            # noise → tak lolos
