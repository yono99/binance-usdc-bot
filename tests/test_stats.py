"""Lapisan signifikansi: bootstrap, effective-n, koreksi Bonferroni."""
import numpy as np

from bot.stats import (
    block_bootstrap_pvalue,
    bonferroni_significant,
    effective_sample_size,
    significance_report,
)


def test_pvalue_small_for_clear_edge():
    rs = list(np.random.default_rng(0).normal(0.3, 0.5, 120))   # mean jelas > 0
    assert block_bootstrap_pvalue(rs) < 0.05


def test_pvalue_large_for_noise():
    rs = list(np.random.default_rng(0).normal(0.0, 1.0, 120))   # mean ~ 0
    assert block_bootstrap_pvalue(rs) > 0.05


def test_pvalue_one_for_negative_mean():
    rs = list(np.random.default_rng(0).normal(-0.2, 0.5, 80))
    assert block_bootstrap_pvalue(rs) == 1.0


def test_effective_n_drops_with_autocorrelation():
    rng = np.random.default_rng(1)
    iid = rng.normal(0, 1, 500)
    # deret sangat berkorelasi (AR kuat) → eff_n jauh < n
    ar = np.zeros(500)
    for i in range(1, 500):
        ar[i] = 0.9 * ar[i - 1] + rng.normal(0, 1)
    assert effective_sample_size(iid) > effective_sample_size(ar)
    assert effective_sample_size(ar) < 500


def test_bonferroni_tightens_with_trials():
    assert bonferroni_significant(0.01, n_trials=1) is True
    assert bonferroni_significant(0.01, n_trials=1000) is False


def test_report_significant_flag():
    rep = significance_report(list(np.random.default_rng(3).normal(0.3, 0.5, 120)), n_trials=1)
    assert rep["significant"] is True and rep["p_adj"] < 0.05
    rep_many = significance_report(list(np.random.default_rng(3).normal(0.3, 0.5, 120)), n_trials=100000)
    assert rep_many["significant"] is False        # banyak trial → tak signifikan
