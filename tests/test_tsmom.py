"""Fase 4 — H32 TSMOM harian: kontrol positif (drift persisten) & negatif (noise)."""
import numpy as np

from bot import tsmom
from bot import xsectional as xs


def _market(mu, T=3000, N=10, seed=41, noise=0.01):
    rng = np.random.default_rng(seed)
    drift = np.resize([mu, -mu], N)                      # separuh naik, separuh turun
    r = drift + rng.normal(0, noise, (T, N))
    return 100 * np.exp(np.cumsum(r, axis=0))


def test_tsmom_positive_control():
    close = _market(0.002)
    _, oos = tsmom.walk_forward_tsmom(close, lookbacks=[30, 60, 90], hold=5,
                                      cost_frac=0.0, train_len=800, test_len=300)
    v = xs.verdict(oos, 3)
    assert v["mean"] > 0 and v["ok"], v["reason"]


def test_tsmom_negative_control():
    close = _market(0.0, seed=43)
    _, oos = tsmom.walk_forward_tsmom(close, lookbacks=[30, 60, 90], hold=5,
                                      cost_frac=0.0, train_len=800, test_len=300)
    assert not xs.verdict(oos, 3)["ok"]
