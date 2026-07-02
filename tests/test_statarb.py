"""Fase 2 / H21 — engine stat-arb pairs. Validasi: kontrol POSITIF (pasangan
kointegrasi → mean-revert → untung) & NEGATIF (random-walk independen → nihil)."""
import numpy as np

from bot import statarb as sa
from bot.xsectional import verdict


def _ou(T, phi, sd, rng):
    x = np.zeros(T)
    for t in range(1, T):
        x[t] = phi * x[t - 1] + rng.normal(0, sd)
    return x


def _coint_panel(T=2500, N=5, seed=3):
    """N seri berbagi SATU random-walk base + deviasi OU → tiap pasang kointegrasi
    (spread = selisih OU, mean-reverting). Log-harga."""
    rng = np.random.default_rng(seed)
    base = np.cumsum(rng.normal(0, 0.02, T))
    return np.column_stack([base + _ou(T, 0.9, 0.05, rng) + 4 for _ in range(N)])


def _rw_panel(T=2500, N=5, seed=9):
    rng = np.random.default_rng(seed)
    return np.column_stack([np.cumsum(rng.normal(0, 0.02, T)) + 4 for _ in range(N)])


# ---------- unit ----------

def test_hedge_ratio_recovers_beta():
    rng = np.random.default_rng(0)
    x = rng.normal(0, 1, 1000)
    y = 3.0 * x + 2.0 + rng.normal(0, 0.01, 1000)
    a, b = sa.hedge_ratio(y, x)
    assert abs(b - 3.0) < 0.05 and abs(a - 2.0) < 0.05


def test_half_life_ou_vs_randomwalk():
    rng = np.random.default_rng(1)
    hl_ou = sa.half_life(_ou(5000, 0.9, 0.05, rng))       # AR(1) φ=0.9 → hl≈6.6
    assert 3 < hl_ou < 12
    hl_rw = sa.half_life(np.cumsum(rng.normal(0, 0.02, 5000)))            # RW → tak revert
    assert hl_rw == np.inf or hl_rw > 100                 # jauh > horizon OU (filter menolaknya)


def test_select_pairs_finds_cointegrated_not_random():
    coint = sa.select_pairs(_coint_panel(), hl_max=30)
    rw = sa.select_pairs(_rw_panel(), hl_max=30)
    assert len(coint) >= 5 and len(rw) < len(coint)       # 5 seri → 10 pasang kointegrasi


# ---------- kontrol positif / negatif ----------

def test_positive_control_statarb_profits():
    logp = _coint_panel()
    grid = sa.build_grid([1.5], [30])
    _, oos = sa.walk_forward_statarb(logp, grid, cost=0.0, train_len=600, test_len=250)
    v = verdict(oos, len(grid))
    assert v["mean"] > 0 and v["ok"], v["reason"]


def test_negative_control_randomwalk_rejected():
    logp = _rw_panel(seed=17)
    grid = sa.build_grid([1.5], [30])
    _, oos = sa.walk_forward_statarb(logp, grid, cost=0.0, train_len=600, test_len=250)
    assert not verdict(oos, len(grid))["ok"]
