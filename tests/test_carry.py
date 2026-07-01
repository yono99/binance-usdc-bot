"""Fase 2 — engine funding carry. Validasi: kontrol POSITIF (funding persisten +
harga flat → carry untung), NEGATIF (funding nol → nihil), dan KEJUJURAN (funding
tinggi + harga pump → short kelindas → TIDAK untung)."""
import numpy as np
import pandas as pd

from bot import carry
from bot.xsectional import verdict


def _synth(phis, T=3000, drifts=None, seed=5, noise=0.0005):
    """Panel harga + funding. phis[i] = funding 8h pair i (konstan). drifts[i] = drift
    harga/jam. Funding dibebankan tiap jam kelipatan 8."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=T, freq="1h", tz="UTC")
    drifts = drifts if drifts is not None else [0.0] * len(phis)
    closes, fundings, cols = {}, {}, []
    f_idx = idx[idx.hour % 8 == 0]
    for i, (phi, dr) in enumerate(zip(phis, drifts)):
        s = f"P{i}"
        cols.append(s)
        r = dr + rng.normal(0, noise, T)
        closes[s] = pd.Series(100 * np.exp(np.cumsum(r)), index=idx)
        fundings[s] = pd.Series([phi] * len(f_idx), index=f_idx)
    close = pd.DataFrame(closes)[cols].to_numpy()
    level, cumf = carry.align_funding(fundings, idx, cols)
    return close, level, cumf


def _run(close, level, cumf, cost=0.0):
    grid = carry.build_grid([1, 8], [8, 24])
    _, oos = carry.walk_forward_carry(close, level, cumf, grid, quantile=0.3,
                                      cost_frac=cost, train_len=800, test_len=300)
    return oos, len(grid)


def test_align_funding_cumulative_is_causal():
    idx = pd.date_range("2026-01-01", periods=17, freq="1h", tz="UTC")
    f = {"A": pd.Series([0.01], index=idx[idx.hour % 8 == 0][:1])}   # 1 event di jam 0
    level, cumf = carry.align_funding(f, idx, ["A"])
    assert cumf[0, 0] == 0.01 and cumf[-1, 0] == 0.01               # kumulatif tetap
    assert level[5, 0] == 0.01                                      # rate ffill ke depan


def test_positive_control_carry_profits():
    # 8 pair: sebagian funding tinggi positif, sebagian ~0; harga flat → carry murni
    phis = [0.002, 0.0015, 0.001, 0.0005, 0.0, -0.0005, -0.001, -0.0015]
    close, level, cumf = _synth(phis)
    oos, n_trials = _run(close, level, cumf)
    v = verdict(oos, n_trials)
    assert v["mean"] > 0 and v["ok"], v["reason"]


def test_negative_control_no_funding():
    close, level, cumf = _synth([0.0] * 8, seed=9)
    oos, n_trials = _run(close, level, cumf)
    assert not verdict(oos, n_trials)["ok"]


def test_honesty_price_run_over_cancels_carry():
    # funding tinggi TERKAIT harga pump (long crowded): short-carry kelindas harga
    phis = [0.002, 0.0015, 0.001, 0.0005, -0.0005, -0.001, -0.0015, -0.002]
    drifts = [0.004, 0.003, 0.002, 0.001, -0.001, -0.002, -0.003, -0.004]  # pump ~ funding
    close, level, cumf = _synth(phis, drifts=drifts, noise=0.001)
    oos, n_trials = _run(close, level, cumf)
    # income funding kecil vs pergerakan harga besar melawan → tak untung
    assert verdict(oos, n_trials)["mean"] <= 0
