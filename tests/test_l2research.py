"""Pipeline H30: mekanik half-life, fill-proxy/adverse (+/− kontrol), verdict gates."""
import numpy as np
import pandas as pd

from bot import l2research as lr


def _frames(T=5000, spread_bps=10.0, adverse_drift=0.0, seed=3):
    """Snapshot 2s sintetis: mid random-walk; SETELAH mid menembus bid (fill-proxy),
    mid melanjutkan drift `adverse_drift` (negatif = terus turun → adverse nyata)."""
    rng = np.random.default_rng(seed)
    mid = 100 * np.exp(np.cumsum(rng.normal(0, 2e-4, T)))
    half = spread_bps / 2 / 1e4
    bid, ask = mid * (1 - half), mid * (1 + half)
    if adverse_drift:
        cross = mid[1:] <= bid[:-1]
        for i in np.where(cross)[0]:
            mid[i + 1:i + 40] *= (1 + adverse_drift)
    ts = 1_700_000_000_000 + np.arange(T) * 2000
    return pd.DataFrame({"ts": ts, "mid": mid, "spread_bps": np.full(T, spread_bps),
                         "bid1": bid, "ask1": ask})


def test_half_life_mean_reverting_vs_constant():
    rng = np.random.default_rng(1)
    s = np.zeros(3000)
    for t in range(1, 3000):                      # AR(1) phi=0.9 → hl ≈ 6.6
        s[t] = 0.9 * s[t - 1] + rng.normal(0, 1)
    hl = lr.spread_half_life(s + 10)
    assert hl is not None and 4 < hl < 10
    assert lr.spread_half_life(np.full(3000, 10.0)) is None


def test_edge_positive_when_no_adverse():
    df = _frames(adverse_drift=0.0)
    v = lr.analyze_symbol(df)
    assert v["fills"] > 10
    assert v["edge_gross_bps"] is not None and v["edge_gross_bps"] > 2.0  # ~spread/2


def test_edge_killed_by_adverse_selection():
    df = _frames(adverse_drift=-0.0005, seed=5)   # harga lanjut turun pasca-fill
    v = lr.analyze_symbol(df)
    assert v["edge_gross_bps"] < lr.analyze_symbol(_frames(seed=5))["edge_gross_bps"]


def test_verdict_gates():
    prev = lr.verdict({"X": {"days": 2.0, "edge_gross_bps": 5.0}})
    assert prev["verdict"] == "PREVIEW"
    dead = lr.verdict({"X": {"days": 30.0, "edge_gross_bps": 1.0}})
    assert dead["verdict"] == "REJECTED"
    go = lr.verdict({"X": {"days": 30.0, "edge_gross_bps": 4.5}})
    assert go["verdict"] == "PROCEED_TO_SIM"
    assert lr.verdict({})["verdict"] == "NO_DATA"
