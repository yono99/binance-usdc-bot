"""Fase 3 — H13 sektor lead-lag: mekanik clustering + kontrol positif/negatif
lewat engine skor generik (walk_forward_scores)."""
import numpy as np

from bot import sector
from bot import xsectional as xs


def test_greedy_clusters_block_matrix():
    """Matriks korelasi blok 2 klaster → label memisahkan blok dengan benar."""
    C = np.full((6, 6), 0.1)
    for grp in ([0, 1, 2], [3, 4, 5]):
        for i in grp:
            for j in grp:
                C[i, j] = 0.9
    labels = sector.greedy_clusters(C, threshold=0.6)
    assert labels[0] == labels[1] == labels[2]
    assert labels[3] == labels[4] == labels[5]
    assert labels[0] != labels[3]


def _leadlag_market(T=2000, followers=5, a=0.8, b=0.4, noise=0.005, seed=7):
    """2 klaster: tiap klaster 1 leader (volume 10×) + `followers` follower.
    follower_ret[t] = a·leader_ret[t] (co-move, membentuk klaster) +
    b·leader_ret[t-1] (LAG yang bisa dieksploitasi) + noise."""
    rng = np.random.default_rng(seed)
    rets, vols = [], []
    for k in range(2):
        lr = rng.normal(0, 0.02, T)
        rets.append(lr)
        vols.append(np.full(T, 10.0))
        for _ in range(followers):
            fr = a * lr + b * np.concatenate([[0.0], lr[:-1]]) + rng.normal(0, noise, T)
            rets.append(fr)
            vols.append(np.full(T, 1.0))
    r = np.column_stack(rets)
    close = 100 * np.exp(np.cumsum(r, axis=0))
    return close, np.column_stack(vols)


def test_sector_leadlag_positive_control():
    close, vol = _leadlag_market()
    score = sector.score_sector_leadlag(close, vol, corr_window=120, lead_lookback=1,
                                        threshold=0.5, refresh=10)
    # leader tidak boleh diskor (NaN); follower harus diskor setelah warmup
    assert np.all(~np.isfinite(score[150:, 0])) and np.all(~np.isfinite(score[150:, 6]))
    assert np.isfinite(score[150:, 1]).mean() > 0.9
    _, oos = xs.walk_forward_scores(close, {"sec": score}, holds=[1], quantile=0.3,
                                    cost_frac=0.0, train_len=600, test_len=300)
    v = xs.verdict(oos, 1)
    assert v["mean"] > 0 and v["ok"], v["reason"]


def test_sector_leadlag_negative_control():
    """Random walk independen (tanpa struktur lead-lag) → tak boleh lolos."""
    rng = np.random.default_rng(3)
    r = rng.normal(0, 0.02, (2000, 12))
    close = 100 * np.exp(np.cumsum(r, axis=0))
    vol = np.ones_like(close)
    score = sector.score_sector_leadlag(close, vol, corr_window=120, lead_lookback=1,
                                        threshold=0.5, refresh=10)
    _, oos = xs.walk_forward_scores(close, {"sec": score}, holds=[1], quantile=0.3,
                                    cost_frac=0.0, train_len=600, test_len=300)
    assert not xs.verdict(oos, 1)["ok"]
